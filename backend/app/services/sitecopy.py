"""Копия сайта в Библиотеку (Wave-4 PR-11): httrack-зеркало → файлы Библиотеки.

HTTrack собран из завендоренного исходника в образ backend'а (см. Dockerfile).
Запуск — фоновой задачей (task_store, логи в /ws/logs): лимиты глубины, объёма
и времени заданы снаружи валидацией + флагами httrack. По завершении файлы
импортируются в Библиотеку РАСПАКОВАННЫМИ (вариант B) в папку
`Сайты/<host>-<дата>` — html первыми, внутренний кэш httrack исключается.
"""
from __future__ import annotations

import asyncio
import mimetypes
import re
import tempfile
import time
from pathlib import Path

from app.services import library_store
from app.services.task_store import TaskStatus

_HOST_RE = re.compile(r"^https?://([A-Za-z0-9.\-]+)")

# Внутренняя служебная директория httrack — в библиотеку не несём.
_SKIP_DIRS = {"hts-cache"}


def build_httrack_cmd(url: str, dest: Path, *, depth: int, max_bytes: int,
                      sockets: int = 4) -> list[str]:
    """Команда зеркалирования. Все лимиты — и в argv, и валидацией выше:
    -rN глубина, -%e0 без внешней глубины (не уходим на другие домены),
    --max-size объём, --timeout/--retries чтобы не виснуть."""
    return [
        "httrack", url,
        "-O", str(dest),
        f"-r{depth}",               # глубина рекурсии
        "-%e0",                     # внешняя глубина 0: только свой домен
        "--max-size", str(max_bytes),
        "--timeout", "20",
        "--retries", "1",
        "--sockets", str(sockets),
        "--user-agent", "node-assistant sitecopy (+https://github.com/vitabled/node-assistant)",
        "--quiet",
    ]


def collect_files(src: Path) -> list[tuple[str, bytes, str]]:
    """(relpath, bytes, mime) зеркала: html/htm первыми, кэш httrack пропускаем."""
    html: list[tuple[str, bytes, str]] = []
    other: list[tuple[str, bytes, str]] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or any(part in _SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(src).as_posix()
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        entry = (rel, p.read_bytes(), mime)
        (html if p.suffix.lower() in (".html", ".htm") else other).append(entry)
    return html + other


async def run_sitecopy(url: str, *, depth: int, max_bytes: int, task,
                       account_id: str) -> None:
    """Фоновая задача: httrack → распакованный импорт в Библиотеку."""
    tmp = tempfile.TemporaryDirectory(prefix="na-sitecopy-")
    try:
        dest = Path(tmp.name) / "mirror"
        cmd = build_httrack_cmd(url, dest, depth=depth, max_bytes=max_bytes)
        task.add_log(f"$ {' '.join(cmd[:3])} … -r{depth} --max-size {max_bytes}")
        task.set_step(1, TaskStatus.RUNNING)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("httrack превысил лимит времени (10 минут)")
        out = (out_b or b"").decode("utf-8", "replace")
        rc = proc.returncode or 0
        # httrack возвращает 0 даже при частичных ошибках сети — судим по файлам.
        entries = collect_files(dest) if dest.exists() else []
        task.add_log(f"httrack завершён (rc={rc}), файлов: {len(entries)}")
        for line in out.splitlines()[-5:]:
            task.add_log(line)
        if not entries:
            raise RuntimeError("Копия пуста — сайт не ответил или недоступен")

        m = _HOST_RE.match(url)
        host = (m.group(1) if m else "site").replace(":", "_")
        folder = f"Сайты/{host}-{time.strftime('%Y%m%d-%H%M')}"
        task.set_step(2, TaskStatus.RUNNING)
        stats = library_store.add_files_bulk(entries, folder, account_id)
        task.add_log(
            f"\x1b[32mИмпортировано в Библиотеку: {stats['imported']} файлов "
            f"(папка «{stats['folder']}»)\x1b[0m")
        if stats["skipped_oversize"]:
            task.add_log(f"\x1b[33mПропущено (больше 25 МиБ): {stats['skipped_oversize']}\x1b[0m")
        if stats["skipped_cap"]:
            task.add_log(f"\x1b[33mПропущено (лимит библиотеки 500 элементов): "
                         f"{stats['skipped_cap']}\x1b[0m")
        task.finish(TaskStatus.SUCCESS)
    except Exception as exc:
        task.add_log(f"\x1b[31mОшибка: {exc}\x1b[0m")
        task.finish(TaskStatus.FAILED)
    finally:
        tmp.cleanup()


SITE_COPY_STEPS = ["Зеркалирование (httrack)", "Импорт файлов в Библиотеку"]
