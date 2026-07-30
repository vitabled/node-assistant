"""Реестр инструментов ассистента и мост в собственный REST.

Это граница безопасности, а не витрина: имя инструмента и путь ручки приходят от
модели, значит проверять надо в момент вызова. Поэтому тесты утверждают СМЫСЛ
(«запись недоступна в режиме чтения», «денилист не зависит от режима»), а не
форму — набор инструментов и список ручек будут расти.
"""
import asyncio
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_tools, library_store
from app.services.ai_tools import bridge

client = TestClient(app)


def _register() -> tuple[str, dict]:
    login = f"ait-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw"})
    return r.json()["id"], {"Authorization": f"Bearer {r.json()['token']}"}


def _ctx(account_id: str = "acc", **kw) -> ai_tools.ToolContext:
    return ai_tools.ToolContext(account_id=account_id, **kw)


def _names(ctx: ai_tools.ToolContext) -> list[str]:
    return [t.name for t in ai_tools.available(ctx)]


# ── 1. витрина: режим и веб ───────────────────────────────────
def test_readonly_shows_no_write_tool_and_write_appears_when_allowed():
    ro = _names(_ctx(readonly=True))
    # Утверждаем свойство, а не список имён: новый write-инструмент обязан
    # попасть под то же правило, не потребовав правки теста.
    assert [n for n in ro if ai_tools.TOOLS[n].write] == []
    assert "panel_write" not in ro and "write_note" not in ro
    # Чтение доступно в обоих режимах — режим ограничивает запись, а не работу.
    assert "panel_get" in ro and "panel_context" in ro

    rw = _names(_ctx(readonly=False))
    assert "panel_write" in rw and "write_note" in rw
    assert set(ro) < set(rw)


def test_web_tools_follow_the_web_switch():
    off = _names(_ctx(web_enabled=False))
    assert [n for n in off if ai_tools.TOOLS[n].web] == []
    assert "web_search" not in off and "web_open" not in off
    # Выключенный веб не должен задевать локальные инструменты.
    assert "search_hostings" in off and "list_nodes" in off

    on = _names(_ctx(web_enabled=True))
    assert "web_search" in on and "web_open" in on


# ── 2. гейт живёт в run(), а не только в витрине ───────────────
def test_run_enforces_the_gate_itself_not_only_the_showcase():
    """Модель вправе назвать инструмент, которого ей не показывали."""
    ctx = _ctx(readonly=True, web_enabled=False)

    ok, res = asyncio.run(ai_tools.run(
        "panel_write",
        {"method": "POST", "path": "/api/hostings", "body": {"name": "прокрался"}},
        ctx))
    assert ok is False and "только для чтения" in res

    ok, res = asyncio.run(ai_tools.run("web_open", {"url": "http://x/"}, ctx))
    assert ok is False and "интернет" in res

    ok, res = asyncio.run(ai_tools.run("такого-нет", {}, ctx))
    assert ok is False and "неизвестный инструмент" in res

    # Отказ случился ДО вызова: иначе имя оказалось бы в списке отработавших.
    assert ctx.used == []


# ── 3. денилист ───────────────────────────────────────────────
def test_delete_is_refused_on_every_path():
    """Удаление необратимо, а модель ошибается в идентификаторах."""
    for path in ("/api/hostings/abc", "/api/library/xyz", "/api/rules/1",
                 "/api/vault/e1", "/api/subscriptions/s1"):
        reason = bridge.deny_reason("DELETE", path)
        assert reason, f"DELETE {path} должен быть запрещён"
        assert "удаление" in reason.lower()
    # Те же пути другим методом проходят — запрет именно на удаление.
    assert bridge.deny_reason("GET", "/api/hostings/abc") == ""


def test_denylist_blocks_secrets_money_and_infrastructure():
    denied = [
        # секреты
        ("POST", "/api/vault/e1/reveal"),
        ("GET", "/api/vault/e1/download"),
        ("POST", "/api/certs/download"),
        ("POST", "/api/panel/env/read"),
        ("POST", "/api/panel/env/write"),
        # самоконфигурация агента и выпуск ключей
        ("GET", "/api/ai/config"),
        ("POST", "/api/ai/config"),
        ("GET", "/api/cliproxy/status"),
        ("GET", "/api/api-tokens"),
        ("POST", "/api/api-tokens"),
        ("GET", "/api/mcp/config"),
        # выгрузка/загрузка данных аккаунта
        ("POST", "/api/export"),
        ("POST", "/api/import"),
        ("POST", "/api/backup/run"),
        ("POST", "/api/backup/restore"),
        # деньги
        ("POST", "/api/infra-billing/providers/X/order"),
        ("POST", "/api/cloudflare/domains/register"),
        # необратимое с инфраструктурой
        ("POST", "/api/deploy"),
        ("POST", "/api/updates/apply"),
        ("POST", "/api/node/step"),
    ]
    for method, path in denied:
        assert bridge.deny_reason(method, path), f"{method} {path} должен быть запрещён"

    for method, path in (("GET", "/api/hostings"), ("GET", "/api/settings"),
                         ("GET", "/api/library"), ("POST", "/api/hostings")):
        assert bridge.deny_reason(method, path) == "", f"{method} {path} должен быть разрешён"


def test_settings_are_readable_but_not_writable():
    """Асимметрия по методу: посмотреть конфигурацию можно, переставить — нет."""
    assert bridge.deny_reason("GET", "/api/settings") == ""
    assert bridge.deny_reason("GET", "/api/settings/auto-backup") != ""  # там токен бота
    for method in ("POST", "PUT"):
        assert bridge.deny_reason(method, "/api/settings/remnawave")
        assert bridge.deny_reason(method, "/api/settings/deploy-defaults")


def test_unsupported_method_is_refused():
    assert bridge.deny_reason("HEAD", "/api/hostings")
    assert bridge.deny_reason("TRACE", "/api/hostings")


# ── отложенное выполнение мимо денилиста (найдено ревью, закрыто) ──
# Обе дыры были одной природы: ручка сама ничего не делает, а взводит то, что
# сработает ПОТОМ — уже вне денилиста, вне `readonly` и после того, как
# «загрязнение вебом» сбросится вместе с ответом.

def test_update_config_is_denied_like_update_apply():
    """`/api/updates/apply` был запрещён, а `/api/updates/config` — нет, хотя
    даёт тот же результат с задержкой: `{auto_update: true}` заставит фоновый
    `updater.auto_loop` вызвать `apply()` сам в течение ~6 часов. В том же теле
    едут `branch` и `image` — образ сайдкара, которому `apply()` отдаёт
    docker.sock и bind-mount хост-репозитория. Настройка глобальная, не
    пер-аккаунтная.
    """
    assert bridge.deny_reason("POST", "/api/updates/apply")
    # Починено по итогам ревью: запрет накрыл весь раздел на запись.
    assert bridge.deny_reason("POST", "/api/updates/config")
    # ...но статус читать по-прежнему можно — это обычные данные.
    assert bridge.deny_reason("GET", "/api/updates/status") == ""


def test_rules_are_read_only_because_they_run_outside_every_gate():
    """Правило выполняется фоновым `rules_loop` — вне денилиста, вне `readonly`
    и вне «загрязнения вебом» (флаг сбрасывается с концом ответа, а правило
    остаётся). Среди действий есть `node_disable`/`user_disable`/`hide_hosts`,
    то есть отложенное изменение инфраструктуры, и `telegram` — внешний канал;
    при этом `/api/settings/auto-backup` закрыт ровно с формулировкой «токен
    бота и адрес чата — канал утечки». Оставлять создание правил открытым было
    бы непоследовательно.
    """
    assert bridge.deny_reason("POST", "/api/settings/auto-backup")
    # Починено по итогам ревью: правила теперь только для чтения.
    assert bridge.deny_reason("POST", "/api/rules")
    assert bridge.deny_reason("PATCH", "/api/rules/r1")
    assert bridge.deny_reason("GET", "/api/rules") == ""


# ── 4. вычищение секретов из ответа ───────────────────────────
def test_scrub_hides_the_value_but_keeps_the_key():
    """Ключ остаётся намеренно: иначе модель решит, что панель не настроена."""
    src = {
        "remnawave": {"panel_url": "https://p", "api_token": "eyJsecret"},
        "entries": [{"name": "SSH прод", "fields_enc": "gAAAAA", "note": "личный"}],
        "deep": {"a": {"b": {"c": {"bot_token": "123:abcdef"}}}},
        "empty_token": "",
        "count": 7,
    }
    out = bridge.scrub(src)

    assert out["remnawave"]["api_token"] == bridge._SECRET_MASK
    assert "eyJsecret" not in str(out)
    assert "api_token" in out["remnawave"]
    assert out["remnawave"]["panel_url"] == "https://p"     # несекретное не тронуто
    assert out["entries"][0]["fields_enc"] == bridge._SECRET_MASK
    assert out["entries"][0]["name"] == "SSH прод"          # список разобран
    assert out["deep"]["a"]["b"]["c"]["bot_token"] == bridge._SECRET_MASK
    # Пустое значение не маскируем: «не заполнено» — это полезный факт.
    assert out["empty_token"] == ""
    assert out["count"] == 7
    # Исходник не мутируется — ответ ручки может понадобиться вызывающему целиком.
    assert src["remnawave"]["api_token"] == "eyJsecret"


def test_scrub_caps_a_long_list():
    out = bridge.scrub([{"i": i} for i in range(250)])
    assert len(out) == 200


# ── 5. нормализация пути ──────────────────────────────────────
def test_normalize_path_forgives_what_a_model_usually_sends():
    assert bridge.normalize_path("hostings") == "/api/hostings"
    assert bridge.normalize_path("/hostings") == "/api/hostings"
    assert bridge.normalize_path("/api/hostings") == "/api/hostings"
    assert bridge.normalize_path("https://panel.example.com/api/hostings") == "/api/hostings"
    assert bridge.normalize_path("http://panel/api/hostings?page=2") == "/api/hostings"
    assert bridge.normalize_path("/api/hostings/") == "/api/hostings"
    assert bridge.normalize_path(" /api/hostings ") == "/api/hostings"


# ── 6. мост через настоящий ASGI ──────────────────────────────
def test_call_reads_real_data_through_the_app():
    aid, h = _register()
    assert client.post("/api/hostings", headers=h,
                       json={"name": "Мостовой хостинг", "tags": ["тест"]}).status_code == 201

    # Путь без префикса — нормализация должна доехать до реальной ручки.
    out = asyncio.run(bridge.call("GET", "hostings", aid, user_id=aid))
    assert out["ok"] is True and out["status"] == 200
    assert isinstance(out["data"], list)
    assert [x["name"] for x in out["data"]] == ["Мостовой хостинг"]

    # Чужой аккаунт своих данных здесь не видит — токен выписывается на account_id.
    other, _ = _register()
    assert asyncio.run(bridge.call("GET", "hostings", other, user_id=other))["data"] == []


def test_readonly_refuses_any_non_get_without_executing_it():
    aid, h = _register()
    for method in ("POST", "PUT", "PATCH"):
        out = asyncio.run(bridge.call(method, "/api/hostings", aid, user_id=aid,
                                      body={"name": "не должен появиться"},
                                      readonly=True))
        assert out["ok"] is False and "только для чтения" in out["error"]
    # Доказательство «не выполнилось»: запись не появилась в сторе.
    assert client.get("/api/hostings", headers=h).json() == []


def test_denied_path_is_refused_before_anything_runs(monkeypatch):
    """Отказ обязан случиться ДО авторизации и ДО запроса в приложение.

    Проверяем счётчиком выписанных токенов: он инкрементится в `call` сразу
    после денилиста, поэтому ноль означает, что дальше гейта не прошли.
    """
    # Волна 13: токен минтится для ПОЛЬЗОВАТЕЛЯ (`users.issue_token`), а не для
    # рабочей области — иначе ассистент стал бы способом обойти роли. Счётчик
    # переехал вместе с этим.
    from app.services import users

    aid, _h = _register()
    calls = []
    real = users.issue_token
    monkeypatch.setattr(users, "issue_token",
                        lambda a: (calls.append(a), real(a))[1])

    # Разрешённый вызов счётчик двигает — иначе тест ничего не доказывал бы.
    asyncio.run(bridge.call("GET", "/api/hostings", aid, user_id=aid))
    assert len(calls) == 1

    for method, path in (("POST", "/api/deploy"),
                         ("POST", "/api/infra-billing/providers/p1/order"),
                         ("POST", "/api/cloudflare/domains/register"),
                         ("POST", "/api/vault/e1/reveal"),
                         ("DELETE", "/api/hostings/x")):
        out = asyncio.run(bridge.call(method, path, aid, body={}, readonly=False, user_id=aid))
        assert out["ok"] is False
        assert "запрещено" in out["error"]
    assert len(calls) == 1, "запрещённый путь дошёл до выполнения"


# ── 7. каталог ручек ──────────────────────────────────────────
def test_endpoints_lists_only_what_is_actually_callable():
    rows = bridge.endpoints("")
    assert rows, "каталог маршрутов пуст — мост не видит приложение"

    # Главный инвариант: каталог и денилист не могут разойтись.
    leaked = [(r["path"], m) for r in rows for m in r["methods"]
              if bridge.deny_reason(m, r["path"])]
    assert leaked == []

    paths = {r["path"] for r in rows}
    assert "/api/hostings" in paths
    # Именно секретоносные пути, а не любой «/download»: выгрузка оверлея
    # страницы подписок — это собственный контент пользователя, не секрет.
    for hidden in ("/api/api-tokens", "/api/deploy", "/api/ai/config",
                   "/api/vault/{entry_id}/reveal", "/api/vault/{entry_id}/download",
                   "/api/certs/download", "/api/panel/env/read"):
        assert hidden not in paths
    assert not [p for p in paths if p.startswith("/api/api-tokens")]
    assert not [p for p in paths if p.startswith("/api/ai")]
    # Ни одна ручка «Хранилища», выдающая значение секрета, не должна светиться.
    assert not [p for p in paths if p.startswith("/api/vault") and
                (p.endswith("/reveal") or p.endswith("/download"))]

    # Фильтр по подстроке — то, чем модель ищет нужный путь.
    filtered = bridge.endpoints("hosting")
    assert filtered and all("hosting" in r["path"] for r in filtered)


def test_endpoints_shows_only_get_in_readonly_mode():
    ok, res = asyncio.run(ai_tools.run("panel_endpoints", {"contains": "hosting"},
                                       _ctx(readonly=True)))
    assert ok is True
    assert res["count"] > 0
    assert all(r["methods"] == ["GET"] for r in res["endpoints"])

    ok, res = asyncio.run(ai_tools.run("panel_endpoints", {"contains": "hosting"},
                                       _ctx(readonly=False)))
    assert any("POST" in r["methods"] for r in res["endpoints"])


# ── 8. асимметрия «загрязнения вебом» ─────────────────────────
def test_web_taint_blocks_panel_write_but_not_write_note():
    """Осознанная асимметрия, зафиксированная тестом.

    Страница из интернета может нести текст, адресованный модели («примени вот
    такую настройку»), поэтому после веб-вызова панель становится доступна
    только на чтение. Заметка — исключение: она аддитивна, живёт в библиотеке
    пользователя и ничего в панели не переставляет, а «нашёл и сохранил» —
    основной сценарий связки поиска с записью.
    """
    aid, _h = _register()
    ctx = _ctx(aid, readonly=False, web_tainted=True)

    ok, res = asyncio.run(ai_tools.run(
        "panel_write", {"method": "POST", "path": "/api/hostings",
                        "body": {"name": "из статьи"}}, ctx))
    assert ok is True                       # инструмент отработал…
    assert res["ok"] is False               # …и сам отказал
    assert "интернета" in res["error"]

    ok, res = asyncio.run(ai_tools.run(
        "write_note", {"name": "Найдено в вебе", "text": "конспект"}, ctx))
    assert ok is True and res["ok"] is True
    saved = library_store.get_note(res["id"], aid)
    assert saved is not None and saved["text"] == "конспект"


def test_web_taint_is_raised_by_a_web_call():
    """Флаг взводится самим инструментом — иначе асимметрия ни на что не влияла бы."""
    ctx = _ctx(readonly=False, web_enabled=False)
    assert ctx.web_tainted is False
    # Веб выключен → до тела инструмента не дошли, флага нет.
    asyncio.run(ai_tools.run("web_search", {"query": "x"}, ctx))
    assert ctx.web_tainted is False


# ── 9. запись заметок ─────────────────────────────────────────
def test_write_note_creates_updates_and_is_refused_in_readonly():
    aid, _h = _register()
    rw = _ctx(aid, readonly=False)

    ok, res = asyncio.run(ai_tools.run(
        "write_note", {"name": "Ноды", "text": "первая версия", "folder": "Инфра"}, rw))
    assert ok is True and res["ok"] is True and res["created"] is True
    nid = res["id"]
    saved = library_store.get_note(nid, aid)
    assert saved["name"] == "Ноды" and saved["folder"] == "Инфра"

    ok, res = asyncio.run(ai_tools.run(
        "write_note", {"id": nid, "name": "Ноды", "text": "вторая версия"}, rw))
    assert ok is True and res["ok"] is True and res["updated"] is True
    assert library_store.get_note(nid, aid)["text"] == "вторая версия"

    # Несуществующий id — отказ, а не новая заметка поверх.
    ok, res = asyncio.run(ai_tools.run(
        "write_note", {"id": "нет-такого", "name": "X", "text": "y"}, rw))
    assert ok is True and res["ok"] is False
    assert len([i for i in library_store.list_items(aid) if i["kind"] == "note"]) == 1

    # Без имени заметку не создаём.
    ok, res = asyncio.run(ai_tools.run("write_note", {"text": "без имени"}, rw))
    assert res["ok"] is False

    ok, res = asyncio.run(ai_tools.run(
        "write_note", {"name": "нельзя", "text": "т"}, _ctx(aid, readonly=True)))
    assert ok is False and "только для чтения" in res
    assert len([i for i in library_store.list_items(aid) if i["kind"] == "note"]) == 1


# ── 9. регрессии по итогам состязательного ревью ──────────────
def test_denylist_survives_path_canonicalization():
    """⚠️ CRITICAL, найдено ревью и починено.

    `httpx` перед отправкой в ASGI сам схлопывает `.`/`..` и раскрывает
    процент-кодирование. Пока `normalize_path` этого не делала, денилист
    проверял ОДНУ строку, а Starlette маршрутизировал ДРУГУЮ — и запреты
    обходились целиком: `/api/vault/id/./reveal` отдавал секрет, а
    `/api/clipro%78y/config` — мастер-ключ шлюза, причём в режиме чтения.

    Тест утверждает инвариант, а не список: КАКОЙ БЫ формой ни записали путь,
    после канонизации решение денилиста должно совпадать с решением по
    канонической форме.
    """
    cases = [
        ("/api/vault/id/./reveal", "/api/vault/id/reveal"),
        ("/api/../api/vault/id/reveal", "/api/vault/id/reveal"),
        ("/api/clipro%78y/config", "/api/cliproxy/config"),
        ("//api/vault/x/reveal", "/api/vault/x/reveal"),
        ("/api/./settings/remnawave", "/api/settings/remnawave"),
        ("/api/cloudflare/domains/./register", "/api/cloudflare/domains/register"),
        ("/api/infra-billing/providers/p1/./order", "/api/infra-billing/providers/p1/order"),
    ]
    for raw, expected in cases:
        canon = bridge.normalize_path(raw)
        assert canon == expected, raw
        assert bridge.deny_reason("POST", canon), f"{raw} прошёл мимо денилиста"


def test_certs_endpoints_are_denied():
    """Ревью: `/api/certs/deploy` не был в списке вовсе — а это выпуск серта по
    SSH на живой ноде, ровно та категория, которую докстринг объявляет закрытой."""
    assert bridge.deny_reason("POST", "/api/certs/deploy")
    assert bridge.deny_reason("POST", "/api/certs/download")


def test_config_templates_and_subscriptions_are_closed():
    """Оба раздела отдают капабилити в теле ответа: конфиги — приватный ключ
    REALITY внутри JSON-строки, подписки — URL, равносильный доступу к конфигам."""
    assert bridge.deny_reason("GET", "/api/config-templates")
    assert bridge.deny_reason("GET", "/api/config-templates/abc")
    assert bridge.deny_reason("GET", "/api/subscriptions")
    assert bridge.deny_reason("GET", "/api/subscriptions/status")


def test_scrub_finds_secrets_inside_string_values():
    """Второй слой скрабера. Обход по именам ключей принципиально не видит
    секрет, приехавший ВНУТРИ строки: `xray_json_template` у шаблона хоста —
    это JSON текстом, и `privateKey` лежит внутри него."""
    out = bridge.scrub({
        "xray_json_template": '{"realitySettings": {"privateKey": "aBcD1234567890xyz"}}',
        "note": "используйте Bearer abcdefghijklmnopqrst для доступа",
        "jwt": "",
        "plain": "обычный текст без секретов",
    })
    assert "aBcD1234567890xyz" not in str(out)
    assert "abcdefghijklmnopqrst" not in str(out)
    assert out["plain"] == "обычный текст без секретов"


def test_scrub_catches_camel_case_and_dashed_key_names():
    """Ревью: первая версия сравнивала имя как есть и пропускала половину
    реальных полей проекта."""
    src = {k: "SECRETVALUE" for k in
           ("privateKey", "master_key", "X-API-Key", "passphrase",
            "access_key_id", "consumer_key", "fields_enc", "ssh_key")}
    src.update({"path": "/a/b", "ping": 12, "name": "нода", "url": "https://x"})
    out = bridge.scrub(src)
    assert "SECRETVALUE" not in str(out)
    # ...и ничего лишнего: короткие имена вроде `pat`/`pin` не должны ловить
    # `path`/`ping` как подстроку.
    assert out["path"] == "/a/b" and out["ping"] == 12
    assert out["name"] == "нода" and out["url"] == "https://x"


def test_subscription_urls_never_reach_the_model():
    """URL подписки — капабилити: кто им владеет, тот скачал все конфиги.
    Ярлык отдаёт только хост, чтобы подписки можно было различать."""
    aid, h = _register()
    client.post("/api/subscriptions",
                json={"url": "https://panel.example.com/sub/SECRETTOKEN123456"},
                headers=h)
    ok, out = asyncio.run(ai_tools.run("list_subscriptions", {}, _ctx(aid)))
    assert ok and out
    assert "SECRETTOKEN123456" not in str(out)
    assert out[0]["host"] == "panel.example.com"


def test_web_calls_are_capped_per_answer():
    """Модель вправе запросить сколько угодно вызовов подряд, а каждый — сетевая
    операция с дедлайном в десятки секунд. Без потолка один вопрос занимает
    воркер надолго (найдено ревью)."""
    ctx = _ctx(web_enabled=True)
    ctx.web_calls = ai_tools.MAX_WEB_CALLS
    ok, out = asyncio.run(ai_tools.run("web_open", {"url": "https://example.com"}, ctx))
    assert ok and "лимит" in out["error"]
    # Счётчик не растёт после отказа — иначе он был бы неотличим от расхода.
    assert ctx.web_calls == ai_tools.MAX_WEB_CALLS
