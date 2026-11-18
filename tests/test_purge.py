from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

SAMPLE = "[2/2/26, 8:45:05\u202fPM] Ann: Hello\n[2/2/26, 8:46:10\u202fPM] Bob: Hi\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def upload(client):
    return client.post("/upload", files={"file": ("c.txt", SAMPLE.encode())}).json()["chat_id"]


def age_chat(chat_id, hours):
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE chats SET created_at = ? WHERE id = ?", (old, chat_id))


def test_old_chats_are_purged_and_new_ones_kept(client):
    old_id, new_id = upload(client), upload(client)
    age_chat(old_id, 25)
    assert db.purge_old_chats(24) == 1
    assert client.get(f"/chat/{old_id}").status_code == 404
    assert client.get(f"/chat/{new_id}").status_code == 200


def test_search_index_is_purged_too(client):
    old_id = upload(client)
    age_chat(old_id, 48)
    db.purge_old_chats(24)
    with db.get_conn() as conn:
        left = conn.execute("SELECT COUNT(*) FROM messages_fts WHERE chat_id = ?", (old_id,)).fetchone()[0]
    assert left == 0


def test_upload_triggers_purge(client):
    old_id = upload(client)
    age_chat(old_id, 30)
    upload(client)  # a new upload cleans up expired chats
    assert client.get(f"/chat/{old_id}").status_code == 404


def test_home_shows_retention_notice(client):
    assert "deleted automatically after 24 hours" in client.get("/").text