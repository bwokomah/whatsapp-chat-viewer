import os
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.parser import parse_chat, read_upload

BASE_DIR = Path(__file__).parent
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", "24"))

MEDIA_ICONS = {
    "image": "📷",
    "video": "🎥",
    "audio": "🎤",
    "sticker": "🏷️",
    "gif": "🎞️",
    "document": "📄",
    "contact card": "👤",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.purge_old_chats(RETENTION_HOURS)
    yield


app = FastAPI(title="WhatsApp Chat Viewer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def media_label(text: str) -> str:
    """'sticker omitted' -> '🏷️ Sticker'"""
    word = text.lower().replace("omitted", "").strip()
    icon = MEDIA_ICONS.get(word, "📎")
    return f"{icon} {word.capitalize() or 'Media'}"


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
        text = media_label(m["text"]) if m["kind"] == "media" else m["text"]
        if m["kind"] == "deleted":
            text = "🚫 " + m["text"]
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
            }
        )
        last_sender = m["sender"]
    return days


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


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def document_context(chat_id: str, me: str | None, start: str | None, end: str | None):
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
    days = [datetime.fromisoformat(m["ts"]).date() for m in msgs]
    return {
        "chat": chat,
        "me": me,
        "header_name": others[0] if len(others) == 1 else chat["title"],
        "days": build_days(msgs, me),
        "count": sum(1 for m in msgs if m["kind"] != "system"),
        "first_day": long_date(days[0]) if days else "",
        "last_day": long_date(days[-1]) if days else "",
        "start": start_d.isoformat() if start_d else "",
        "end": end_d.isoformat() if end_d else "",
        "css": (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8"),
        "standalone": False,
    }


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"retention_hours": RETENTION_HOURS}
    )


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    db.purge_old_chats(RETENTION_HOURS)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 25 MB)")
    name = file.filename or "chat.txt"
    if not name.lower().endswith((".txt", ".zip")):
        raise HTTPException(400, "Please upload a .txt or .zip WhatsApp export")
    try:
        raw = read_upload(name, data)
    except Exception as exc:
        raise HTTPException(400, f"Could not read file: {exc}")
    messages = parse_chat(raw)
    if not messages:
        raise HTTPException(400, "No WhatsApp messages found in that file")
    title = name.rsplit(".", 1)[0]
    chat_id = db.create_chat(title, messages)
    return {"chat_id": chat_id, "messages": len(messages), "url": f"/c/{chat_id}"}


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
    ctx = document_context(chat_id, me, start, end)
    if ctx is None:
        return RedirectResponse(f"/c/{chat_id}")
    ctx["standalone"] = True  # the saved file has no server links
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