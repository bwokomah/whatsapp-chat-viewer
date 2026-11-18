import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLE = (
    "\u200e[2/2/26, 8:45:05\u202fPM] Ann: First day message\r\n"
    "[2/2/26, 8:46:10\u202fPM] Bob: Hi <b>Ann</b>\r\n"
    "[3/5/26, 9:00:00\u202fAM] Ann: Much later message\r\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def chat_id(client):
    r = client.post("/upload", files={"file": ("My Chat.txt", SAMPLE.encode())})
    return r.json()["chat_id"]


def test_document_page(client, chat_id):
    r = client.get(f"/c/{chat_id}/document", params={"me": "Bob"})
    assert r.status_code == 200
    assert "Chat with Ann" in r.text
    assert "3 messages" in r.text
    assert "Print / Save as PDF" in r.text
    assert "&lt;b&gt;Ann&lt;/b&gt;" in r.text and "<b>Ann</b>" not in r.text


def test_date_filter(client, chat_id):
    r = client.get(f"/c/{chat_id}/document", params={"me": "Bob", "start": "2026-03-01"})
    assert "Much later message" in r.text
    assert "First day message" not in r.text
    assert "1 message<" in r.text


def test_empty_range(client, chat_id):
    r = client.get(f"/c/{chat_id}/document", params={"me": "Bob", "start": "2030-01-01"})
    assert "No messages in this date range." in r.text


def test_download_is_standalone_attachment(client, chat_id):
    r = client.get(f"/c/{chat_id}/download", params={"me": "Bob"})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="My_Chat.html"'
    assert "<style>" in r.text and "bubble" in r.text
    assert "Back to chat" not in r.text and "Download HTML" not in r.text


def test_bad_me_redirects_to_picker(client, chat_id):
    r = client.get(f"/c/{chat_id}/document", params={"me": "Nobody"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == f"/c/{chat_id}"


def test_unknown_chat_404(client):
    assert client.get("/c/nope/document", params={"me": "x"}).status_code == 404