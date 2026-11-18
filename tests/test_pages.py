import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLE = (
    "\u200e[2/2/26, 8:45:05\u202fPM] Ann: \u200eMessages and calls are end-to-end encrypted.\r\n"
    "[2/2/26, 8:46:10\u202fPM] Bob: Hi <b>Ann</b>, how are you?\r\n"
    "[2/3/26, 9:00:00\u202fAM] Ann: \u200esticker omitted\r\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def make_chat(client):
    r = client.post("/upload", files={"file": ("chat.txt", SAMPLE.encode())})
    return r.json()["chat_id"]


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "Upload" in r.text


def test_picker_shown_without_me(client):
    cid = make_chat(client)
    r = client.get(f"/c/{cid}")
    assert "Which one are you?" in r.text
    assert "me=Ann" in r.text and "me=Bob" in r.text


def test_chat_page_renders_bubbles(client):
    cid = make_chat(client)
    r = client.get(f"/c/{cid}", params={"me": "Bob"})
    assert r.status_code == 200
    assert "row mine" in r.text and "row theirs" in r.text
    assert "February 2, 2026" in r.text and "8:46 PM" in r.text
    assert "system-pill" in r.text          # the encryption notice
    assert "Sticker" in r.text              # media placeholder


def test_message_html_is_escaped(client):
    cid = make_chat(client)
    r = client.get(f"/c/{cid}", params={"me": "Bob"})
    assert "<b>Ann</b>" not in r.text
    assert "&lt;b&gt;Ann&lt;/b&gt;" in r.text


def test_unknown_chat_404(client):
    assert client.get("/c/nope", params={"me": "x"}).status_code == 404