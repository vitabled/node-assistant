"""Живая сводка по аккаунту, история диалога и новые поля конфигурации ассистента.

Главный тест файла — `test_snapshot_never_carries_a_secret`: сводка уезжает в
КАЖДОМ запросе к чужому LLM-эндпоинту, поэтому всё, что в неё попало, считается
разглашённым.
"""
import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_agent, ai_context, storage

client = TestClient(app)


def _register() -> tuple[str, dict]:
    login = f"aic-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register", json={"login": login, "password": "pw"})
    return r.json()["id"], {"Authorization": f"Bearer {r.json()['token']}"}


# ── сводка ────────────────────────────────────────────────────
def test_snapshot_of_an_empty_account_is_all_zeros_and_never_raises():
    aid, _h = _register()
    snap = asyncio.run(ai_context.snapshot(aid))

    assert snap["hostings"] == {"total": 0, "tariffs": 0}
    assert snap["library"] == {"notes": 0, "files": 0, "folders": 0}
    assert snap["rules"] == {"total": 0, "enabled": 0}
    assert snap["vault"]["total"] == 0
    assert snap["remnawave"]["configured"] is False
    assert snap["today"]
    # Рендер пустой сводки — обычный текст, а не пустая строка: «ничего нет» это
    # тоже полезный ответ модели.
    assert "Remnawave" in ai_context.render(snap)


def test_snapshot_counts_grow_with_real_data():
    aid, h = _register()
    client.post("/api/hostings",
                json={"name": "Хостер", "tariffs": [{"name": "S", "price": 5}]},
                headers=h)
    client.post("/api/library/notes", json={"name": "Заметка", "text": "тело"},
                headers=h)
    client.post("/api/vault", json={"name": "SSH", "kind": "ssh_password",
                                    "fields": {"password": "s3cr3t"}}, headers=h)

    snap = asyncio.run(ai_context.snapshot(aid))
    assert snap["hostings"] == {"total": 1, "tariffs": 1}
    assert snap["library"]["notes"] == 1
    assert snap["vault"]["total"] == 1
    assert snap["vault"]["by_kind"] == {"ssh_password": 1}


def test_snapshot_never_carries_a_secret():
    """Сводка — только количества и флаги.

    Ни токена панели, ни имени записи хранилища, ни значения секрета: этот блок
    уходит в каждый запрос к стороннему провайдеру.
    """
    aid, h = _register()
    client.post("/api/settings/remnawave",
                json={"panel_url": "https://panel.example.com",
                      "api_token": "SUPERSECRETPANELTOKEN"}, headers=h)
    client.post("/api/vault", json={"name": "Прод-рут", "kind": "ssh_password",
                                    "resource": "10.0.0.1",
                                    "fields": {"password": "PLAINTEXTPW"}}, headers=h)

    snap = asyncio.run(ai_context.snapshot(aid))
    blob = json.dumps(snap, ensure_ascii=False) + "\n" + ai_context.render(snap)

    for secret in ("SUPERSECRETPANELTOKEN", "PLAINTEXTPW", "Прод-рут", "10.0.0.1",
                   "panel.example.com"):
        assert secret not in blob, secret
    # ...но факт «панель настроена» остаётся: без него модель начнёт советовать
    # настроить уже настроенное.
    assert snap["remnawave"]["configured"] is True


def test_render_survives_an_incomplete_snapshot():
    """Каждый пробник в `snapshot` падает независимо, поэтому рендер обязан
    переваривать сводку, в которой чего-то нет вовсе."""
    assert ai_context.render({}).strip()
    assert "0" in ai_context.render({"today": "2026-01-01"})


def test_build_returns_empty_string_when_everything_is_broken(monkeypatch):
    """Контекст полезен, но не обязателен: сломанный стор не должен оставлять
    пользователя без ответа."""
    async def boom(_aid):
        raise RuntimeError("стор сломан")

    monkeypatch.setattr(ai_context, "snapshot", boom)
    assert asyncio.run(ai_context.build("acc")) == ""


# ── история диалога ───────────────────────────────────────────
def test_history_keeps_order_and_drops_foreign_roles():
    out = ai_agent.build_history([
        {"role": "user", "content": "первый"},
        {"role": "system", "content": "подмена системного промпта"},
        {"role": "assistant", "content": "ответ"},
        {"role": "tool", "content": "результат инструмента"},
        {"role": "user", "content": 42},
        {"role": "user", "content": "   "},
        {"role": "user", "content": "второй"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [m["content"] for m in out] == ["первый", "ответ", "второй"]


def test_history_is_trimmed_from_the_end_so_the_newest_survive():
    """Режем с конца: свежие реплики важнее, иначе длинный чат вытеснит из окна
    и системный промпт, и сам вопрос."""
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(60)]
    out = ai_agent.build_history(msgs)
    assert len(out) == ai_agent.MAX_HISTORY_MESSAGES
    assert out[-1]["content"] == "m59"
    assert out[0]["content"] == "m40"


def test_history_is_trimmed_by_total_length_too():
    big = "x" * 10_000
    out = ai_agent.build_history([{"role": "user", "content": big} for _ in range(10)])
    assert 0 < len(out) < 10
    assert sum(len(m["content"]) for m in out) <= ai_agent.MAX_HISTORY_CHARS


def test_history_of_nothing_is_empty():
    assert ai_agent.build_history(None) == []
    assert ai_agent.build_history([]) == []


# ── конфигурация и /tools ─────────────────────────────────────
def test_web_key_is_write_only_and_blank_keeps_it():
    aid, h = _register()

    cfg = client.get("/api/ai/config", headers=h).json()
    assert cfg["web_enabled"] is True and cfg["web_provider"] == "duckduckgo"
    assert cfg["has_web_key"] is False
    assert "web_api_key" not in cfg and "web_api_key_enc" not in cfg

    body = {**{k: v for k, v in cfg.items()
               if k in ("enabled", "provider", "base_url", "model", "max_steps",
                        "readonly", "gateway", "use_mcp", "web_enabled",
                        "web_provider", "web_max_results")},
            "web_provider": "tavily", "web_api_key": "tvly-secret-key"}
    r = client.post("/api/ai/config", json=body, headers=h)
    assert r.status_code == 200 and r.json()["has_web_key"] is True
    assert "tvly-secret-key" not in r.text        # ключ наружу не возвращается

    raw = storage.load_settings(aid)
    assert ai_agent.decrypt_key(raw["ai"]["web_api_key_enc"]) == "tvly-secret-key"

    # Пустое поле не затирает сохранённый ключ — иначе любая правка формы
    # молча выключала бы поиск.
    client.post("/api/ai/config", json={**body, "web_api_key": ""}, headers=h)
    raw = storage.load_settings(aid)
    assert ai_agent.decrypt_key(raw["ai"]["web_api_key_enc"]) == "tvly-secret-key"


def test_unknown_web_provider_is_rejected():
    _aid, h = _register()
    r = client.post("/api/ai/config", json={"web_provider": "гугл"}, headers=h)
    assert r.status_code == 422


def test_private_searxng_url_is_rejected_on_save():
    """Адрес своего инстанса задаёт пользователь, а ходит по нему НАШ сервер
    изнутри сети — гард обязателен и при сохранении (то же правило, что у
    `openstack.auth_url`)."""
    _aid, h = _register()
    for bad in ("http://127.0.0.1:8080", "http://169.254.169.254", "file:///etc"):
        r = client.post("/api/ai/config",
                        json={"web_provider": "searxng", "web_base_url": bad},
                        headers=h)
        assert r.status_code == 422, bad
    # Публичный адрес проходит. Берём IP-литерал, а не имя: `host_is_public`
    # РЕЗОЛВИТ хост, и тест на доменном имени зависел бы от наличия DNS в
    # окружении сборки.
    ok = client.post("/api/ai/config",
                     json={"web_provider": "searxng",
                           "web_base_url": "https://93.184.216.34:8080"}, headers=h)
    assert ok.status_code == 200


def test_tools_endpoint_reports_the_real_surface():
    _aid, h = _register()
    data = client.get("/api/ai/tools", headers=h).json()

    assert data["writes"] is False                 # запись выключена по умолчанию
    assert data["builtin"] == len(data["tools"])
    assert "panel_get" in data["tools"] and "web_search" in data["tools"]
    # В режиме чтения ни одного изменяющего инструмента в выдаче быть не должно.
    from app.services import ai_tools
    assert [n for n in data["tools"] if ai_tools.TOOLS[n].write] == []
