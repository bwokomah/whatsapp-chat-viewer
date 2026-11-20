import io
import os
import re
import shutil
import unicodedata
import zipfile
from pathlib import Path

# Only these file types are ever saved or served. Anything else (html, svg, scripts...) is ignored.
MEDIA_TYPES = {
    # pictures
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"),
    # video
    ".mp4": ("video", "video/mp4"),
    ".m4v": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".3gp": ("video", "video/3gpp"),
    ".webm": ("video", "video/webm"),
    # audio and voice notes
    ".opus": ("audio", "audio/ogg"),
    ".ogg": ("audio", "audio/ogg"),
    ".m4a": ("audio", "audio/mp4"),
    ".mp3": ("audio", "audio/mpeg"),
    ".aac": ("audio", "audio/aac"),
    ".wav": ("audio", "audio/wav"),
    ".amr": ("audio", "audio/amr"),
    # documents (offered as downloads)
    ".pdf": ("file", "application/pdf"),
    ".docx": ("file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": ("file", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": ("file", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".txt": ("file", "text/plain"),
    ".csv": ("file", "text/csv"),
    ".vcf": ("file", "text/vcard"),
    ".heic": ("file", "image/heic"),  # most browsers can't show HEIC, so offer a download
}

KIND_LABELS = {
    "image": "📷 Image",
    "video": "🎥 Video",
    "audio": "🎤 Audio",
    "sticker": "🏷️ Sticker",
    "file": "📄 Document",
}


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_DIR", "media"))


def chat_dir(chat_id: str) -> Path:
    return media_root() / chat_id


def remove_chat_media(chat_id: str) -> None:
    shutil.rmtree(chat_dir(chat_id), ignore_errors=True)


def safe_name(name: str) -> str:
    """A file name that is safe to store: no folders, no odd characters."""
    base = Path(name.replace("\\", "/")).name
    base = unicodedata.normalize("NFC", base)
    base = re.sub(r"[^\w.\- ()]", "_", base).strip(" .")
    return base[:150] or "file"


def kind_of(name: str) -> str:
    """'image', 'sticker', 'video', 'audio', 'file', or '' if the type isn't supported."""
    entry = MEDIA_TYPES.get(Path(name).suffix.lower())
    if not entry:
        return ""
    if Path(name).suffix.lower() == ".webp":
        upper = name.upper()
        return "sticker" if ("STICKER" in upper or upper.startswith("STK-")) else "image"
    return entry[0]


def mime_of(name: str) -> str:
    entry = MEDIA_TYPES.get(Path(name).suffix.lower())
    return entry[1] if entry else "application/octet-stream"


class _TooBig(Exception):
    pass


def save_from_zip(
    data: bytes, chat_id: str, wanted: set[str], max_file_bytes: int, max_total_bytes: int
) -> dict[str, str]:
    """
    Copy the files named in `wanted` out of the zip into this chat's media folder.
    Returns {name used in the chat: name it was saved as}. Anything unsafe, unsupported
    or too big is skipped, and the message just shows a placeholder instead.
    """
    saved: dict[str, str] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return saved

    # Look files up by their bare name, ignoring folders, accents form and letter case
    index = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        base = Path(info.filename.replace("\\", "/")).name
        index.setdefault(unicodedata.normalize("NFC", base).lower(), info)

    dest = chat_dir(chat_id)
    total = 0
    for want in sorted(wanted):
        info = index.get(unicodedata.normalize("NFC", Path(want).name).lower())
        stored = safe_name(want)
        if info is None or not kind_of(stored) or info.file_size > max_file_bytes:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / stored
        written = 0
        try:
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(65536):
                    written += len(chunk)
                    if written > max_file_bytes or total + written > max_total_bytes:
                        raise _TooBig
                    out.write(chunk)
        except (_TooBig, zipfile.BadZipFile, OSError):
            target.unlink(missing_ok=True)
            continue
        total += written
        saved[want] = stored
    return saved