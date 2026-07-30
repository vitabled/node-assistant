"""Журнал привилегированных действий: кто, когда, что пытался сделать.

Пока панелью владел один человек, журнал был не нужен: всё, что в ней произошло,
сделал он сам. С появлением ролей первый вопрос владельца — «кто отключил ноду»,
и ответить на него нечем.

Пишем JSONL в `DATA_DIR/audit.log`, без внешних зависимостей и без БД: запись
append-only, читается глазами и `grep`, ротация по размеру.

⚠️ **Журналируется РЕШЕНИЕ гейта, а не результат обработчика.** Запись делается в
`require_identity`, то есть до того, как ручка отработала: `allowed: true` значит
«доступ разрешён и запрос пошёл дальше», а не «операция удалась». Ловить исход
пришлось бы middleware поверх маршрутизации, а ценность журнала — именно в
попытках и отказах: успешный ответ и так виден по последствиям.

⚠️ **Секретов здесь нет и быть не должно.** Пишем метод, шаблон маршрута и
привилегию — не тело запроса. Тело несёт пароли, SSH-креды и токены, и журнал
мгновенно стал бы самым удобным местом для их сбора.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("audit")

MAX_BYTES = 5 * 1024 * 1024
_LOCK = threading.Lock()

#: Методы, которые ничего не меняют. Их успешные вызовы не журналируем — иначе
#: файл заполнит опрос дэшборда раз в 10 секунд, и в нём утонут настоящие события.
_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})


def _path() -> Path:
    from app.services import accounts

    return accounts.DATA_DIR / "audit.log"


def _rotate(p: Path) -> None:
    """Один бэкап: журнал нужен для «что было вчера», а не для вечного архива."""
    try:
        if p.exists() and p.stat().st_size >= MAX_BYTES:
            p.replace(p.with_suffix(".log.1"))
    except Exception:  # noqa: BLE001
        pass


def record(*, user: Optional[dict], method: str, route: str, allowed: bool,
           permission: str = "", source: str = "http") -> None:
    """Дописать событие. Никогда не бросает: сбой журнала не должен ломать запрос."""
    if allowed and (method or "").upper() in _SAFE:
        return
    entry = {
        "ts": int(time.time()),
        "user_id": (user or {}).get("id") or None,
        "login": (user or {}).get("login") or None,
        "method": (method or "").upper(),
        "route": route,
        "allowed": bool(allowed),
        "permission": permission,
        "source": source,
    }
    try:
        p = _path()
        with _LOCK:
            _rotate(p)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.info("audit.write_failed", extra={"err": str(exc)[:200]})


def tail(limit: int = 200, only_denied: bool = False) -> list[dict]:
    """Последние события, свежие первыми.

    Читаем файл целиком и берём хвост: при потолке 5 МБ это дешевле, чем
    изобретать обратное чтение с буферами, а точность важнее — построчный
    обратный разбор ломается на неполной последней строке.
    """
    p = _path()
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if only_denied and row.get("allowed"):
            continue
        out.append(row)
        if len(out) >= max(1, min(limit, 2000)):
            break
    return out
