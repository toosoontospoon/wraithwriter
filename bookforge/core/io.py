"""Small IO helpers: atomic writes and stable hashing."""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, data) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    txt = path.read_text(encoding="utf-8").strip()
    return json.loads(txt) if txt else default


def stable_hash(obj) -> str:
    if not isinstance(obj, (str, bytes)):
        obj = json.dumps(obj, sort_keys=True, default=str)
    if isinstance(obj, str):
        obj = obj.encode("utf-8")
    return hashlib.sha1(obj).hexdigest()[:16]
