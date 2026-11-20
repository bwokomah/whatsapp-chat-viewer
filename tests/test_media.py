import base64
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app import media as media_files
from app.main import app
from app.parser import parse_chat

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

IOS_CHAT = (
    "\u200e[2/2/26, 8:45:05\u202fPM] Ann: \u200e<attached: 00000001-PHOTO-2026-02-02-20-45-05.jpg>\n"
    "[2/2/26, 8:46:10\u202fPM] Bob: \u200e<attached: 00000002-AUDIO-2026-02-02-20-46-10.opus>\n"
    "[2/2/26, 8:47:00\u202fPM] Ann: \u200e<attached: 00000003-VIDEO-2026-02-02-20-47-00.mp4>\n"
    "[2/2/26, 8:48:00\u202fPM] Bob: \u200e<attached: 00000004-STICKER-2026-02-02-20-48-00.webp>\n"
    "[2/2/26, 8:49:00\u202fPM] Ann: \u200e<attached: 00000005-lunch menu.pdf>\n"
    "[2/2/26, 8:50:00\u202fPM] Bob: normal text\n"
)
ANDROID_CHAT = (
    "2/2/26, 8:45 PM - Ann: IMG-20260202-WA0001.jpg (file attached)\n"
    "Sunset at the lake\n"
    "2/2/26, 8:46 PM - Bob: hello\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    with TestClient(app) as c:
        yield c


def make_zip(chat_text: str, files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", chat_text)
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def upload(client, data, name="chat.zip"):
    return client.post("/upload", files={"file": (name, data)})


IOS_FILES = {
    "00000001-PHOTO-2026-02-02-20-45-05.jpg": PNG,
    "00000002-AUDIO-2026-02-02-20-46-10.opus": b"OggS-fake-audio",
    "00000003-VIDEO-2026-02-02-20-47-00.mp4": b"fake-video-bytes",
    "00000004-STICKER-2026-02-02-20-48-00.webp": b"RIFFfake",
    "00000005-lunch menu.pdf": b"%PDF-fake",
}


# ---------- parser ----------
def test_parser_extracts_ios_and_android_attachments():
    ios = parse_chat(IOS_CHAT)
    assert ios[0].media == "00000001-PHOTO-2026-02-02-20-45-05.jpg" and ios[0].caption == ""
    assert ios[4].media == "00000005-lunch menu.pdf"
    android = parse_chat(ANDROID_CHAT)
    assert android[0].media == "IMG-20260202-WA0001.jpg"
    assert android[0].caption == "Sunset at the lake"
    assert android[1].media == ""


# ---------- helpers ----------
def test_kinds_and_safe_names():
    assert media_files.kind_of("a.jpg") == "image"
    assert media_files.kind_of("00000004-STICKER-x.webp") == "sticker"
    assert media_files.kind_of("STK-2026.webp") == "sticker"
    assert media_files.kind_of("photo.webp") == "image"
    assert media_files.kind_of("x.opus") == "audio"
    assert media_files.kind_of("x.html") == "" and media_files.kind_of("x.svg") == ""
    assert media_files.safe_name("../../etc/passwd.jpg") == "passwd.jpg"
    assert media_files.safe_name("a<b>c.jpg") == "a_b_c.jpg"


# ---------- upload and serving ----------
def test_zip_with_media_is_saved_and_served(client):
    r = upload(client, make_zip(IOS_CHAT, IOS_FILES))
    assert r.status_code == 200 and r.json()["media"] == 5
    cid = r.json()["chat_id"]
    img = client.get(f"/media/{cid}/00000001-PHOTO-2026-02-02-20-45-05.jpg")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert img.content == PNG
    assert client.get(f"/media/{cid}/00000002-AUDIO-2026-02-02-20-46-10.opus").headers["content-type"] == "audio/ogg"
    pdf = client.get(f"/media/{cid}/00000005-lunch menu.pdf")
    assert pdf.status_code == 200 and "attachment" in pdf.headers["content-disposition"]


def test_chat_page_shows_each_media_type(client):
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    html = client.get(f"/c/{cid}", params={"me": "Ann"}).text
    assert 'class="media-img"' in html
    assert "<audio" in html and "<video" in html
    assert 'class="media-sticker"' in html
    assert "media-file" in html and "lunch menu.pdf" in html
    assert f"/media/{cid}/00000005-lunch%20menu.pdf" in html


def test_android_caption_is_shown_and_searchable(client):
    cid = upload(client, make_zip(ANDROID_CHAT, {"IMG-20260202-WA0001.jpg": PNG})).json()["chat_id"]
    html = client.get(f"/c/{cid}", params={"me": "Ann"}).text
    assert "Sunset at the lake" in html and 'class="media-img"' in html
    hits = client.get(f"/chat/{cid}/search", params={"q": "sunset"}).json()["results"]
    assert len(hits) == 1


def test_missing_file_falls_back_to_placeholder(client):
    cid = upload(client, make_zip(IOS_CHAT, {})).json()["chat_id"]
    html = client.get(f"/c/{cid}", params={"me": "Ann"}).text
    assert 'class="media-img"' not in html and "<audio" not in html
    assert "📷 Image" in html and "🎤 Audio" in html and "🎥 Video" in html


def test_plain_txt_with_attachment_lines_shows_placeholders(client):
    cid = upload(client, IOS_CHAT.encode(), "chat.txt").json()["chat_id"]
    html = client.get(f"/c/{cid}", params={"me": "Ann"}).text
    assert "📷 Image" in html and "📄 Document" in html


# ---------- safety ----------
def test_unsupported_types_are_never_saved(client):
    chat = "[2/2/26, 8:45:05\u202fPM] Ann: \u200e<attached: evil.html>\n"
    cid = upload(client, make_zip(chat, {"evil.html": b"<script>alert(1)</script>"})).json()["chat_id"]
    assert client.get(f"/media/{cid}/evil.html").status_code == 404


def test_zip_slip_and_path_tricks(client, tmp_path):
    chat = "[2/2/26, 8:45:05\u202fPM] Ann: \u200e<attached: ../../escape.jpg>\n"
    cid = upload(client, make_zip(chat, {"../../escape.jpg": PNG})).json()["chat_id"]
    assert not (tmp_path / "escape.jpg").exists()
    assert not (tmp_path.parent / "escape.jpg").exists()
    saved = list((tmp_path / "media" / cid).iterdir())
    assert [p.name for p in saved] == ["escape.jpg"]  # stayed inside this chat's folder
    assert client.get(f"/media/{cid}/..%2F..%2Fescape.jpg").status_code == 404
    assert client.get(f"/media/{cid}/%2e%2e%2fchats.db").status_code == 404


def test_media_is_private_to_its_chat(client):
    a = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    b = upload(client, make_zip(ANDROID_CHAT, {"IMG-20260202-WA0001.jpg": PNG})).json()["chat_id"]
    assert client.get(f"/media/{b}/00000001-PHOTO-2026-02-02-20-45-05.jpg").status_code == 404
    assert client.get(f"/media/{a}/IMG-20260202-WA0001.jpg").status_code == 404


def test_size_limits_skip_big_files(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_MEDIA_FILE_BYTES", 10)
    r = upload(client, make_zip(IOS_CHAT, {"00000001-PHOTO-2026-02-02-20-45-05.jpg": PNG}))
    assert r.json()["media"] == 0


def test_total_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_MEDIA_TOTAL_BYTES", len(PNG) + 5)
    files = {
        "00000001-PHOTO-2026-02-02-20-45-05.jpg": PNG,
        "00000003-VIDEO-2026-02-02-20-47-00.mp4": b"x" * 50,
    }
    assert upload(client, make_zip(IOS_CHAT, files)).json()["media"] == 1


def test_range_requests_work_for_video_seeking(client):
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    r = client.get(f"/media/{cid}/00000003-VIDEO-2026-02-02-20-47-00.mp4", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206 and r.content == b"fake"


# ---------- cleanup ----------
def test_delete_removes_files(client, tmp_path):
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    assert (tmp_path / "media" / cid).is_dir()
    client.delete(f"/chat/{cid}")
    assert not (tmp_path / "media" / cid).exists()
    assert client.get(f"/media/{cid}/00000001-PHOTO-2026-02-02-20-45-05.jpg").status_code == 404


def test_purge_removes_files(client, tmp_path):
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE chats SET created_at = ? WHERE id = ?", (old, cid))
    assert db.purge_old_chats(24) == 1
    assert not (tmp_path / "media" / cid).exists()


# ---------- export ----------
def test_export_page_and_download(client):
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    page = client.get(f"/c/{cid}/document", params={"me": "Ann"}).text
    assert f'src="/media/{cid}/00000001-PHOTO' in page
    assert "🎤 Audio" in page and "🎥 Video" in page and "<audio" not in page
    dl = client.get(f"/c/{cid}/download", params={"me": "Ann"}).text
    assert "data:image/jpeg;base64," in dl and "/media/" not in dl


def test_download_respects_embed_budget(client, monkeypatch):
    monkeypatch.setattr(main, "EMBED_BUDGET_BYTES", 1)
    cid = upload(client, make_zip(IOS_CHAT, IOS_FILES)).json()["chat_id"]
    dl = client.get(f"/c/{cid}/download", params={"me": "Ann"}).text
    assert "data:image" not in dl and "📷 Image" in dl


# ---------- database upgrade ----------
def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (chat_id TEXT, idx INTEGER, ts TEXT, sender TEXT, text TEXT, kind TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(path))
    db.init_db()
    with db.get_conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)")}
    assert {"media", "caption"} <= cols