"""Wave-5 Plan J — CLIProxyAPI gateway mode: SSRF exemption for the internal
gateway container, graceful list_models, config wiring."""
import asyncio
import uuid

import pytest

from fastapi.testclient import TestClient

from app.main import app
from app.models.settings import AiConfig
from app.services import ai_agent

client = TestClient(app)


def _auth():
    r = client.post("/api/auth/register", json={"login": f"g-{uuid.uuid4().hex[:8]}", "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_internal_gateway_exempt_from_ssrf():
    cfg = AiConfig(gateway="cliproxy", gateway_internal=True,
                   base_url="http://node-installer-cliproxy:8317/v1")
    ai_agent._check_base_url(cfg)  # exempt → does not raise


def test_external_private_gateway_still_blocked():
    # gateway=cliproxy but NOT internal, private host → SSRF guard blocks
    cfg = AiConfig(gateway="cliproxy", gateway_internal=False, base_url="http://127.0.0.1:8317/v1")
    with pytest.raises(ai_agent.AgentError):
        ai_agent._check_base_url(cfg)
    # internal flag but non-container host → still checked (private → blocked)
    cfg2 = AiConfig(gateway="cliproxy", gateway_internal=True, base_url="http://127.0.0.1:8317/v1")
    with pytest.raises(ai_agent.AgentError):
        ai_agent._check_base_url(cfg2)


def test_list_models_graceful_on_blocked():
    cfg = AiConfig(gateway="cliproxy", gateway_internal=False, base_url="http://127.0.0.1/v1")
    assert asyncio.run(ai_agent.list_models(cfg, "k")) == []


def test_config_gateway_roundtrip_and_validation():
    h = _auth()
    # default → none, models endpoint empty
    assert client.get("/api/ai/config", headers=h).json()["gateway"] == "none"
    assert client.get("/api/ai/models", headers=h).json() == {"models": []}
    # set cliproxy
    r = client.post("/api/ai/config", headers=h, json={"enabled": True, "provider": "openai", "gateway": "cliproxy"})
    assert r.status_code == 200 and r.json()["gateway"] == "cliproxy"
    # bad gateway → 422
    assert client.post("/api/ai/config", headers=h, json={"gateway": "bogus"}).status_code == 422


# ── Волна 6, План C Ф2: каталог моделей разгейчен ──

def test_list_models_makes_no_network_call_without_a_key(monkeypatch):
    """Свежий аккаунт открывает вкладку настроек — запрос без ключа всё равно
    вернул бы 401, поэтому сети быть не должно вовсе."""
    def boom(*a, **k):
        raise AssertionError("сеть не должна трогаться без ключа")
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", boom)
    cfg = AiConfig(base_url="https://api.openai.com/v1")
    assert asyncio.run(ai_agent.list_models(cfg, "")) == []


class _FakeResp:
    status_code = 200
    def json(self):
        return {"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]}


class _RecordingClient:
    """Перехватывает заголовки одного GET, не выходя в сеть."""
    seen: dict = {}

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, headers=None):
        _RecordingClient.seen = {"url": url, "headers": headers or {}}
        return _FakeResp()


def test_list_models_uses_anthropic_headers(monkeypatch):
    """Anthropic не понимает Bearer: нужны x-api-key + обязательная версия API."""
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    cfg = AiConfig(provider="anthropic", base_url="https://api.anthropic.com/v1")
    out = asyncio.run(ai_agent.list_models(cfg, "sk-ant"))
    assert out == ["claude-opus-4-8", "claude-haiku-4-5"]
    h = _RecordingClient.seen["headers"]
    assert h["x-api-key"] == "sk-ant"
    assert h["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in h
    assert _RecordingClient.seen["url"] == "https://api.anthropic.com/v1/models"


def test_list_models_uses_bearer_for_openai_compatible(monkeypatch):
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    cfg = AiConfig(provider="openai", base_url="https://api.openai.com/v1")
    asyncio.run(ai_agent.list_models(cfg, "sk-oai"))
    h = _RecordingClient.seen["headers"]
    assert h["Authorization"] == "Bearer sk-oai"
    assert "x-api-key" not in h


def test_models_endpoint_no_longer_gated_on_gateway(monkeypatch):
    """Гейт `gateway != cliproxy → []` снят: прямой провайдер тоже отдаёт каталог."""
    monkeypatch.setattr(ai_agent.httpx, "AsyncClient", _RecordingClient)
    h = _auth()
    client.post("/api/ai/config", headers=h,
                json={"enabled": True, "provider": "openai", "gateway": "none",
                      "base_url": "https://api.openai.com/v1", "api_key": "sk-direct"})
    assert client.get("/api/ai/models", headers=h).json() == {
        "models": ["claude-opus-4-8", "claude-haiku-4-5"]
    }


# ── Через шлюз провайдерский ключ не нужен ────────────────────
def test_gateway_supplies_the_key_instead_of_the_provider_api_key():
    """Регрессия: после успешного OAuth-входа ассистент всё равно требовал
    API-ключ, потому что `run_agent` смотрел только на `api_key_enc`."""
    from app.models.settings import AiConfig
    from app.services import ai_agent, cliproxy_server

    cfg = AiConfig(gateway="cliproxy", cliproxy_enabled=True, api_key_enc="",
                   cliproxy_master_key_enc=cliproxy_server.encrypt("master-xyz"))
    eff, key = ai_agent.effective_target(cfg)

    assert key == "master-xyz", "авторизуемся мастер-ключом шлюза"
    # `/v1` обязателен: тёрны собирают `{base_url}/chat/completions`.
    assert eff.base_url == cliproxy_server.internal_base_url().rstrip("/") + "/v1"


def test_without_a_gateway_nothing_changes():
    from app.models.settings import AiConfig
    from app.services import ai_agent

    cfg = AiConfig(gateway="none", api_key_enc=ai_agent.encrypt_key("sk-plain"))
    eff, key = ai_agent.effective_target(cfg)
    assert key == "sk-plain" and eff.base_url == cfg.base_url

    assert ai_agent.effective_target(AiConfig(gateway="none", api_key_enc=""))[1] == ""


def test_external_gateway_falls_back_to_the_users_key():
    """Чужой шлюз пускает по своему ключу — его кладут в поле API-ключа."""
    from app.models.settings import AiConfig
    from app.services import ai_agent

    cfg = AiConfig(gateway="cliproxy", cliproxy_enabled=False, gateway_internal=False,
                   base_url="https://gw.example.com/v1",
                   api_key_enc=ai_agent.encrypt_key("client-key"))
    eff, key = ai_agent.effective_target(cfg)
    assert key == "client-key"
    assert eff.base_url == "https://gw.example.com/v1", "внешний адрес не подменяем"


def test_external_gateway_never_gets_the_local_master_key():
    """Мастер-ключ действителен ТОЛЬКО против нашего контейнера.

    Он засеян в его `config.yaml`, и больше нигде не значит ничего. Прежний
    порядок выбирал мастер-ключ всегда, когда тот сохранён, а ключ пользователя
    брал лишь как фолбэк на пустой мастер-ключ. Достаточно было один раз тронуть
    локальный шлюз (`ensure_keys` генерит ключ), а потом выключить контейнер —
    и наш случайный токен уезжал на публичный `base_url`, откуда возвращалось
    «провайдер отклонил ключ», хотя ключ пользователя был верный и просто не
    доехал.
    """
    from app.models.settings import AiConfig
    from app.services import ai_agent, cliproxy_server

    cfg = AiConfig(gateway="cliproxy", cliproxy_enabled=False, gateway_internal=False,
                   base_url="https://api.openai.com/v1",
                   api_key_enc=ai_agent.encrypt_key("sk-настоящий"),
                   cliproxy_master_key_enc=cliproxy_server.encrypt("master-локальный"))
    eff, key = ai_agent.effective_target(cfg)

    assert key == "sk-настоящий", "внешнему адресу — ключ пользователя"
    assert key != "master-локальный"
    assert eff.base_url == "https://api.openai.com/v1"


def test_local_gateway_still_prefers_the_master_key():
    """Обратная сторона того же правила: для НАШЕГО контейнера мастер-ключ
    выигрывает у ключа из формы, иначе после OAuth-входа ассистент снова
    требовал бы провайдерский ключ."""
    from app.models.settings import AiConfig
    from app.services import ai_agent, cliproxy_server

    cfg = AiConfig(gateway="cliproxy", cliproxy_enabled=True,
                   api_key_enc=ai_agent.encrypt_key("sk-неважно"),
                   cliproxy_master_key_enc=cliproxy_server.encrypt("master-локальный"))
    eff, key = ai_agent.effective_target(cfg)

    assert key == "master-локальный"
    assert eff.base_url.endswith("/v1")


def test_401_message_names_the_host_that_refused():
    """Сообщение обязано отличать «ключ неверный» от «ключ уехал не туда»."""
    import httpx

    from app.services import ai_agent

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(401, json={"error": {"message": "invalid_api_key"}},
                          request=req)
    msg = ai_agent._provider_error(resp, "sk-секрет")

    assert "api.openai.com" in msg
    assert "sk-секрет" not in msg, "ключ в текст ошибки не попадает"


def test_base_url_follows_the_provider():
    """Смена провайдера меняет адрес назначения — иначе ключ уезжает не туда."""
    from app.models.settings import AiConfig, PROVIDER_BASE_URLS
    from app.services import ai_agent

    cfg = AiConfig(provider="anthropic", base_url="https://api.openai.com/v1",
                   api_key_enc=ai_agent.encrypt_key("k"))
    # Прежний адрес был от OpenAI, но провайдер уже anthropic.
    assert cfg.base_url_auto is True
    assert cfg.effective_base_url() == PROVIDER_BASE_URLS["anthropic"]
    eff, _key = ai_agent.effective_target(cfg)
    assert eff.base_url == PROVIDER_BASE_URLS["anthropic"]


def test_custom_endpoint_survives_the_upgrade():
    """⚠️ Поле появилось позже настройки: у существующих установок его в
    settings.json нет, и умолчание «авто» молча переключило бы OpenRouter на
    api.openai.com. Режим выводится из адреса, когда он не задан явно."""
    from app.models.settings import AiConfig

    custom = AiConfig(provider="openai", base_url="https://openrouter.ai/api/v1")
    assert custom.base_url_auto is False, "чужой адрес → ручной режим"
    assert custom.effective_base_url() == "https://openrouter.ai/api/v1"

    # Явное указание сильнее вывода по адресу.
    forced = AiConfig(provider="openai", base_url="https://openrouter.ai/api/v1",
                      base_url_auto=True)
    assert forced.effective_base_url() == "https://api.openai.com/v1"

    # Штатный адрес и пустой — авторежим.
    assert AiConfig(base_url="https://api.openai.com/v1").base_url_auto is True
    assert AiConfig(base_url="").base_url_auto is True


def test_anthropic_output_cap_comes_from_config_not_a_hardcoded_1024():
    """⚠️ Регрессия: потолок вывода был зашит в 1024, и тело одной карточки
    хостинга с тарифами не помещалось — ответ обрывался посреди JSON, разбирать
    было нечего, и в чат приходил пустой пузырь."""
    import asyncio

    import httpx

    from app.models.settings import AiConfig
    from app.services import ai_agent

    seen = {}

    class _Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            seen.update(json or {})
            return httpx.Response(200, json={"content": [{"type": "text", "text": "ок"}],
                                             "usage": {"input_tokens": 1, "output_tokens": 2},
                                             "stop_reason": "end_turn"},
                                  request=httpx.Request("POST", url))

    import app.services.ai_agent as mod
    real = mod.httpx.AsyncClient
    mod.httpx.AsyncClient = _Client
    try:
        cfg = AiConfig(provider="anthropic", max_tokens=8192,
                       api_key_enc=ai_agent.encrypt_key("k"))
        asyncio.run(mod._anthropic_turn(cfg, "k", [{"role": "user", "content": "x"}],
                                        with_tools=False))
    finally:
        mod.httpx.AsyncClient = real
    assert seen["max_tokens"] == 8192


def test_truncated_answer_is_explained_instead_of_looking_empty():
    """Обрыв по потолку вывода — самая частая причина пустоты, и её надо назвать
    отдельно от «модель промолчала»: чинится она другой настройкой."""
    import asyncio

    from app.models.settings import AiConfig
    from app.services import ai_agent, users

    u = users.list_users()[0] if users.list_users() else None
    if u is None:
        return
    async def cut(config, key, messages, with_tools=True, system="", mcp=None, ctx=None):
        return {"text": "", "tool_calls": [], "raw": {}, "usage": 5, "stop": "max_tokens"}

    real = ai_agent._provider_turn
    ai_agent._provider_turn = cut
    try:
        cfg = AiConfig(enabled=True, api_key_enc=ai_agent.encrypt_key("k"),
                       max_steps=2, max_tokens=8192)

        async def main():
            return [e async for e in ai_agent.run_agent("x", cfg, u["workspace_id"])]

        events = asyncio.run(main())
    finally:
        ai_agent._provider_turn = real
    texts = [e["delta"] for e in events if e["type"] == "text"]
    assert texts and "лимит вывода" in texts[0] and "8192" in texts[0]


def test_provider_5xx_is_retried_and_4xx_is_not():
    """⚠️ Один 500 убивал ВЕСЬ ответ вместе с проделанной работой: полтора
    десятка вызовов, полфайла прочитано — и всё из-за секундной неполадки на
    чужой стороне. 5xx и обрыв связи преходящи, тёрн побочных эффектов не имеет.
    А вот 4xx (неверный ключ, неизвестная модель) сам не починится — повтор там
    только утроил бы задержку перед той же ошибкой."""
    import asyncio

    import httpx

    import app.services.ai_agent as mod

    calls = {"n": 0}

    def _mk(status: int):
        def client(**kw):
            class C:
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                async def post(self, url, json=None, headers=None):
                    calls["n"] += 1
                    # Первые две попытки — 500, третья удачная.
                    code = status if calls["n"] < 3 else 200
                    return httpx.Response(code, json={"ok": True},
                                          request=httpx.Request("POST", url))
            return C()
        return client

    real_client, real_delays = mod.httpx.AsyncClient, mod._RETRY_DELAYS
    mod._RETRY_DELAYS = (0, 0)   # не ждём в тесте
    try:
        mod.httpx.AsyncClient = _mk(500)
        r = asyncio.run(mod._post_retrying("http://x", json_body={}, headers={}, key="k"))
        assert r.status_code == 200 and calls["n"] == 3, "повторили и добились ответа"

        # 4xx возвращается СРАЗУ, без повторов.
        calls["n"] = 0
        def client4(**kw):
            class C:
                async def __aenter__(self): return self
                async def __aexit__(self, *a): return False
                async def post(self, url, json=None, headers=None):
                    calls["n"] += 1
                    return httpx.Response(401, json={}, request=httpx.Request("POST", url))
            return C()
        mod.httpx.AsyncClient = client4
        r = asyncio.run(mod._post_retrying("http://x", json_body={}, headers={}, key="k"))
        assert r.status_code == 401 and calls["n"] == 1
    finally:
        mod.httpx.AsyncClient, mod._RETRY_DELAYS = real_client, real_delays


def test_giving_up_says_the_work_is_not_lost():
    """Сообщение обязано подсказать действие: «Продолжи» подхватит файл и
    контекст, а не начнёт всё заново."""
    import asyncio

    import httpx

    import app.services.ai_agent as mod

    def client(**kw):
        class C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                return httpx.Response(503, json={}, request=httpx.Request("POST", url))
        return C()

    real_client, real_delays = mod.httpx.AsyncClient, mod._RETRY_DELAYS
    mod._RETRY_DELAYS = (0, 0)
    mod.httpx.AsyncClient = client
    try:
        try:
            asyncio.run(mod._post_retrying("http://x", json_body={}, headers={}, key="k"))
            assert False, "должно было бросить"
        except mod.AgentError as exc:
            assert "Продолжи" in str(exc) and "не потеряно" in str(exc)
    finally:
        mod.httpx.AsyncClient, mod._RETRY_DELAYS = real_client, real_delays


def test_retry_shrinks_the_request_and_names_a_silent_exception():
    """⚠️ Две беды одного сбоя. Первая: у таймаутов httpx текст ПУСТОЙ, и
    сообщение вырождалось в «Провайдер недоступен: » — из него нечего понять.
    Вторая: повтор тем же телом бесполезен, если провайдер споткнулся на объёме,
    поэтому каждая попытка режет историю вдвое."""
    import asyncio

    import httpx

    import app.services.ai_agent as mod

    # Пустое исключение обязано назвать хотя бы свой тип.
    assert mod._describe_exc(httpx.ReadTimeout("")) == "ReadTimeout"
    assert "боль" in mod._describe_exc(RuntimeError("боль"))

    # Подключение падает быстро, чтение — долго: генерация длинного тела идёт
    # минутами, и общий таймаут обрывал живой ответ.
    assert mod._TIMEOUT.connect <= 30 < mod._TIMEOUT.read

    seen: list[int] = []
    body = {"messages": [{"role": "assistant"}]
            + [{"role": "tool", "content": "x" * 30_000} for _ in range(8)]}

    def size() -> int:
        return sum(len(m.get("content") or "") for m in body["messages"]
                   if m.get("role") == "tool" and m.get("content") != mod._EVICTED)

    def client(**kw):
        class C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                seen.append(size())
                if len(seen) < 3:
                    raise httpx.ReadTimeout("")
                return httpx.Response(200, json={}, request=httpx.Request("POST", url))
        return C()

    real_client, real_delays = mod.httpx.AsyncClient, mod._RETRY_DELAYS
    mod._RETRY_DELAYS = (0, 0)
    mod.httpx.AsyncClient = client
    try:
        def shrink(attempt: int) -> None:
            mod._trim_history(body["messages"],
                              mod._HISTORY_RESULT_BUDGET // (2 ** attempt))

        r = asyncio.run(mod._post_retrying("http://x", json_body=body, headers={},
                                           key="k", shrink=shrink))
    finally:
        mod.httpx.AsyncClient, mod._RETRY_DELAYS = real_client, real_delays

    assert r.status_code == 200
    assert len(seen) == 3
    assert seen[0] > seen[1] > seen[2], "каждая попытка отправляет меньше прежней"
