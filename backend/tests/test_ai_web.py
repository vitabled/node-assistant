"""Веб-инструменты ассистента (`services/ai_web.py`) — БЕЗ живой сети.

Два источника недетерминированности убраны на входе (фикстура `_offline`):
  * `httpx.AsyncClient` подменяется клиентом с `MockTransport` — но kwargs
    вызывающего сохраняются, иначе тест проверял бы НЕ ТОТ клиент, что работает
    в проде (у `fetch` весь смысл в `follow_redirects=False`);
  * `net_guard.socket.getaddrinfo` — таблицей вместо DNS. Литеральные адреса
    (169.254.169.254, 127.0.0.1) резолвер и так не спрашивает, но публичные имена
    без этого требовали бы интернета.

Главный приём файла: **каждый тест про отказ считает исходящие запросы**. Для
SSRF-гарда и для «нет ключа — не ходим» проверяемое поведение — это именно
«запроса не было»; результат при этом одинаково пустой и в случае отказа, и в
случае честного «ничего не нашлось».
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx
import pytest

from app.services import ai_web, net_guard

# Публичные адреса для имён, которые в тестах должны считаться маршрутизируемыми.
# Неизвестное имя трактуется как литеральный IP (см. `_fake_getaddrinfo`), поэтому
# «плохие» адреса перечислять не нужно — они и так придут литералами.
_DNS = {
    "example.com": "93.184.216.34",
    "docs.example.com": "93.184.216.35",
    "searx.example": "93.184.216.40",
    "lite.duckduckgo.com": "40.114.177.156",
    "api.tavily.com": "104.18.20.1",
    "api.search.brave.com": "104.18.20.2",
}


def _fake_getaddrinfo(host, *_a, **_kw):
    ip = _DNS.get(host, host)
    # Настоящий резолвер на несуществующем имени бросает — `host_is_public`
    # рассчитывает именно на это (ловит Exception → «не публичный»).
    ipaddress.ip_address(ip)
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", _fake_getaddrinfo)
    # Лазейка ALLOW_PRIVATE_HOSTS=1 в окружении отключила бы гард целиком и
    # превратила бы SSRF-тесты в проверку заглушки. Фиксируем явно.
    monkeypatch.setattr(net_guard, "_ALLOW_PRIVATE", False)


def _wire(monkeypatch, handler) -> list[httpx.Request]:
    """Ставит MockTransport и возвращает список ушедших запросов."""
    calls: list[httpx.Request] = []
    real = httpx.AsyncClient

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        return real(*args, transport=httpx.MockTransport(recording), **kwargs)

    monkeypatch.setattr(ai_web.httpx, "AsyncClient", factory)
    return calls


def _never(_request: httpx.Request) -> httpx.Response:
    """Обработчик для случаев «сети быть не должно»."""
    raise AssertionError("сетевой запрос не должен был уйти")


# ── html_to_text ──────────────────────────────────────────────
_PAGE = (
    "<html><head><title>  Тест   страницы </title>"
    "<style>.a{color:red}</style><meta name='x' content='y'></head>"
    "<body><script>var secret = 1;</script>"
    "<h1>Заголовок</h1><p>Первый&nbsp;&nbsp;абзац.</p>\n\n\n\n"
    "<p>Второй абзац.</p><noscript>без js</noscript>"
    "<footer>подвал</footer></body></html>"
)


def test_html_to_text_extracts_the_title_and_drops_service_tags():
    title, text = ai_web.html_to_text(_PAGE)
    assert title == "Тест страницы"
    assert "Заголовок" in text and "Первый" in text and "Второй абзац." in text
    # Содержимое script/style/noscript/footer в промпт уезжать не должно.
    for junk in ("var secret", "color:red", "без js", "подвал"):
        assert junk not in text


def test_html_to_text_collapses_whitespace_and_blank_lines():
    _, text = ai_web.html_to_text(_PAGE)
    # &nbsp; после convert_charrefs становится \xa0 — он тоже схлопывается,
    # иначе модель видела бы «Первый\xa0\xa0абзац».
    assert "Первый абзац." in text
    assert "\xa0" not in text
    assert "\n\n\n" not in text
    assert text == text.strip()


def test_html_to_text_survives_broken_markup():
    # Незакрытые/лишние теги и «голые» угловые скобки — обычное дело в дикой
    # вёрстке; контракт модуля — не бросать никогда.
    title, text = ai_web.html_to_text("<p>привет<div>мир</p></b><<>&nbsp;<span")
    assert title == ""
    assert "привет" in text and "мир" in text


def test_html_to_text_falls_back_to_the_raw_string():
    assert ai_web.html_to_text("") == ("", "")
    # Текст без разметки парсер отдаёт как есть.
    assert ai_web.html_to_text("просто   текст")[1] == "просто текст"


# ── unwrap_ddg ────────────────────────────────────────────────
def test_unwrap_ddg_unwraps_the_redirect():
    href = ("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1"
            "&amp;rut=deadbeef")
    assert ai_web.unwrap_ddg(href) == "https://example.com/a?b=1"


def test_unwrap_ddg_passes_plain_links_through():
    assert ai_web.unwrap_ddg("https://example.org/x") == "https://example.org/x"
    assert ai_web.unwrap_ddg("http://example.org/x") == "http://example.org/x"


def test_unwrap_ddg_drops_non_http_schemes():
    assert ai_web.unwrap_ddg("") == ""
    assert ai_web.unwrap_ddg("ftp://example.org/x") == ""
    assert ai_web.unwrap_ddg("javascript:alert(1)") == ""


# ── parse_ddg ─────────────────────────────────────────────────
_DDG_LITE = """<html><body><table>
<tr><td><a rel="nofollow" class="result-link"
   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.com%2Fone&amp;rut=x"
   >Первый результат</a></td></tr>
<tr><td class="result-snippet">Описание первого.</td></tr>
<tr><td><a class="result-link" href="https://example.org/two">Второй результат</a></td></tr>
<tr><td class="result-snippet">Описание второго.</td></tr>
</table></body></html>"""

_DDG_HTML = """<div class="result results_links">
<a class="result__a js-result-title-link"
   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.com%2Fone">Первый результат</a>
<a class="result__snippet" href="https://docs.example.com/one">Описание первого.</a>
</div>
<div class="result"><a class="result__a" href="https://example.org/two">Второй результат</a>
<a class="result__snippet">Описание второго.</a></div>"""


@pytest.mark.parametrize("body", [_DDG_LITE, _DDG_HTML], ids=["lite", "html"])
def test_parse_ddg_reads_both_layouts(body):
    # Обе вёрстки живут у вендора одновременно, поэтому обе — обязательный
    # контракт, а не запасной вариант.
    got = ai_web.parse_ddg(body, 5)
    assert [x["title"] for x in got] == ["Первый результат", "Второй результат"]
    # Редирект развёрнут, обычная ссылка не тронута.
    assert got[0]["url"] == "https://docs.example.com/one"
    assert got[1]["url"] == "https://example.org/two"
    assert [x["snippet"] for x in got] == ["Описание первого.", "Описание второго."]


@pytest.mark.parametrize("body", [_DDG_LITE, _DDG_HTML], ids=["lite", "html"])
def test_parse_ddg_respects_count(body):
    assert len(ai_web.parse_ddg(body, 1)) == 1


def test_parse_ddg_returns_nothing_on_a_changed_layout():
    # Смена вёрстки у вендора = пустой список, а не исключение (иначе поиск
    # ронял бы весь ответ агента).
    assert ai_web.parse_ddg("<div class='новая-вёрстка'>x</div>", 5) == []


# ── search ────────────────────────────────────────────────────
def _json_ok(payload):
    return lambda _r: httpx.Response(200, json=payload)


@pytest.mark.parametrize("provider", ["tavily", "brave"])
def test_search_without_a_key_never_touches_the_network(monkeypatch, provider):
    calls = _wire(monkeypatch, _never)
    res = asyncio.run(ai_web.search("что-нибудь", provider, key=""))
    assert calls == []
    assert res["results"] == []
    # Отсутствие "error" доказывает, что до обработчика не дошли: сработай он,
    # AssertionError был бы проглочен контрактом «не бросает» и осел в error.
    assert "error" not in res
    assert res["note"] == ai_web.UNTRUSTED_NOTE


def test_search_searxng_rejects_a_private_base_url(monkeypatch):
    calls = _wire(monkeypatch, _never)
    res = asyncio.run(ai_web.search("q", "searxng", base_url="http://127.0.0.1:8080"))
    assert calls == []
    assert res["results"] == [] and "error" not in res


def test_search_searxng_reads_a_public_instance(monkeypatch):
    long_content = "щ" * 500
    calls = _wire(monkeypatch, _json_ok({"results": [
        {"title": "Док", "url": "https://docs.example.com/a", "content": long_content},
        {"title": "Ещё", "url": "https://example.org/b", "content": "коротко"},
        "мусор-не-словарь",
    ]}))
    # Хвостовой слэш в base_url — обычная опечатка пользователя, она не должна
    # давать «//search».
    res = asyncio.run(ai_web.search("q", "searxng", base_url="https://searx.example/"))

    assert len(calls) == 1
    assert calls[0].url.path == "/search"
    assert dict(calls[0].url.params) == {"q": "q", "format": "json"}
    assert [x["url"] for x in res["results"]] == [
        "https://docs.example.com/a", "https://example.org/b"]
    assert len(res["results"][0]["snippet"]) == ai_web.MAX_SNIPPET


def test_search_marks_results_as_untrusted(monkeypatch):
    calls = _wire(monkeypatch, lambda _r: httpx.Response(200, text=_DDG_LITE))
    res = asyncio.run(ai_web.search("вопрос", "duckduckgo"))
    assert len(calls) == 1 and "duckduckgo.com" in str(calls[0].url)
    assert len(res["results"]) == 2
    # Маркер обязан ехать в теле ответа: системный промпт остаётся далеко позади,
    # а граница доверия нужна модели рядом с самим текстом.
    assert res["note"] == ai_web.UNTRUSTED_NOTE


def test_search_network_failure_returns_an_error_not_an_exception(monkeypatch):
    def boom(_r):
        raise httpx.ConnectError("сеть недоступна")

    _wire(monkeypatch, boom)
    res = asyncio.run(ai_web.search("вопрос", "duckduckgo"))
    assert res["results"] == []
    assert "error" in res and "note" not in res


def test_search_clamps_count(monkeypatch):
    calls = _wire(monkeypatch, _json_ok({"web": {"results": []}}))
    asyncio.run(ai_web.search("q", "brave", key="k", count=99))
    assert calls[0].url.params["count"] == str(ai_web.MAX_RESULTS)

    # 0 — это «не указано» (`count or DEFAULT_RESULTS`), а не «ноль результатов».
    calls.clear()
    asyncio.run(ai_web.search("q", "brave", key="k", count=0))
    assert calls[0].url.params["count"] == str(ai_web.DEFAULT_RESULTS)


def test_search_rejects_an_empty_query_without_a_request(monkeypatch):
    calls = _wire(monkeypatch, _never)
    res = asyncio.run(ai_web.search("   ", "duckduckgo"))
    assert calls == []
    assert res["results"] == [] and res["error"] == "пустой запрос"


def test_search_falls_back_to_ddg_on_an_unknown_provider(monkeypatch):
    calls = _wire(monkeypatch, lambda _r: httpx.Response(200, text=_DDG_LITE))
    res = asyncio.run(ai_web.search("q", "не-существует"))
    assert "duckduckgo.com" in str(calls[0].url)
    assert len(res["results"]) == 2


# ── fetch ─────────────────────────────────────────────────────
def test_fetch_rejects_link_local_before_any_request(monkeypatch):
    calls = _wire(monkeypatch, _never)
    res = asyncio.run(ai_web.fetch("http://169.254.169.254/latest/meta-data/"))
    assert calls == []
    assert "SSRF" in res["error"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/settings",   # loopback — соседний процесс
    "http://10.1.2.3/",                     # private — соседний контейнер
    "file:///etc/passwd",                   # не http(s)
    "",                                     # пусто
])
def test_fetch_rejects_unroutable_targets(monkeypatch, url):
    calls = _wire(monkeypatch, _never)
    res = asyncio.run(ai_web.fetch(url))
    assert calls == []
    assert "error" in res


def test_fetch_rejects_a_redirect_into_a_private_host(monkeypatch):
    # Ради этого случая у fetch стоит follow_redirects=False: публичный хост
    # одним 302 увёл бы нас на метаданные облака, а автоследование сделало бы
    # проверку входного адреса бессмысленной.
    def redirect_to_imds(_r):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    calls = _wire(monkeypatch, redirect_to_imds)
    res = asyncio.run(ai_web.fetch("https://example.com/start"))

    assert len(calls) == 1, "второй запрос — уже в приватную сеть — уйти не должен"
    assert "SSRF" in res["error"]
    # Отвергнутый адрес назван в тексте ошибки: `url` в ответе остаётся
    # исходным (по нему пользователь и спрашивал), а понять, что его уводили на
    # метаданные облака, надо именно из сообщения.
    assert "169.254.169.254" in res["error"]
    assert res["url"] == "https://example.com/start"


def test_fetch_gives_up_after_too_many_redirects(monkeypatch):
    def hop(request):
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={
            "location": f"https://example.com/next?n={n + 1}"})

    calls = _wire(monkeypatch, hop)
    res = asyncio.run(ai_web.fetch("https://example.com/next?n=0"))
    assert res["error"] == "слишком много редиректов"
    assert len(calls) == 4, "цепочка обрывается на фиксированном числе прыжков"


def test_fetch_follows_a_redirect_between_public_hosts(monkeypatch):
    def hop(request):
        if request.url.host == "example.com":
            return httpx.Response(301, headers={"location": "https://docs.example.com/final"})
        return httpx.Response(200, text="итоговая страница")

    calls = _wire(monkeypatch, hop)
    res = asyncio.run(ai_web.fetch("https://example.com/old"))
    assert len(calls) == 2
    assert res["url"] == "https://docs.example.com/final"
    assert res["text"] == "итоговая страница"


def test_fetch_truncates_text_and_flags_it(monkeypatch):
    _wire(monkeypatch, lambda _r: httpx.Response(200, text="я" * 5000))
    res = asyncio.run(ai_web.fetch("https://example.com/big", max_chars=600))
    assert len(res["text"]) == 600
    assert res["truncated"] is True
    assert res["note"] == ai_web.UNTRUSTED_NOTE


def test_fetch_does_not_flag_short_pages(monkeypatch):
    _wire(monkeypatch, lambda _r: httpx.Response(200, text="коротко"))
    res = asyncio.run(ai_web.fetch("https://example.com/small"))
    assert res["truncated"] is False and res["text"] == "коротко"


def test_fetch_keeps_json_verbatim(monkeypatch):
    # JSON — это уже данные; прогон через html_to_text съел бы разметку внутри
    # значений и превратил бы валидный ответ API в кашу.
    _wire(monkeypatch, _json_ok({"note": "<b>жирный</b>", "n": 1}))
    res = asyncio.run(ai_web.fetch("https://example.com/api"))
    assert "<b>жирный</b>" in res["text"]
    assert res["title"] == ""
    assert res["content_type"] == "application/json"


def test_fetch_extracts_title_from_html(monkeypatch):
    _wire(monkeypatch, lambda _r: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"},
        content=_PAGE.encode("utf-8")))
    res = asyncio.run(ai_web.fetch("https://example.com/page"))
    assert res["title"] == "Тест страницы"
    assert res["content_type"] == "text/html"
    assert "var secret" not in res["text"]


def test_fetch_reports_http_errors(monkeypatch):
    _wire(monkeypatch, lambda _r: httpx.Response(404, text="нет такой"))
    res = asyncio.run(ai_web.fetch("https://example.com/gone"))
    assert res["error"] == "HTTP 404"


def test_fetch_reports_a_transport_failure(monkeypatch):
    def boom(_r):
        raise httpx.ReadTimeout("таймаут")

    _wire(monkeypatch, boom)
    res = asyncio.run(ai_web.fetch("https://example.com/slow"))
    assert "не удалось загрузить" in res["error"]


# ── регрессия: найденный ревью дефект, уже починенный ─────────
def test_search_does_not_follow_a_redirect_into_a_private_host(monkeypatch):
    """Раньше `search()` создавал клиент с `follow_redirects=True`, и SSRF-гард у
    searxng проверял ТОЛЬКО базовый адрес: одного 302 хватало, чтобы увести
    бэкенд на loopback или метаданные облака. Теперь переходы делает
    `_get_guarded`, проверяя каждый прыжок, — как в `fetch()`."""
    def redirect_to_loopback(request):
        if request.url.host == "searx.example":
            return httpx.Response(302, headers={
                "location": "http://127.0.0.1:8000/search?q=q&format=json"})
        return httpx.Response(200, json={"results": []})

    calls = _wire(monkeypatch, redirect_to_loopback)
    asyncio.run(ai_web.search("q", "searxng", base_url="https://searx.example"))

    assert len(calls) == 1
    assert all(c.url.host == "searx.example" for c in calls)
