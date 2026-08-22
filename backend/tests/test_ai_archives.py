"""Архивы во вложениях чата.

Симптом, ради которого всё это писалось: пользователь прикладывает
`as-ip-blocks-ipv4-only.tar.gz`, а агент крутится в `search_attachment` →
`web_search` и отвечает, что данных нет. До него не доезжало НИЧЕГО: фронт
пытался прочитать gzip как текст и отбрасывал файл, а бэкенд умел работать
только с `attachment.text`.
"""
import base64
import io
import tarfile
import zipfile

import pytest

from app.services import ai_agent, ai_archives, ai_tools


# ── помощники ─────────────────────────────────────────────────
def make_targz(files: dict[str, bytes], mode: str = "w:gz") -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


def make_zip(files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return base64.b64encode(buf.getvalue()).decode()


BLOCKS = {
    "as-ip-blocks/data/ru.csv": b"asn,prefix\n12345,5.8.0.0/16\n12345,31.13.0.0/18\n",
    "as-ip-blocks/data/de.csv": b"asn,prefix\n3320,2.160.0.0/12\n",
    "as-ip-blocks/README.md": b"# AS IP blocks\nIPv4 only dump.\n",
}


def ctx_with(items):
    ctx = ai_tools.ToolContext(account_id="acc", user_id="acc", readonly=False)
    ctx.attachments = list(items)
    return ctx


# ── распознавание ─────────────────────────────────────────────
@pytest.mark.parametrize("name,mime", [
    ("as-ip-blocks-ipv4-only.tar.gz", "application/gzip"),
    ("dump.tgz", ""),
    ("dump.zip", ""),
    ("dump.tar", ""),
    # Windows часто не знает типа и шлёт пустой mime — узнаём по имени.
    ("blocks.tar.gz", ""),
    # …а иногда наоборот: имя без расширения, зато mime честный.
    ("blob", "application/zip"),
])
def test_archive_is_recognised(name, mime):
    assert ai_archives.is_archive(name, mime)


@pytest.mark.parametrize("name,mime", [
    ("nodes.log", "text/plain"),
    ("catalog.html", "text/html"),
    ("shot.png", "image/png"),
])
def test_plain_files_are_not_archives(name, mime):
    assert not ai_archives.is_archive(name, mime)


# ── распаковка ────────────────────────────────────────────────
def test_targz_becomes_separate_text_attachments():
    """Главное, ради чего фича: архив превращается в файлы, а не в пустоту."""
    res = ai_archives.unpack("as-ip-blocks-ipv4-only.tar.gz", make_targz(BLOCKS))
    assert res is not None
    names = [f["name"] for f in res["files"]]
    assert set(names) == set(BLOCKS)
    ru = next(f for f in res["files"] if f["name"].endswith("ru.csv"))
    assert "5.8.0.0/16" in ru["text"] and ru["mime"] == "text/plain"


def test_zip_unpacks_too():
    res = ai_archives.unpack("blocks.zip", make_zip(BLOCKS))
    assert res is not None
    assert len(res["files"]) == 3


def test_plain_tar_unpacks():
    res = ai_archives.unpack("blocks.tar", make_targz(BLOCKS, mode="w"))
    assert res is not None
    assert len(res["files"]) == 3


def test_binary_members_are_skipped_not_decoded_into_garbage():
    """⚠️ Картинка в latin-1 декодируется «успешно» и даёт мегабайт мусора,
    неотличимого от данных. Поэтому — белый список расширений + проверка NUL."""
    data = dict(BLOCKS)
    data["as-ip-blocks/logo.png"] = b"\x89PNG\r\n\x1a\n" + b"\x00" * 500
    res = ai_archives.unpack("x.tar.gz", make_targz(data))
    assert not any(f["name"].endswith(".png") for f in res["files"])
    assert len(res["files"]) == 3, "текстовые файлы при этом на месте"
    assert any("logo.png" in s for s in res["skipped"])


def test_text_file_with_nul_bytes_is_skipped():
    res = ai_archives.unpack("x.tar.gz", make_targz(
        {"data/fake.csv": b"asn,prefix\n\x00\x00\x00binary\n"}))
    # Единственный файл нечитаем → в files пусто, но в skipped он есть, значит
    # результат не None и модель видит справку.
    assert res is not None and res["files"] == []
    assert res["skipped"] == ["data/fake.csv"]


def test_gz_inside_tar_is_decompressed():
    """`hosts.txt.gz` внутри tar — обычное дело в дампах реестров."""
    import gzip
    inner = gzip.compress(b"1.2.3.0/24\n4.5.6.0/24\n")
    res = ai_archives.unpack("x.tar", make_targz({"nets.txt.gz": inner}, mode="w"))
    assert "1.2.3.0/24" in res["files"][0]["text"]


# ── устойчивость ──────────────────────────────────────────────
def test_corrupt_archive_returns_none_and_does_not_raise():
    junk = base64.b64encode(b"this is definitely not a tarball" * 20).decode()
    assert ai_archives.unpack("broken.tar.gz", junk) is None


def test_empty_and_garbage_input_return_none():
    assert ai_archives.unpack("x.tar.gz", "") is None
    assert ai_archives.unpack("x.tar.gz", "!!!not base64!!!") is None


# ── защита от zip-bomb ────────────────────────────────────────
def test_zip_bomb_is_capped_by_total_budget(monkeypatch):
    """42 КБ архива разворачиваются в гигабайты нулей. Верхняя граница должна
    сработать РАНЬШЕ, чем память кончится, — поэтому лимит проверяется по
    заголовку, а не по фактически прочитанному."""
    monkeypatch.setattr(ai_archives, "MAX_TOTAL_BYTES", 4096)
    monkeypatch.setattr(ai_archives, "MAX_FILE_BYTES", 4096)
    bomb = {f"bomb/{i}.txt": b"A" * 3000 for i in range(20)}
    res = ai_archives.unpack("bomb.zip", make_zip(bomb))
    assert res is not None
    total = sum(len(f["text"]) for f in res["files"])
    assert total <= 4096, "распаковали больше бюджета"
    assert res["truncated"], "и об усечении сказано вслух"


def test_single_oversized_member_is_skipped_not_truncated_silently(monkeypatch):
    monkeypatch.setattr(ai_archives, "MAX_FILE_BYTES", 1000)
    res = ai_archives.unpack("x.tar.gz", make_targz({
        "small.csv": b"ok\n", "huge.csv": b"B" * 50_000}))
    assert [f["name"] for f in res["files"]] == ["small.csv"]
    assert any("huge.csv" in s for s in res["skipped"])


def test_file_count_is_capped(monkeypatch):
    monkeypatch.setattr(ai_archives, "MAX_FILES", 5)
    res = ai_archives.unpack("many.zip", make_zip(
        {f"f{i}.txt": b"x\n" for i in range(30)}))
    assert len(res["files"]) == 5 and res["truncated"]


def test_path_traversal_names_are_normalised():
    """Файл никуда не пишется, но путь уходит в промпт и в аргументы
    инструментов — `../../etc/passwd` там незачем."""
    res = ai_archives.unpack("evil.tar", make_targz(
        {"../../etc/passwd.conf": b"root:x:0:0\n"}, mode="w"))
    assert res["files"][0]["name"] == "etc/passwd.conf"


# ── справка для промпта ───────────────────────────────────────
def test_describe_lists_files_and_tells_how_to_read_them():
    res = ai_archives.unpack("as-ip-blocks-ipv4-only.tar.gz", make_targz(BLOCKS))
    note = ai_archives.describe("as-ip-blocks-ipv4-only.tar.gz", res)
    assert "as-ip-blocks-ipv4-only.tar.gz" in note
    assert "ru.csv" in note and "de.csv" in note
    assert "read_attachment" in note and "search_attachment" in note


def test_describe_shows_only_the_list_when_files_are_many():
    """Больше двадцати файлов — образец содержимого не показываем: список сам
    станет стеной текста, в которой утонет вопрос."""
    res = ai_archives.unpack("many.zip", make_zip(
        {f"d/f{i}.txt": b"payload-%d\n" % i for i in range(60)}))
    note = ai_archives.describe("many.zip", res)
    assert "и ещё" in note
    assert "payload-0" not in note, "образец при большом списке не нужен"


# ── интеграция с агентом ──────────────────────────────────────
def test_expand_archives_replaces_archive_with_its_files():
    items = [{"name": "as-ip-blocks-ipv4-only.tar.gz", "mime": "application/gzip",
              "text": "", "data_b64": make_targz(BLOCKS)}]
    out = ai_agent.expand_archives(items)
    names = [a["name"] for a in out]
    assert names[0] == "as-ip-blocks-ipv4-only.tar.gz", "справка идёт первой"
    assert "as-ip-blocks/data/ru.csv" in names
    assert all(a.get("inline") is False for a in out[1:]), \
        "файлы архива не вклеиваются в промпт целиком"


def test_expand_archives_keeps_broken_archive_as_attachment():
    """⚠️ Отбросить нераспакованное значит показать модели пустоту, и она честно
    ответит «файла нет». Пусть видит хотя бы имя."""
    items = [{"name": "broken.tar.gz", "mime": "application/gzip",
              "text": "", "data_b64": base64.b64encode(b"nope" * 50).decode()}]
    out = ai_agent.expand_archives(items)
    assert len(out) == 1 and out[0]["name"] == "broken.tar.gz"
    assert out[0]["text"], "и текст непустой, иначе вложение выпадет дальше"


def test_expand_archives_leaves_plain_files_untouched():
    items = [{"name": "nodes.log", "mime": "text/plain", "text": "hi", "data_b64": ""}]
    assert ai_agent.expand_archives(items) == items


def test_expand_archives_is_idempotent():
    """`run_agent` и `build_user_content` зовут её независимо друг от друга."""
    items = [{"name": "x.zip", "mime": "application/zip", "text": "",
              "data_b64": make_zip(BLOCKS)}]
    once = ai_agent.expand_archives(items)
    assert ai_agent.expand_archives(once) == once


def test_prompt_gets_the_file_list_not_the_whole_archive():
    body = ai_agent.build_user_content(
        "какие подсети у RU?",
        [{"name": "as-ip-blocks-ipv4-only.tar.gz", "mime": "application/gzip",
          "text": "", "data_b64": make_targz(BLOCKS)}],
        "openai")
    assert isinstance(body, str)
    assert "ru.csv" in body and "read_attachment" in body
    # Содержимое второго файла в промпт не вклеивается — оно читается
    # инструментом. Иначе сотня файлов архива вытеснит сам вопрос.
    assert "2.160.0.0/12" not in body


# ── поиск вложения по имени ───────────────────────────────────
def test_find_attachment_by_basename_inside_archive():
    """⚠️ Модель просит «ru.csv», а вложение зовётся полным путём внутри
    архива. Отказ «нет такого файла» здесь — прямой путь в цикл поиска."""
    ctx = ctx_with([{"name": "as-ip-blocks/data/ru.csv", "text": "asn,prefix\n"},
                    {"name": "as-ip-blocks/data/de.csv", "text": "x\n"}])
    found = ai_tools._find_attachment(ctx, "ru.csv")
    assert found is not None and found["name"] == "as-ip-blocks/data/ru.csv"


def test_find_attachment_exact_path_still_wins():
    ctx = ctx_with([{"name": "a/ru.csv", "text": "A"}, {"name": "b/ru.csv", "text": "B"}])
    assert ai_tools._find_attachment(ctx, "b/ru.csv")["text"] == "B"


def test_find_attachment_by_windows_style_path():
    ctx = ctx_with([{"name": "as-ip-blocks/data/ru.csv", "text": "A"},
                    {"name": "other.txt", "text": "B"}])
    assert ai_tools._find_attachment(ctx, r"data\ru.csv")["text"] == "A"


def test_find_attachment_returns_none_when_nothing_matches():
    ctx = ctx_with([{"name": "a.csv", "text": "A"}, {"name": "b.csv", "text": "B"}])
    assert ai_tools._find_attachment(ctx, "zzz.csv") is None


def test_read_attachment_works_on_unpacked_archive_member():
    import asyncio
    items = ai_agent.expand_archives(
        [{"name": "x.tar.gz", "mime": "application/gzip", "text": "",
          "data_b64": make_targz(BLOCKS)}])
    ctx = ctx_with([{"name": a["name"], "text": a.get("text") or ""} for a in items])
    ok, res = asyncio.run(ai_tools.run("read_attachment", {"name": "ru.csv"}, ctx))
    assert ok and "5.8.0.0/16" in res["text"]
    ok, res = asyncio.run(
        ai_tools.run("search_attachment", {"name": "de.csv", "query": "3320"}, ctx))
    assert ok and res["total"] == 1


# ── потолок вложений ──────────────────────────────────────────
def test_archive_members_do_not_eat_the_user_attachment_limit():
    """⚠️ MAX_ATTACHMENTS = 5 — про файлы ОТ ПОЛЬЗОВАТЕЛЯ. Если считать в нём
    содержимое архива, от каталога на 200 подсетей останется пять штук."""
    many = {f"d/f{i}.csv": b"prefix\n" for i in range(30)}
    items = ai_agent.expand_archives(
        [{"name": "big.zip", "mime": "application/zip", "text": "",
          "data_b64": make_zip(many)}])
    capped = ai_agent.cap_attachments(items)
    assert len(capped) == 31, "справка + все 30 файлов архива"


def test_cap_attachments_still_limits_user_files():
    items = [{"name": f"f{i}.txt", "text": "x"}
             for i in range(ai_agent.MAX_ATTACHMENTS + 3)]
    capped = ai_agent.cap_attachments(items)
    assert len(capped) == ai_agent.MAX_ATTACHMENTS


def test_members_of_a_dropped_archive_are_dropped_too():
    """Архив за потолком уезжает вместе со своим содержимым: оставить файлы без
    справки значило бы показать модели безымянные куски неизвестно чего."""
    head = [{"name": f"f{i}.txt", "text": "x"} for i in range(ai_agent.MAX_ATTACHMENTS)]
    tail = ai_agent.expand_archives(
        [{"name": "late.zip", "mime": "application/zip", "text": "",
          "data_b64": make_zip(BLOCKS)}])
    capped = ai_agent.cap_attachments(head + tail)
    assert len(capped) == ai_agent.MAX_ATTACHMENTS
    assert not any(a.get("from_archive") for a in capped)
