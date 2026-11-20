import base64
import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app import media as media_files
from app.parser import parse_chat, read_upload

BASE_DIR = Path(__file__).parent
MB = 1024 * 1024
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * MB
MAX_MEDIA_FILE_BYTES = int(os.environ.get("MAX_MEDIA_FILE_MB", "30")) * MB   # one picture/video
MAX_MEDIA_TOTAL_BYTES = int(os.environ.get("MAX_MEDIA_TOTAL_MB", "150")) * MB  # all media in one chat
EMBED_BUDGET_BYTES = 20 * MB  # pictures embedded in a downloaded HTML file
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", "24"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.purge_old_chats(RETENTION_HOURS)
    yield


app = FastAPI(title="WhatsApp Chat Viewer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def media_label(text: str) -> str:
    """Placeholder for an attachment we don't have: 'sticker omitted' -> '🏷️ Sticker'."""
    t = text.lower().strip()
    m = re.search(r"<attached: ([^>]+)>", t) or re.search(r"(\S+) \(file attached\)", t)
    if m:
        kind = media_files.kind_of(m.group(1)) or "file"
    else:
        word = t.replace("<", "").replace(">", "").replace("omitted", "").strip()
        kind = {"document": "file", "gif": "image"}.get(word, word)
    return media_files.KIND_LABELS.get(kind, "📎 " + (kind.capitalize() or "Media"))


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def build_days(messages: list[dict], me: str) -> list[dict]:
    """Group messages by day: [{"label": "February 2, 2026", "items": [...]}, ...]"""
    days: list[dict] = []
    current = None
    last_day = None
    last_sender = None
    for m in messages:
        ts = datetime.fromisoformat(m["ts"])
        if current is None or ts.date() != last_day:
            current = {"label": long_date(ts.date()), "items": []}
            days.append(current)
            last_day = ts.date()
            last_sender = None
        if m["kind"] == "system":
            current["items"].append({"type": "system", "idx": m["idx"], "text": m["text"]})
            last_sender = None
            continue

        media = m.get("media") or ""
        mtype = media_files.kind_of(media) if media and m["kind"] == "media" else ""
        if mtype:  # we have the actual file: show it, with its caption underneath
            text = m.get("caption", "")
        elif m["kind"] == "media":  # no file: show a labelled placeholder
            text = media_label(m["text"])
        elif m["kind"] == "deleted":
            text = "🚫 " + m["text"]
        else:
            text = m["text"]

        hour = ts.hour % 12 or 12
        current["items"].append(
            {
                "type": "msg",
                "idx": m["idx"],
                "mine": m["sender"] == me,
                "first": m["sender"] != last_sender,
                "kind": m["kind"],
                "text": text,
                "time": f"{hour}:{ts:%M} {ts:%p}",
                "media": media if mtype else "",
                "mtype": mtype,
                "label": media_files.KIND_LABELS.get(mtype, ""),
            }
        )
        last_sender = m["sender"]
    return days


def prepare_export_media(days: list[dict], chat_id: str, standalone: bool) -> None:
    """
    Printed/downloaded documents can't play audio or video, so only pictures are shown.
    On the page, pictures load from the server. In a downloaded file they are embedded
    (up to a size budget) so the file still works offline.
    """
    budget = EMBED_BUDGET_BYTES
    for day in days:
        for it in day["items"]:
            if it.get("type") != "msg" or not it["mtype"]:
                continue
            it["src"] = ""
            if it["mtype"] not in ("image", "sticker"):
                continue
            if not standalone:
                it["src"] = f"/media/{chat_id}/{it['media']}"
                continue
            path = media_files.chat_dir(chat_id) / it["media"]
            try:
                size = path.stat().st_size
                if size <= budget:
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    it["src"] = f"data:{media_files.mime_of(it['media'])};base64,{encoded}"
                    budget -= size
            except OSError:
                pass


def get_senders(messages: list[dict]) -> list[str]:
    senders: list[str] = []
    for m in messages:
        if m["kind"] != "system" and m["sender"] not in senders:
            senders.append(m["sender"])
    return senders


def parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def filter_by_date(messages: list[dict], start: date | None, end: date | None) -> list[dict]:
    out = []
    for m in messages:
        day = datetime.fromisoformat(m["ts"]).date()
        if (start and day < start) or (end and day > end):
            continue
        out.append(m)
    return out


def document_context(
    chat_id: str, me: str | None, start: str | None, end: str | None, standalone: bool = False
):
    """Everything the export page needs, or None if `me` isn't one of the senders."""
    chat = db.get_chat(chat_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    senders = get_senders(chat["messages"])
    if me not in senders:
        return None
    start_d, end_d = parse_day(start), parse_day(end)
    msgs = filter_by_date(chat["messages"], start_d, end_d)
    others = [s for s in senders if s != me]
    days_seen = [datetime.fromisoformat(m["ts"]).date() for m in msgs]
    days = build_days(msgs, me)
    prepare_export_media(days, chat_id, standalone)
    return {
        "chat": chat,
        "me": me,
        "header_name": others[0] if len(others) == 1 else chat["title"],
        "days": days,
        "count": sum(1 for m in msgs if m["kind"] != "system"),
        "first_day": long_date(days_seen[0]) if days_seen else "",
        "last_day": long_date(days_seen[-1]) if days_seen else "",
        "start": start_d.isoformat() if start_d else "",
        "end": end_d.isoformat() if end_d else "",
        "css": (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8"),
        "standalone": standalone,
    }


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"retention_hours": RETENTION_HOURS, "max_upload_mb": MAX_UPLOAD_MB},
    )


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    db.purge_old_chats(RETENTION_HOURS)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
    name = file.filename or "chat.txt"
    if not name.lower().endswith((".txt", ".zip")):
        raise HTTPException(400, "Please upload a .txt or .zip WhatsApp export")
    try:
        raw = read_upload(name, data)
    except Exception as exc:
        raise HTTPException(400, f"Could not read file: {exc}")
    messages = parse_chat(raw)
    if not messages:
        first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")[:30]
        raise HTTPException(
            400,
            "No WhatsApp messages found in that file. Its first line starts with: " + first,
        )

    chat_id = db.new_chat_id()
    wanted = {m.media for m in messages if m.media}
    saved: dict[str, str] = {}
    if wanted and name.lower().endswith(".zip"):
        saved = media_files.save_from_zip(
            data, chat_id, wanted, MAX_MEDIA_FILE_BYTES, MAX_MEDIA_TOTAL_BYTES
        )
    for m in messages:  # messages whose file we don't have fall back to a placeholder
        m.media = saved.get(m.media, "") if m.media else ""

    title = name.rsplit(".", 1)[0]
    try:
        db.create_chat(title, messages, chat_id)
    except Exception:
        media_files.remove_chat_media(chat_id)
        raise
    return {
        "chat_id": chat_id,
        "messages": len(messages),
        "media": len(saved),
        "url": f"/c/{chat_id}",
    }


@app.get("/media/{chat_id}/{name}")
def media_file(chat_id: str, name: str):
    # Only files that belong to a message in this chat are ever served
    if not db.has_media(chat_id, name):
        raise HTTPException(404, "Not found")
    path = media_files.chat_dir(chat_id) / name
    if not path.is_file():
        raise HTTPException(404, "Not found")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    kind = media_files.kind_of(name)
    if kind in ("image", "sticker", "video", "audio"):
        return FileResponse(path, media_type=media_files.mime_of(name), headers=headers)
    return FileResponse(
        path,
        media_type=media_files.mime_of(name),
        filename=name,
        content_disposition_type="attachment",
        headers=headers,
    )


@app.get("/c/{chat_id}")
def chat_page(request: Request, chat_id: str, me: str | None = None):
    chat = db.get_chat(chat_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    senders = get_senders(chat["messages"])
    if me not in senders:
        # Ask who "me" is so we know which side the green bubbles go on
        return templates.TemplateResponse(
            request, "chat.html", {"chat": chat, "senders": senders, "me": None}
        )
    others = [s for s in senders if s != me]
    header_name = others[0] if len(others) == 1 else chat["title"]
    return templates.TemplateResponse(
        request,
        "chat.html",
        {
            "chat": chat,
            "senders": senders,
            "me": me,
            "header_name": header_name,
            "initial": header_name[:1].upper(),
            "days": build_days(chat["messages"], me),
        },
    )


@app.get("/c/{chat_id}/document")
def document_page(
    request: Request, chat_id: str, me: str | None = None,
    start: str | None = None, end: str | None = None,
):
    ctx = document_context(chat_id, me, start, end)
    if ctx is None:
        return RedirectResponse(f"/c/{chat_id}")
    return templates.TemplateResponse(request, "export.html", ctx)


@app.get("/c/{chat_id}/download")
def download_document(
    request: Request, chat_id: str, me: str | None = None,
    start: str | None = None, end: str | None = None,
):
    ctx = document_context(chat_id, me, start, end, standalone=True)
    if ctx is None:
        return RedirectResponse(f"/c/{chat_id}")
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", ctx["chat"]["title"]).strip("_") or "whatsapp-chat"
    response = templates.TemplateResponse(request, "export.html", ctx)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}.html"'
    return response


@app.get("/chat/{chat_id}")
def get_chat(chat_id: str):
    chat = db.get_chat(chat_id)
    if chat is None:
        raise HTTPException(404, "Chat not found")
    return chat


@app.get("/chat/{chat_id}/search")
def search(chat_id: str, q: str = ""):
    if db.get_chat(chat_id) is None:
        raise HTTPException(404, "Chat not found")
    return {"query": q, "results": db.search_chat(chat_id, q)}


@app.delete("/chat/{chat_id}")
def delete(chat_id: str):
    if not db.delete_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    return {"deleted": True}