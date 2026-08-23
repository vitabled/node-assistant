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

Границы. Раньше их было три (потолок на файл, TTL 24 часа, потолок числа файлов
на аккаунт) — и вторая с третьей ломали ровно то, ради чего человек прикладывал
файл: приложил .tsv к разговору, агент разобрал половину, назавтра вернулся —
файла нет ни в чате, ни где-либо ещё. Теперь загрузка привязана к РАЗГОВОРУ
(`session_id`) и живёт столько же, сколько он: удаляется только вместе с ним
(`purge_session` из `DELETE /api/ai/chat/history`). TTL и вытеснение остались
только для СИРОТ — загрузок без сессии (старые файлы до этого изменения и
диагностические заходы мимо чата), иначе каталог рос бы вечно.
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

#: Сколько живёт СИРОТСКАЯ загрузка — та, у которой нет `session_id` (файлы,
#: залитые до привязки к разговору, и диагностические заходы мимо чата). Файлы
#: разговора этим TTL не трогаются вообще: их удаляет только удаление чата.
TTL_SECONDS = 24 * 60 * 60

#: Сколько СИРОТСКИХ загрузок держим на аккаунт. Файлы разговоров не вытесняются
#: (у них есть владелец, который их удалит), поэтому лимит считается только по
#: сиротам — и поднят с 20 до 50: раньше он делил место с файлами чатов.
MAX_FILES_PER_ACCOUNT = 50

#: Идентификатор разговора по умолчанию. Пустой `session_id` — это НЕ «файл
#: ничей»: у клиента до первого «Новый чат» никакого id нет, а разговор есть.
#: Тот же приём и та же строка, что в `ai_chat_store._clean_id`.
DEFAULT_SESSION = "default"
MAX_SESSION_ID = 64

#: Картиночные mime — их фронт тоже умеет прикладывать.
_IMAGE_MIME = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class UploadError(ValueError):
    """Файл не приняли. Текст показывается человеку дословно."""


def norm_session(session_id: Optional[str]) -> str:
    """`""` → `"default"`: разговор по умолчанию — полноценный разговор."""
    return (session_id or "").strip()[:MAX_SESSION_ID] or DEFAULT_SESSION


def _session_of(info: dict) -> str:
    """Сессия загрузки по её метаданным. `""` — сирота (ключа нет вовсе).

    ⚠️ Ключ ОТСУТСТВУЕТ только у файлов, залитых до привязки к разговору. Новые
    загрузки всегда несут сессию (минимум `default`), поэтому «пусто» здесь
    честно значит «владельца нет» — такой файл и подпадает под TTL.
    """
    sid = info.get("session_id")
    return sid.strip() if isinstance(sid, str) else ""


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
    """Снести протухшее и лишнее СРЕДИ СИРОТ. Зовётся на каждой загрузке —
    отдельного планировщика тут не нужно: каталог растёт только при записи.

    ⚠️ Файлы с `session_id` не трогаются ни по TTL, ни по лимиту: их судьбу
    решает пользователь, удаляя разговор (`purge_session`). Иначе повторялся бы
    исходный симптом — вложение исчезало из живого чата само по себе.
    """
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
        if _session_of(info):
            continue          # файл разговора: не протухает и не вытесняется
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


def purge_session(account_id: Optional[str], session_id: str) -> int:
    """Снести загрузки ОДНОГО разговора. Зовётся из `DELETE /chat/history`.

    Это единственный штатный способ потерять вложение чата: пользователь удалил
    разговор — вместе с ним ушли и его файлы.
    """
    sid = norm_session(session_id)
    d = _dir(account_id)
    if not d.exists():
        return 0
    killed = 0
    for meta in sorted(d.glob("*.json")):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue          # мусор разберёт purge(), тут не наша сессия
        if _session_of(info) == sid:
            _drop(d, meta.stem)
            killed += 1
    return killed


def wipe_account(account_id: Optional[str] = None) -> int:
    """Снести ВСЕ загрузки аккаунта — для «удалить все разговоры»."""
    d = _dir(account_id)
    if not d.exists():
        return 0
    killed = 0
    for meta in sorted(d.glob("*.json")):
        _drop(d, meta.stem)
        killed += 1
    return killed


def save(name: str, mime: str, content: bytes,
         account_id: Optional[str] = None, session_id: str = "") -> dict:
    """Положить файл и вернуть его паспорт `{upload_id, name, mime, size}`.

    `session_id` — разговор-владелец: пока он жив, жив и файл. Пустой означает
    разговор по умолчанию, а не «ничей» (см. `norm_session`).
    """
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
            "size": len(content), "ts": time.time(),
            "session_id": norm_session(session_id)}
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
    # TTL — только для сирот: файл разговора не должен исчезать из живого чата.
    if not _session_of(info) and time.time() - float(info.get("ts") or 0) > TTL_SECONDS:
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
