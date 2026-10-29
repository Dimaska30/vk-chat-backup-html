"""Local manual media registry, independent of generated RawData trees."""
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

MEDIA_TYPES = {"photo", "sticker", "doc", "video", "short_video", "audio", "audio_message", "video_message"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mp3", ".ogg", ".opus", ".m4a", ".wav", ".pdf", ".doc", ".docx", ".txt", ".zip"}
MAX_BYTES = 1024 * 1024 * 1024


def fingerprint(attachment):
    return hashlib.sha256(json.dumps(attachment, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def connect(dialog_dir):
    directory = Path(dialog_dir) / "ManualMedia"
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "index.sqlite3", timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS media (cid INTEGER, path TEXT, fingerprint TEXT, filename TEXT, PRIMARY KEY (cid, path))")
    return db


def lookup(dialog_dir, cid, path, attachment):
    directory = Path(dialog_dir) / "ManualMedia"
    if not (directory / "index.sqlite3").is_file():
        return None
    with connect(dialog_dir) as db:
        row = db.execute("SELECT fingerprint, filename FROM media WHERE cid=? AND path=?", (cid, path)).fetchone()
    if not row:
        return None
    if row[0] != fingerprint(attachment):
        return {"state": "source_changed"}
    file = (directory / row[1]).resolve()
    if file.parent != directory.resolve() or not file.is_file():
        return {"state": "manual_file_missing"}
    return {"state": "manual_restored", "file": file}


def store(dialog_dir, cid, path, attachment, filename, stream):
    if attachment.get("type") not in MEDIA_TYPES:
        raise ValueError("Этот тип вложения пока не поддерживает ручное восстановление")
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise ValueError("Неподдерживаемое расширение файла")
    directory = Path(dialog_dir) / "ManualMedia"
    db = connect(dialog_dir)
    file = directory / (uuid4().hex + ext)
    try:
        total = 0
        with file.open("xb") as target:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("Файл больше 1 ГБ")
                target.write(chunk)
        if not total:
            raise ValueError("Файл пуст")
        with db:
            db.execute("INSERT OR REPLACE INTO media VALUES (?, ?, ?, ?)", (cid, path, fingerprint(attachment), file.name))
    except BaseException:
        file.unlink(missing_ok=True)
        raise
    finally:
        db.close()
    return file


def update_markdown(dialog_dir, cid, path, file):
    """Update a separate generated block, preserving user edits to existing Markdown."""
    import os
    import re
    directory = Path(dialog_dir) / "MdFiles"
    files = sorted(f for f in directory.rglob("*.md") if (match := re.search(r"(?:^|\.Id)(\d+)(?:_part_\d+)?\.md$", f.name)) and int(match.group(1)) == cid)
    if not files:
        return False
    target = files[0]
    key = hashlib.sha256(path.encode()).hexdigest()
    start, end = f"<!-- manual-media:{key} -->", f"<!-- /manual-media:{key} -->"
    relative = os.path.relpath(file, target.parent).replace("\\", "/")
    block = f"{start}\n### Вложение добавлено вручную\n\nПуть вложения: `{path}`\n\n" + render_media(relative) + f"\n{end}"
    text = target.read_text(encoding="utf-8")
    if start in text:
        text = re.sub(re.escape(start) + r"[\s\S]*?" + re.escape(end), lambda match: block, text)
    else:
        text += "\n" + block + "\n"
    temp = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return True


def render_media(path):
    from html import escape
    url = escape(path, quote=True)
    ext = Path(path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return f'<img src="{url}" width="400" alt="Добавлено вручную">'
    if ext in {".mp4", ".webm", ".mov"}:
        return f'<video src="{url}" controls preload="none"></video>'
    if ext in {".mp3", ".ogg", ".opus", ".m4a", ".wav"}:
        return f'<audio src="{url}" controls preload="none"></audio>'
    return f'<a href="{url}">Скачать добавленный файл</a>'
