import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# --- line formats -----------------------------------------------------------
# \u200e = invisible left-to-right mark, \u202f = narrow no-break space (iPhone)
_DATE = r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})"
_TIME = r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?[\u202f\u00a0 ]*([AaPp]\.?[ \u202f\u00a0]?[Mm]\.?)?"

# iPhone:  [2/2/26, 8:45:05 PM] Name: text      (also 24-hour: [02/02/2026, 20:45:05])
IOS_RE = re.compile(rf"^\u200e?\[{_DATE},?\s{_TIME}\]\s?(.*)$")
# Android: 2/2/26, 8:45 PM - Name: text         (also 24-hour: 02/02/2026, 20:45 - Name: text)
ANDROID_RE = re.compile(rf"^\u200e?{_DATE},?\s{_TIME}\s[-\u2013\u2014]\s(.*)$")
SENDER_RE = re.compile(r"^([^:]+?):[ ]?(.*)$")

ATTACHED_RE = re.compile(r"<attached:\s*([^>]+?)\s*>")           # iPhone
FILE_ATTACHED_RE = re.compile(r"^(.+?)\s+\(file attached\)", re.M)  # Android

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
    media: str = ""     # file name of an attached picture/video/audio/document, if any
    caption: str = ""   # text sent together with the attachment


def _clean(text: str) -> str:
    return text.replace("\u200e", "").replace("\u200f", "").strip()


def _kind(text: str) -> str:
    t = text.lower()
    if t.endswith("omitted") and any(w.lower() in t for w in MEDIA_WORDS):
        return "media"
    if t == "<media omitted>" or "<attached:" in t or "(file attached)" in t:
        return "media"
    if "this message was deleted" in t or "you deleted this message" in t:
        return "deleted"
    if any(p in t for p in SYSTEM_PHRASES):
        return "system"
    return "text"


def _extract_media(text: str) -> tuple[str, str]:
    """Return (file name, caption) for a message that has an attachment."""
    for pattern in (ATTACHED_RE, FILE_ATTACHED_RE):
        m = pattern.search(text)
        if m:
            caption = (text[: m.start()] + text[m.end():]).strip()
            return m.group(1).strip(), caption
    return "", ""

def _detect_order(date_parts: list[tuple[str, str, str]]) -> str:
    """Work out whether dates are day-first ('dmy') or month-first ('mdy')."""
    firsts, seconds = [], []
    for a, b, c in date_parts:
        if len(a) == 4:  # year-first (2026-02-02): order is irrelevant
            return "ymd"
        firsts.append(int(a))
        seconds.append(int(b))
    if any(a > 12 for a in firsts):
        return "dmy"
    if any(b > 12 for b in seconds):
        return "mdy"
    # Ambiguous: the month changes slowly, the day jumps around. The field that
    # goes backwards less often is the month.
    dec_a = sum(1 for x, y in zip(firsts, firsts[1:]) if y < x)
    dec_b = sum(1 for x, y in zip(seconds, seconds[1:]) if y < x)
    return "mdy" if dec_a < dec_b else "dmy"


def _to_datetime(parts: tuple, order: str) -> Optional[datetime]:
    a, b, c, hour, minute, second, ampm = parts
    try:
        if order == "ymd":
            year, month, day = int(a), int(b), int(c)
        else:
            year = int(c)
            month, day = (int(a), int(b)) if order == "mdy" else (int(b), int(a))
        if year < 100:
            year += 2000
        h = int(hour)
        if ampm:
            pm = ampm.replace(".", "").replace(" ", "").replace("\u202f", "").replace("\u00a0", "").upper() == "PM"
            h = h % 12 + (12 if pm else 0)
        return datetime(year, month, day, h, int(minute), int(second or 0))
    except ValueError:
        return None


def parse_chat(raw: str) -> list[Message]:
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Pass 1: find lines that start a message
    starts: list[Optional[tuple]] = []
    for line in lines:
        m = IOS_RE.match(line) or ANDROID_RE.match(line)
        starts.append((m.groups()[:7], m.group(8)) if m else None)

    order = _detect_order([s[0][:3] for s in starts if s])

    # Pass 2: build messages; lines that don't start a message continue the previous one
    messages: list[Message] = []
    current: Optional[Message] = None
    for line, start in zip(lines, starts):
        ts = _to_datetime(start[0], order) if start else None
        if start and ts:
            rest = start[1]
            sm = SENDER_RE.match(rest)
            if sm:
                sender, text = sm.group(1).strip(), _clean(sm.group(2))
                kind = _kind(text)
            else:  # Android group/system lines have no "Name:" part
                sender, text, kind = "", _clean(rest), "system"
            current = Message(len(messages), ts, sender, text, kind)
            messages.append(current)
        elif current is not None:
            current.text += "\n" + _clean(line)
    for m in messages:
        m.text = m.text.rstrip()  # drop blank lines left at the end of a message
        if m.kind == "media":
            m.media, m.caption = _extract_media(m.text)
    return messages


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_upload(filename: str, data: bytes) -> str:
    """Return the chat text from either a .txt or a .zip upload."""
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [
                i for i in zf.infolist()
                if i.filename.lower().endswith(".txt") and not i.filename.startswith("__MACOSX")
            ]
            if not infos:
                raise ValueError("No .txt chat file found inside the zip")
            biggest = max(infos, key=lambda i: i.file_size)
            return _decode(zf.read(biggest))
    return _decode(data)