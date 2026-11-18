import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLE = (
    "\u200e[2/2/26, 8:45:05\u202fPM] Ann: Hello there\r\n"
    "[2/2/26, 8:46:10\u202fPM] Bob: Hi Ann, how are you?\r\n"
    "[2/2/26, 8:47:00\u202fPM] Ann: Doing well\r\n"
    "thanks for asking\r\n"
    "[2/3/26, 9:00:00\u202fAM] Bob: \u200eimage omitted\r\n"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def upload(client, name, data):
    return client.post("/upload", files={"file": (name, data)})


def test_upload_txt_and_read_back(client):
    r = upload(client, "chat.txt", SAMPLE.encode("utf-8"))
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == 4
    chat = client.get(f"/chat/{body['chat_id']}").json()
    assert chat["messages"][2]["text"] == "Doing well\nthanks for asking"
    assert chat["messages"][3]["kind"] == "media"


def test_upload_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", SAMPLE)
    r = upload(client, "export.zip", buf.getvalue())
    assert r.status_code == 200
    assert r.json()["messages"] == 4


def test_search_finds_prefix_and_is_per_chat(client):
    a = upload(client, "a.txt", SAMPLE.encode()).json()["chat_id"]
    b = upload(client, "b.txt", "[1/1/26, 1:00:00\u202fAM] Zed: unrelated words\n".encode()).json()["chat_id"]
    hits = client.get(f"/chat/{a}/search", params={"q": "thank"}).json()["results"]
    assert len(hits) == 1 and "thanks" in hits[0]["text"]
    assert client.get(f"/chat/{b}/search", params={"q": "thank"}).json()["results"] == []


def test_search_handles_punctuation(client):
    a = upload(client, "a.txt", SAMPLE.encode()).json()["chat_id"]
    r = client.get(f"/chat/{a}/search", params={"q": '"how are" AND ('})
    assert r.status_code == 200


def test_delete(client):
    a = upload(client, "a.txt", SAMPLE.encode()).json()["chat_id"]
    assert client.delete(f"/chat/{a}").status_code == 200
    assert client.get(f"/chat/{a}").status_code == 404
    assert client.get(f"/chat/{a}/search", params={"q": "hello"}).status_code == 404


def test_bad_uploads(client):
    assert upload(client, "notes.pdf", b"x").status_code == 400
    assert upload(client, "empty.txt", b"just some text").status_code == 400