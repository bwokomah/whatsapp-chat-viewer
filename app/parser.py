import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Matches: [2/2/26, 8:45:05 PM] Sender: text
# - \u200e is an invisible left-to-right mark WhatsApp adds to some lines
# - \u202f is a narrow no-break space before AM/PM (iPhone exports)
LINE_RE = re.compile(
    r"^\u200e?\[(\d{1,2})/(\d{1,2})/(\d{2,4}),\s(\d{1,2}):(\d{2}):(\d{2})[\u202f\u00a0 ]?([AaPp][Mm])\]\s([^:]+?):[ ]?(.*)$"
)

SYSTEM_PHRASES = (
    "end-to-end encrypted",
    "disappearing messages",
    "security code changed",
    "changed their phone number",
    "you blocked this contact",
    "you unblocked this contact",
)

MEDIA_WORDS = ("image", "video", "audio", "sticker", "GIF", "document", "Contact card")


@dataclass
class Message:
    id: int
    timestamp: datetime
    sender: str
    text: str
    kind: str = "text"  # text | media | deleted | system


def _clean(text: str) -> str:
    return text.replace("\u200e", "").replace("\u200f", "").strip()


def _kind(text: str) -> str:
    t = text.lower()
    if t.endswith("omitted") and any(w.lower() in t for w in MEDIA_WORDS):
        return "media"
    if "this message was deleted" in t or "you deleted this message" in t:
        return "deleted"
    if any(p in t for p in SYSTEM_PHRASES):
        return "system"
    return "text"


def parse_chat(raw: str) -> list[Message]:
    messages: list[Message] = []
    current: Optional[Message] = None

    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = LINE_RE.match(line)
        if m:
            month, day, year, hour, minute, second, ampm, sender, text = m.groups()
            year = int(year)
            if year < 100:
                year += 2000
            ts = datetime.strptime(
                f"{month}/{day}/{year} {hour}:{minute}:{second} {ampm.upper()}",
                "%m/%d/%Y %I:%M:%S %p",
            )
            text = _clean(text)
            current = Message(len(messages), ts, sender.strip(), text, _kind(text))
            messages.append(current)
        elif current is not None:
            # continuation of a multi-line message
            current.text += "\n" + _clean(line)
    return messages


def read_upload(filename: str, data: bytes) -> str:
    """Return the chat text from either a .txt or a .zip upload."""
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not names:
                raise ValueError("No .txt chat file found inside the zip")
            return zf.read(names[0]).decode("utf-8-sig")
    return data.decode("utf-8-sig")