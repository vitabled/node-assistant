"""Распаковка архивов, приложенных к чату ассистента.

Симптом, ради которого модуль появился: пользователь прикладывает
`as-ip-blocks-ipv4-only.tar.gz`, а до агента не доезжает НИЧЕГО. Фронт пытался
прочитать архив как текст, получал мусор и отбрасывал файл целиком; агент видел
пустое сообщение и уходил на второй круг `search_attachment` → `web_search`,
пока не кончались шаги.

Решение: архив едет на сервер как base64 (тот же канал, что у картинок), а
здесь разворачивается в ОБЫЧНЫЕ текстовые вложения — по одному на файл внутри.
Дальше работают уже существующие `read_attachment` / `search_attachment`, и
ничего в них знать про архивы не нужно.

Границы (они же защита от zip-bomb): потолок на суммарный распакованный объём,
на размер одного файла и на число файлов. Проверяем и ЗАЯВЛЕННЫЙ размер (в
заголовке архива), и фактически прочитанное: заголовок можно подделать, а
читать десятки гигабайт «до упора» — способ уронить процесс одним файлом на
пару килобайт.

Ошибка распаковки НЕ теряет вложение: битый или неопознанный архив возвращает
`None`, и вызывающий оставляет исходный элемент как есть — модель хотя бы видит
имя файла и может о нём сказать.
"""
from __future__ import annotations

import base64
import gzip
import io
import logging
import os
import tarfile
import zipfile
from typing import Optional

log = logging.getLogger(__name__)

#: Суммарный объём распакованного текста на ОДИН архив. Защита от zip-bomb:
#: 42 КБ архива способны развернуться в 4,5 ГБ нулей.
MAX_TOTAL_BYTES = 100 * 1024 * 1024

#: Сколько текста берём из одного файла внутри архива. Тот же потолок, что у
#: обычного вложения (`ai_agent.MAX_TEXT_CHARS`).
MAX_FILE_BYTES = 2 * 1024 * 1024

#: Сколько файлов вообще достаём. Каталог из 10 000 мелких файлов — это не
#: материал для разговора, а способ забить контекст списком имён.
MAX_FILES = 200

#: Расширения, которые считаем текстом. Белый список, а не «попробуем декодить
#: всё»: картинка в utf-8 с errors='replace' декодируется успешно и даёт
#: мегабайт мусора, который выглядит как данные.
TEXT_EXT = {
    ".txt", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".conf", ".cfg", ".log", ".md", ".rst", ".list", ".lst",
    ".xml", ".html", ".htm", ".sql", ".env", ".properties",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".go", ".rs",
    ".c", ".h", ".cpp", ".java", ".rb", ".php", ".pl", ".lua",
}

#: Файлы без расширения, которые всё равно текст.
TEXT_NAMES = {
    "readme", "license", "licence", "changelog", "makefile", "dockerfile",
    "authors", "contributors", "notice", "copying", "install",
}

_ARCHIVE_EXT = (".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
                ".zip", ".tar")

_ARCHIVE_MIME = {
    "application/gzip", "application/x-gzip", "application/x-tar",
    "application/x-tgz", "application/tar+gzip", "application/x-gtar",
    "application/zip", "application/x-zip-compressed", "application/x-compressed",
    "application/x-bzip2", "application/x-xz",
}


def is_archive(name: str, mime: str = "") -> bool:
    """Похоже ли вложение на архив.

    Смотрим и на mime, и на имя: браузеры отдают `.tar.gz` то как
    `application/gzip`, то как `application/x-gzip`, то вообще пустой строкой
    (Windows без зарегистрированного типа).
    """
    if (mime or "").lower().strip() in _ARCHIVE_MIME:
        return True
    low = (name or "").lower().strip()
    return low.endswith(_ARCHIVE_EXT)


def _is_text_name(path: str) -> bool:
    base = os.path.basename(path or "").lower()
    if base.endswith(".gz"):            # `hosts.txt.gz` внутри tar
        base = base[:-3]
    ext = os.path.splitext(base)[1]
    if ext in TEXT_EXT:
        return True
    return not ext and base in TEXT_NAMES


def _decode(raw: bytes) -> Optional[str]:
    """Байты → текст, либо None, если это явно не текст.

    NUL-байт — самый дешёвый и самый надёжный признак бинаря: в текстовом файле
    его не бывает, а расширение врёт (`.log` бывает и у бинарного дампа).
    """
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            return None


def _maybe_gunzip(path: str, raw: bytes) -> bytes:
    """`file.txt.gz` внутри tar — обычное дело в дампах реестров."""
    if not path.lower().endswith(".gz"):
        return raw
    try:
        return gzip.decompress(raw)[:MAX_FILE_BYTES]
    except Exception:
        return raw


def _safe_name(path: str) -> str:
    """Имя для показа модели: без ведущих `/` и `..`.

    Файл никуда не пишется (всё в памяти), но путь попадает в промпт и в
    аргументы инструментов — оставлять там `../../etc/passwd` незачем.
    """
    parts = [p for p in (path or "").replace("\\", "/").split("/")
             if p not in ("", ".", "..")]
    return "/".join(parts) or "file"


class _Budget:
    """Общий счётчик распакованного. Отдельный объект, потому что считать надо
    СКВОЗЬ файлы: сто файлов по 2 МБ — те же 200 МБ, что и один большой."""

    def __init__(self, total: Optional[int] = None) -> None:
        # ⚠️ Читаем константу ЗДЕСЬ, а не в значении по умолчанию: значение
        # по умолчанию вычисляется один раз при импорте, и поменять потолок
        # (тестом или настройкой) стало бы невозможно — лимит молча остался бы
        # прежним, а проверка «бомба отсечена» — зелёной и бессмысленной.
        self.left = MAX_TOTAL_BYTES if total is None else total
        self.exhausted = False

    def take(self, n: int) -> bool:
        if n > self.left:
            self.exhausted = True
            return False
        self.left -= n
        return True


def _iter_tar(data: bytes, budget: _Budget):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
        for member in tf:
            if not member.isfile():
                continue
            # Заявленный размер проверяем ДО чтения: так бомба отсекается по
            # заголовку, не заняв память.
            if member.size > MAX_FILE_BYTES:
                yield _safe_name(member.name), None, "слишком большой"
                continue
            if not budget.take(member.size):
                return
            fh = tf.extractfile(member)
            if fh is None:
                continue
            yield _safe_name(member.name), fh.read(MAX_FILE_BYTES), ""


def _iter_zip(data: bytes, budget: _Budget):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE_BYTES:
                yield _safe_name(info.filename), None, "слишком большой"
                continue
            if not budget.take(info.file_size):
                return
            with zf.open(info) as fh:
                # +1 байт сверх потолка: если файл соврал о своём размере,
                # прочитанное окажется длиннее заявленного — и это видно.
                raw = fh.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                yield _safe_name(info.filename), None, "слишком большой"
                continue
            yield _safe_name(info.filename), raw, ""


def unpack(name: str, data_b64: str) -> Optional[dict]:
    """Развернуть архив в список вложений.

    Возвращает `{"files": [{name, mime, text}], "skipped": [...],
    "truncated": bool}` либо **None**, если это не архив или он повреждён — тогда
    вызывающий оставляет исходное вложение нетронутым.
    """
    if not data_b64:
        return None
    try:
        blob = base64.b64decode(data_b64, validate=False)
    except Exception:
        return None
    if not blob:
        return None

    budget = _Budget()
    low = (name or "").lower()
    readers = ([_iter_zip, _iter_tar] if low.endswith(".zip")
               else [_iter_tar, _iter_zip])

    entries = None
    for reader in readers:
        try:
            entries = list(reader(blob, budget))
            break
        except Exception:
            budget = _Budget()          # неудачная попытка не тратит бюджет
            continue
    if entries is None:
        return None

    files: list[dict] = []
    skipped: list[str] = []
    for path, raw, note in entries:
        if len(files) >= MAX_FILES:
            break
        if raw is None:
            skipped.append(f"{path} ({note})" if note else path)
            continue
        if not _is_text_name(path):
            skipped.append(path)
            continue
        raw = _maybe_gunzip(path, raw)
        text = _decode(raw)
        if text is None:
            skipped.append(path)
            continue
        if not text.strip():
            continue
        files.append({"name": path, "mime": "text/plain",
                      "text": text[:MAX_FILE_BYTES], "images": []})

    if not files and not skipped:
        # Пустой tar технически разбирается, но вкладывать нечего — пусть
        # модель видит исходное имя файла, а не пустоту.
        return None
    return {"files": files, "skipped": skipped,
            "truncated": budget.exhausted or len(entries) > MAX_FILES}


#: Сколько имён перечисляем в промпте. Дальше список сам становится стеной
#: текста, ради которой места для данных уже не остаётся.
LIST_NAMES = 20

#: Сколько символов первого файла показываем образцом — чтобы модель поняла
#: ФОРМАТ данных, не тратя шаг на `read_attachment`.
SAMPLE_CHARS = 1000


def describe(archive: str, result: dict) -> str:
    """Справка об архиве для промпта: что распаковано и чем это читать."""
    files = result.get("files") or []
    skipped = result.get("skipped") or []
    lines = [f"Архив «{archive}» распакован на сервере: файлов с текстом — "
             f"{len(files)}."]
    if files:
        shown = [f["name"] for f in files[:LIST_NAMES]]
        tail = (f" … и ещё {len(files) - LIST_NAMES}"
                if len(files) > LIST_NAMES else "")
        lines.append("Файлы: " + ", ".join(shown) + tail)
    if skipped:
        lines.append(f"Пропущено нечитаемых/бинарных: {len(skipped)}"
                     + (f" ({', '.join(skipped[:5])})" if len(skipped) <= 5 else ""))
    if result.get("truncated"):
        lines.append("⚠️ Архив распакован НЕ ПОЛНОСТЬЮ: сработал лимит объёма.")
    lines.append(
        "Читай их инструментом read_attachment(name='<имя файла>', offset=…) "
        "или ищи через search_attachment — имя можно указывать как полным путём "
        "внутри архива, так и одним именем файла.")
    if files and len(files) <= LIST_NAMES:
        sample = (files[0].get("text") or "")[:SAMPLE_CHARS]
        if sample:
            lines.append(f"Начало файла «{files[0]['name']}»:\n{sample}")
    return "\n".join(lines)
