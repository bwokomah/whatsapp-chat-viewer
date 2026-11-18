import collections
from pathlib import Path

from app.parser import parse_chat, read_upload

for path in sorted(Path("samples").iterdir()):
    if path.suffix.lower() not in (".txt", ".zip"):
        continue
    msgs = parse_chat(read_upload(path.name, path.read_bytes()))
    print(path.name, len(msgs), msgs[0].timestamp, msgs[-1].timestamp)
    print(collections.Counter(m.kind for m in msgs))
    print(collections.Counter(m.sender for m in msgs))
    print()