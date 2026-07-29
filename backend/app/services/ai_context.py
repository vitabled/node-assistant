"""Живой контекст панели для встроенного ассистента.

Зачем: без него ассистент начинал каждый разговор с чистого листа и на вопрос
«сколько у меня нод?» шёл дёргать инструменты вслепую — или, что хуже, отвечал
общими словами. Короткая сводка в системном промпте сразу задаёт масштаб
(что настроено, чего нет, сколько чего), и модель тратит шаги на дело, а не на
разведку.

Два правила модуля:

1. **Только локальные чтения.** Сводка собирается на КАЖДЫЙ вопрос, поэтому в
   ней нет ни одного сетевого вызова — только JSON-сторы аккаунта и SQLite.
   Ходить в Remnawave/провайдеров за цифрами для приветствия значило бы платить
   секундой ожидания за каждое «привет».
2. **Никаких секретов.** Здесь считают КОЛИЧЕСТВА и флаги «настроено/нет».
   Ни токена, ни адреса панели, ни имени записи хранилища — сводка целиком
   уезжает в чужой LLM-эндпоинт, и всё, что в неё попало, считается разглашённым.

Ничего не бросает: каждый пробник в своём try/except, потому что один битый стор
не повод оставить пользователя без ответа.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

log = logging.getLogger("ai_context")


def _n(value: Any) -> int:
    try:
        return len(value)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return 0


async def snapshot(account_id: Optional[str]) -> dict:
    """Состояние аккаунта числами и флагами. Никогда не бросает."""
    from app.models.settings import AppSettings
    from app.services import storage

    snap: dict[str, Any] = {"today": _dt.date.today().isoformat()}

    settings_obj = None
    try:
        settings_obj = AppSettings(**storage.load_settings(account_id))
    except Exception as exc:  # noqa: BLE001
        log.info("ai_context.settings_failed", extra={"err": str(exc)[:200]})

    if settings_obj is not None:
        rw = settings_obj.remnawave
        registry = getattr(settings_obj, "remnawave_registry", None)
        snap["remnawave"] = {
            "configured": bool(rw.panel_url and rw.api_token),
            "panels": _n(getattr(registry, "panels", []) or []),
        }
        checker = settings_obj.xray_checker
        snap["checker"] = {"enabled": bool(checker.enabled),
                           "interval": checker.check_interval}
        snap["haproxy"] = {"enabled": bool(settings_obj.haproxy.enabled),
                           "mode": getattr(settings_obj.haproxy, "mode", "local")}
        cf = getattr(settings_obj, "cloudflare", None)
        snap["cloudflare"] = {"enabled": bool(getattr(cf, "enabled", False))}
        mcp = getattr(settings_obj, "mcp", None)
        snap["mcp"] = {"enabled": bool(getattr(mcp, "enabled", False))}

    # ── простые JSON-сторы аккаунта ───────────────────────────
    for key, loader in (
        ("subscriptions", storage.load_subscriptions),
        ("domains", storage.load_domains),
        ("host_templates", storage.load_hosts),
        ("deploy_templates", storage.load_templates),
        ("traffic_rules", storage.load_traffic_rules),
        ("testservers", storage.load_testservers),
        ("checker_instances", storage.load_checkers),
    ):
        try:
            snap[key] = _n(loader(account_id))
        except Exception:  # noqa: BLE001
            snap[key] = 0

    try:
        from app.services import rules_store
        rules = rules_store.list_rules(account_id)
        snap["rules"] = {"total": len(rules),
                         "enabled": sum(1 for r in rules if r.get("enabled"))}
    except Exception:  # noqa: BLE001
        snap["rules"] = {"total": 0, "enabled": 0}

    try:
        from app.services import hostings_store
        items = hostings_store.list_hostings(account_id)
        snap["hostings"] = {
            "total": len(items),
            "tariffs": sum(_n(h.get("tariffs")) for h in items),
        }
    except Exception:  # noqa: BLE001
        snap["hostings"] = {"total": 0, "tariffs": 0}

    try:
        from app.services import vault_store
        entries = vault_store.list_entries(account_id)
        kinds: dict[str, int] = {}
        for e in entries:
            kinds[e.get("kind") or "?"] = kinds.get(e.get("kind") or "?", 0) + 1
        # Только количества по типам — ни имён, ни ресурсов.
        snap["vault"] = {"total": len(entries), "by_kind": kinds}
    except Exception:  # noqa: BLE001
        snap["vault"] = {"total": 0, "by_kind": {}}

    try:
        from app.services import library_store
        items = library_store.list_items(account_id)
        snap["library"] = {
            "notes": sum(1 for i in items if i.get("kind") == "note"),
            "files": sum(1 for i in items if i.get("kind") == "file"),
            "folders": sum(1 for i in items if i.get("kind") == "folder"),
        }
    except Exception:  # noqa: BLE001
        snap["library"] = {"notes": 0, "files": 0, "folders": 0}

    try:
        from app.services import server_monitor_store
        servers = await server_monitor_store.list_servers(account_id)
        latest = await server_monitor_store.get_latest(account_id)
        visible = [s for s in servers if not s.get("hidden")]
        online = sum(1 for s in visible
                     if (latest.get(str(s.get("id"))) or {}).get("online"))
        snap["servers"] = {"total": len(visible), "online": online,
                           "hidden": len(servers) - len(visible)}
    except Exception:  # noqa: BLE001
        snap["servers"] = {"total": 0, "online": 0, "hidden": 0}

    try:
        from app.services import infra_billing_store
        providers = await infra_billing_store.provider_meta_all(account_id)
        services = await infra_billing_store.services(account_id)
        snap["billing"] = {"providers": _n(providers), "services": _n(services)}
    except Exception:  # noqa: BLE001
        snap["billing"] = {"providers": 0, "services": 0}

    return snap


def render(snap: dict) -> str:
    """Сводку — компактным текстом. Одна строка на подсистему, потому что этот
    блок уезжает в КАЖДЫЙ запрос: лишний абзац здесь — это лишние токены на
    каждом вопросе за всё время жизни панели."""
    rw = snap.get("remnawave") or {}
    lines = [
        f"Сегодня: {snap.get('today', '')}.",
        "Состояние панели этого аккаунта (снято локально, на момент вопроса):",
    ]
    lines.append(
        "- Remnawave: " + (
            f"настроен, панелей {rw.get('panels', 0)}"
            if rw.get("configured") else "не настроен"
        )
    )
    checker = snap.get("checker") or {}
    lines.append(f"- Мониторинг xray-checker: "
                 f"{'включён' if checker.get('enabled') else 'выключен'}; "
                 f"подписок {snap.get('subscriptions', 0)}, "
                 f"инстансов чекера {snap.get('checker_instances', 0)}")
    srv = snap.get("servers") or {}
    lines.append(f"- Доступность серверов: {srv.get('online', 0)} онлайн из "
                 f"{srv.get('total', 0)} (скрыто {srv.get('hidden', 0)})")
    rules = snap.get("rules") or {}
    lines.append(f"- Автоматизация: правил {rules.get('total', 0)} "
                 f"(включено {rules.get('enabled', 0)})")
    hostings = snap.get("hostings") or {}
    billing = snap.get("billing") or {}
    lines.append(f"- Хостинги: карточек {hostings.get('total', 0)}, "
                 f"тарифов {hostings.get('tariffs', 0)}; "
                 f"в биллинге провайдеров {billing.get('providers', 0)}, "
                 f"услуг {billing.get('services', 0)}")
    lib = snap.get("library") or {}
    vault = snap.get("vault") or {}
    lines.append(f"- Библиотека: заметок {lib.get('notes', 0)}, "
                 f"файлов {lib.get('files', 0)}; "
                 f"в хранилище записей {vault.get('total', 0)}")
    lines.append(f"- Прочее: доменов {snap.get('domains', 0)}, "
                 f"шаблонов хостов {snap.get('host_templates', 0)}, "
                 f"шаблонов деплоя {snap.get('deploy_templates', 0)}, "
                 f"тест-серверов {snap.get('testservers', 0)}")
    extras = []
    if (snap.get("haproxy") or {}).get("enabled"):
        extras.append(f"HAProxy/NodeFlow ({(snap.get('haproxy') or {}).get('mode')})")
    if (snap.get("cloudflare") or {}).get("enabled"):
        extras.append("Cloudflare")
    if (snap.get("mcp") or {}).get("enabled"):
        extras.append("MCP")
    if extras:
        lines.append("- Подключено: " + ", ".join(extras))
    return "\n".join(lines)


async def build(account_id: Optional[str]) -> str:
    """Готовый блок для системного промпта. Пустая строка, если совсем не
    вышло, — контекст полезен, но не обязателен."""
    try:
        return render(await snapshot(account_id))
    except Exception as exc:  # noqa: BLE001
        log.info("ai_context.build_failed", extra={"err": str(exc)[:200]})
        return ""
