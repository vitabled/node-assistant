"""Двухфазная отправка вложений чата: файл едет ОТДЕЛЬНЫМ запросом.

Симптом, ради которого модуль появился: архив на 50 МБ уезжал в base64 внутри
тела `POST /api/ai/chat` (~67 МБ JSON). У клиента за VPN такой запрос рвался на
середине (`nginx: SSL_read() failed: bad record mac`), ответ не начинался
вообще, а человек полчаса смотрел на «Отправка…» без единого признака прогресса.

Решение — разделить фазы:

* `POST /api/ai/chat/upload` принимает ОДИН файл multipart'ом. Браузер умеет
  показывать прогресс отправки только для XHR (`upload.onprogress`), и здесь
  это работает: тело запроса — сам файл, а не JSON с base64 внутри.
* `POST /api/ai/chat` уходит ЛЁГКИМ телом: вместо мегабайт — `upload_ids`.
  Обрыв на нём больше не стоит пользователю всей загрузки.

Почему на диск, а не в память (в отличие от `ai_attachments`): здесь файл живёт
между ДВУМЯ запросами, а gateway-процесс между ними может и перезапуститься —
тогда «Загрузка 100%» заканчивалась бы отказом «файл не найден». Плюс 50 МБ на
вкладку в памяти процесса — это способ уронить бэкенд пятью вкладками.

Границы: потолок на файл, TTL 24 часа, потолок числа файлов на аккаунт. Без них
каталог растёт вечно: чат — не файловое хранилище, и оставленные загрузки
никто никогда не удалит руками.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from app.services import accounts, ai_archives

#: Потолок на ОДИН файл. Тот же, что у фронта (`MAX_ARCHIVE_BYTES` в AiChat).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

#: Сколько живёт незабранная загрузка. Сутки — чтобы пережить «приложил, ушёл,
#: вернулся завтра дописать вопрос», но не превратиться в архив на годы.
TTL_SECONDS = 24 * 60 * 60

#: Сколько загрузок держим на аккаунт. Дальше вытесняем самые старые: пять
#: файлов — потолок вложений одного сообщения (`ai_agent.MAX_ATTACHMENTS`),
#: двадцать даёт запас на несколько разговоров.
MAX_FILES_PER_ACCOUNT = 20

#: Картиночные mime — их фронт тоже умеет прикладывать.
_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class UploadError(ValueError):
    """Файл не приняли. Текст показывается человеку дословно."""


def _dir(account_id: Optional[str]) -> Path:
    aid = account_id or accounts.current_account.get()
    if not aid:
        raise RuntimeError("No active account in context")
    return accounts.data_dir(aid) / "ai_uploads"


def _is_text(name: str, mime: str) -> bool:
    low = (name or "").lower()
    if (mime or "").startswith("text/"):
        return True
    if mime in ("application/json", "application/xml", "application/x-yaml",
                "application/yaml", "application/x-sh"):
        return True
    stem = low.rsplit("/", 1)[-1]
    if stem in ai_archives.TEXT_NAMES:
        return True
    return any(stem.endswith(ext) for ext in ai_archives.TEXT_EXT)


def is_allowed(name: str, mime: str) -> bool:
    """Тот же набор, что разрешает фронт в `readFile`: архивы, текст, картинки.

    Белый список, а не «примем что угодно»: каталог загрузок — это диск
    пользователя, и превращать чат в приёмник произвольных бинарей незачем.
    """
    if ai_archives.is_archive(name or "", mime or ""):
        return True
    if (mime or "").lower() in _IMAGE_MIME:
        return True
    return _is_text(name or "", (mime or "").lower())


def _meta_path(d: Path, upload_id: str) -> Path:
    return d / f"{upload_id}.json"


def _bin_path(d: Path, upload_id: str) -> Path:
    return d / f"{upload_id}.bin"


def _drop(d: Path, upload_id: str) -> None:
    for p in (_meta_path(d, upload_id), _bin_path(d, upload_id)):
        try:
            p.unlink()
        except OSError:
            pass


def purge(account_id: Optional[str] = None) -> int:
    """Снести протухшее и лишнее. Зовётся на каждой загрузке — отдельного
    планировщика тут не нужно: каталог растёт только при записи в него."""
    d = _dir(account_id)
    if not d.exists():
        return 0
    now = time.time()
    alive: list[tuple[float, str]] = []
    killed = 0
    for meta in sorted(d.glob("*.json")):
        uid = meta.stem
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
            ts = float(info.get("ts") or 0)
        except Exception:
            _drop(d, uid)
            killed += 1
            continue
        if now - ts > TTL_SECONDS:
            _drop(d, uid)
            killed += 1
            continue
        alive.append((ts, uid))
    alive.sort()
    for _ts, uid in alive[:-MAX_FILES_PER_ACCOUNT] if len(alive) > MAX_FILES_PER_ACCOUNT else []:
        _drop(d, uid)
        killed += 1
    return killed


def save(name: str, mime: str, content: bytes,
         account_id: Optional[str] = None) -> dict:
    """Положить файл и вернуть его паспорт `{upload_id, name, mime, size}`."""
    name = (name or "файл").strip()[:200] or "файл"
    mime = (mime or "application/octet-stream").strip()[:100]
    if not content:
        raise UploadError("Файл пустой.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ.")
    if not is_allowed(name, mime):
        raise UploadError(
            "Такой тип файла не принимается: нужны архивы, текстовые файлы или картинки.")

    purge(account_id)
    d = _dir(account_id)
    d.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    _bin_path(d, upload_id).write_bytes(content)
    info = {"upload_id": upload_id, "name": name, "mime": mime,
            "size": len(content), "ts": time.time()}
    tmp = _meta_path(d, upload_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_meta_path(d, upload_id))
    return {"upload_id": upload_id, "name": name, "mime": mime,
            "size": len(content)}


def get(upload_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    """Паспорт загрузки, если она ещё жива. `None` — нет такой (или протухла)."""
    # Идентификатор приходит от клиента и подставляется в путь — принимаем
    # только hex uuid, иначе `../../` увёл бы чтение из каталога аккаунта.
    uid = (upload_id or "").strip()
    if not uid or len(uid) != 32 or any(c not in "0123456789abcdef" for c in uid.lower()):
        return None
    d = _dir(account_id)
    meta = _meta_path(d, uid.lower())
    if not meta.exists() or not _bin_path(d, uid.lower()).exists():
        return None
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - float(info.get("ts") or 0) > TTL_SECONDS:
        _drop(d, uid.lower())
        return None
    return info


def to_attachment(upload_id: str, account_id: Optional[str] = None) -> Optional[dict]:
    """Загрузка → обычное вложение чата (`api/ai.Attachment`).

    Форма ровно та же, что раньше собирал браузер, поэтому ни `ai_agent`, ни
    `ai_archives` про двухфазную отправку знать не должны: архив приезжает
    base64'ом, текстовый файл — текстом.
    """
    info = get(upload_id, account_id)
    if info is None:
        return None
    raw = _bin_path(_dir(account_id), info["upload_id"]).read_bytes()
    name, mime = info.get("name") or "файл", info.get("mime") or ""
    if ai_archives.is_archive(name, mime) or mime.lower() in _IMAGE_MIME:
        return {"name": name, "mime": mime, "text": "",
                "data_b64": base64.b64encode(raw).decode("ascii"), "images": []}
    # Текстовый файл: декодируем здесь — агенту нужен текст, а не base64.
    return {"name": name, "mime": mime or "text/plain",
            "text": raw.decode("utf-8", errors="replace"),
            "data_b64": "", "images": []}
