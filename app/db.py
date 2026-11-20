import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app import media as media_files
from app.parser import Message


def _db_path() -> str:
    return os.environ.get("DB_PATH", "chats.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                chat_id TEXT NOT NULL,
                idx     INTEGER NOT NULL,
                ts      TEXT NOT NULL,
                sender  TEXT NOT NULL,
                text    TEXT NOT NULL,
                kind    TEXT NOT NULL,
                media   TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (chat_id, idx)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text,
                chat_id UNINDEXED,
                idx UNINDEXED
            );
            """
        )
        # Databases created before media support need the two new columns
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
        for column in ("media", "caption"):
            if column not in existing:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def new_chat_id() -> str:
    return secrets.token_urlsafe(8)


def create_chat(title: str, messages: list[Message], chat_id: str | None = None) -> str:
    chat_id = chat_id or new_chat_id()
    now = datetime.now(timezone.utc).isoformat()
    # Text messages are searchable, and so are the captions of pictures/videos
    searchable = [
        (m.text if m.kind == "text" else m.caption, chat_id, m.id)
        for m in messages
        if (m.kind == "text" and m.text) or (m.kind == "media" and m.media and m.caption)
    ]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chats (id, title, created_at) VALUES (?, ?, ?)",
            (chat_id, title, now),
        )
        conn.executemany(
            "INSERT INTO messages (chat_id, idx, ts, sender, text, kind, media, caption) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (chat_id, m.id, m.timestamp.isoformat(), m.sender, m.text, m.kind, m.media, m.caption)
                for m in messages
            ],
        )
        conn.executemany(
            "INSERT INTO messages_fts (text, chat_id, idx) VALUES (?, ?, ?)", searchable
        )
    return chat_id


def get_chat(chat_id: str) -> dict | None:
    with get_conn() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if chat is None:
            return None
        rows = conn.execute(
            "SELECT idx, ts, sender, text, kind, media, caption "
            "FROM messages WHERE chat_id = ? ORDER BY idx",
            (chat_id,),
        ).fetchall()
    return {
        "id": chat["id"],
        "title": chat["title"],
        "messages": [dict(r) for r in rows],
    }


def has_media(chat_id: str, name: str) -> bool:
    """True only if this exact file belongs to a message in this chat."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE chat_id = ? AND media = ? LIMIT 1", (chat_id, name)
        ).fetchone()
    return row is not None


def _fts_query(q: str) -> str:
    """Turn user input into a safe FTS5 query: every word quoted, last one a prefix."""
    words = re.findall(r"\w+", q)
    if not words:
        return ""
    parts = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*']
    return " ".join(parts)


def search_chat(chat_id: str, q: str, limit: int = 200) -> list[dict]:
    fts = _fts_query(q)
    if not fts:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT m.idx, m.ts, m.sender, m.text
            FROM messages_fts f
            JOIN messages m ON m.chat_id = f.chat_id AND m.idx = f.idx
            WHERE messages_fts MATCH ? AND f.chat_id = ?
            ORDER BY m.idx
            LIMIT ?
            """,
            (fts, chat_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_chat(chat_id: str) -> bool:
    with get_conn() as conn:
        conn.execute("DELETE FROM messages_fts WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        deleted = cur.rowcount > 0
    media_files.remove_chat_media(chat_id)
    return deleted


def purge_old_chats(max_age_hours: int = 24) -> int:
    """Delete every chat older than max_age_hours. Returns how many were removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    with get_conn() as conn:
        rows = conn.execute("SELECT id, created_at FROM chats").fetchall()
    old_ids = [r["id"] for r in rows if datetime.fromisoformat(r["created_at"]) < cutoff]
    for chat_id in old_ids:
        delete_chat(chat_id)
    return len(old_ids)