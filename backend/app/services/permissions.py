"""Каталог привилегий и разметка маршрутов «путь → привилегия».

Зачем таблица, а не проверка в каждой ручке: гейт в проекте ОДИН
(`api/auth.py::require_identity`), через него проходят все 45 роутеров и 271
маршрут. Расставить проверки руками — это 271 правка, которая начнёт отставать
от кода на первой же новой ручке. Таблица же лежит рядом с реальной таблицей
маршрутов FastAPI, и её полноту проверяет тест.

⚠️ **Сопоставляем по ШАБЛОНУ маршрута (`request.scope["route"].path`), а не по
сырому URL.** К моменту вызова зависимости Starlette уже отмаршрутизировал
запрос, поэтому шаблон канонический по построению: ни `%2e%2e`, ни `//`, ни `.`
в нём быть не может. Это ровно та ошибка, на которой сломался денилист моста
ассистента (CLAUDE.md §20h) — там путь приходил строкой от модели и его
приходилось канонизировать вручную. Здесь этой работы нет, и изобретать её не
надо.

⚠️ **Запрет по умолчанию.** Маршрут, которого нет в `RULES`, доступен ТОЛЬКО
суперпользователю. Новый роутер, добавленный и не размеченный, не становится
общедоступным — он просто не работает у обычного пользователя, и это видно
сразу. Полноту таблицы держит `tests/test_permissions.py::test_every_route_is_mapped`.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

# ── действия ──────────────────────────────────────────────────
#
# Ровно четыре, как в постановке задачи. Удаление живёт внутри `edit`: разносить
# «изменить» и «удалить» по всем доменам — это вдвое больше галочек в UI, которые
# почти всегда стоят вместе. Там, где удаление действительно необратимо и дорого
# (снос панели, восстановление из бэкапа, обновление установки), оно проходит по
# `execute` или по особой привилегии из `SPECIAL`.
VIEW, CREATE, EDIT, EXECUTE = "view", "create", "edit", "execute"
ACTIONS = (VIEW, CREATE, EDIT, EXECUTE)

# ── домены ────────────────────────────────────────────────────
#
# Границы — по разделам навигации, а не по файлам: роль настраивает человек,
# и «Хостинги» ему понятнее, чем `hostings_store`. Перечислены только осмысленные
# действия: у статистики нет «создать», у ассистента нет «создать».
DOMAINS: dict[str, tuple[str, ...]] = {
    "deploy":     (VIEW, CREATE, EDIT, EXECUTE),   # ноды: карточки, детект, шаги
    "panel":      (VIEW, CREATE, EDIT, EXECUTE),   # установка Remnawave, бэкап, синк
    "certs":      (VIEW, CREATE, EDIT, EXECUTE),   # SSL и домены
    "hosts":      (VIEW, CREATE, EDIT),            # шаблоны хостов
    "configs":    (VIEW, CREATE, EDIT, EXECUTE),   # конфиги клиентов, страницы подписок
    "bridges":    (VIEW, CREATE, EDIT, EXECUTE),   # мосты: маршруты в профилях панели
    "automation": (VIEW, CREATE, EDIT, EXECUTE),   # правила, лимиты трафика
    "assistant":  (VIEW, EDIT, EXECUTE),           # ИИ-агент: смотреть, настроить, спросить
    "monitoring": (VIEW, CREATE, EDIT, EXECUTE),   # чекер, подписки, доступность серверов
    "stats":      (VIEW, EDIT),                    # статистика и раскладка виджетов
    "hostings":   (VIEW, CREATE, EDIT),            # каталог хостингов
    "billing":    (VIEW, CREATE, EDIT, EXECUTE),   # инфра-биллинг
    "cloudflare": (VIEW, EDIT, EXECUTE),
    "haproxy":    (VIEW, CREATE, EDIT, EXECUTE),
    "library":    (VIEW, CREATE, EDIT),            # заметки, файлы, медиа
    "vault":      (VIEW, CREATE, EDIT),            # Хранилище (значения — см. vault.reveal)
    "settings":   (VIEW, EDIT),
    "account":    (VIEW, EDIT),                    # своё: пароль, тема, свои API-токены
}

# ── особые привилегии ─────────────────────────────────────────
#
# Не выводятся из CRUD, потому что меняют не данные, а саму установку, её деньги
# или доступ к секретам.
SPECIAL: dict[str, str] = {
    "admin.users": "Управление пользователями",
    "admin.roles": "Управление ролями",
    "admin.infrastructure":
        "Инфраструктура: обновление панели, развёртывание и остановка общих "
        "контейнеров (MCP, CLIProxyAPI, NodeFlow, xray-checker)",
    "admin.export": "Выгрузка и загрузка данных установки (включая автобэкап)",
    "vault.reveal": "Просмотр значений секретов и выгрузка приватных ключей",
    "billing.purchase": "Покупка ресурсов и доменов (тратит деньги)",
    "deploy.credentials":
        "Передача SSH-учётных данных в запросах (деплой, операции с нодой, замеры)",
}

# Человеческие имена домена — для UI ролей.
DOMAIN_TITLES: dict[str, str] = {
    "deploy": "Ноды и деплой",
    "panel": "Панель Remnawave",
    "certs": "Сертификаты и домены",
    "hosts": "Шаблоны хостов",
    "configs": "Конфиги и страницы подписок",
    "bridges": "Мосты между серверами",
    "automation": "Автоматизация",
    "assistant": "ИИ-ассистент",
    "monitoring": "Мониторинг и доступность",
    "stats": "Статистика",
    "hostings": "Каталог хостингов",
    "billing": "Инфра-биллинг",
    "cloudflare": "Cloudflare",
    "haproxy": "HAProxy / NodeFlow",
    "library": "Библиотека и медиа",
    "vault": "Хранилище",
    "settings": "Настройки",
    "account": "Личное (свой пароль, тема, токены)",
}

ACTION_TITLES = {VIEW: "Просмотр", CREATE: "Создание", EDIT: "Изменение",
                 EXECUTE: "Выполнение"}


def all_permissions() -> list[str]:
    """Полный плоский список привилегий (домен.действие + особые)."""
    out = [f"{d}.{a}" for d, actions in DOMAINS.items() for a in actions]
    out += list(SPECIAL)
    return sorted(out)


ALL_PERMISSIONS = tuple(all_permissions())
_ALL_SET = frozenset(ALL_PERMISSIONS)


def is_known(permission: str) -> bool:
    return permission in _ALL_SET


def normalize(permissions: Iterable[str]) -> list[str]:
    """Отбрасывает неизвестные привилегии и дубли, сохраняя порядок каталога.

    Роль могла быть сохранена версией, где привилегия ещё существовала. Молча
    выбросить лишнее правильнее, чем уронить загрузку ролей целиком.
    """
    seen = {p for p in permissions if p in _ALL_SET}
    return [p for p in ALL_PERMISSIONS if p in seen]


# ── встроенные роли ───────────────────────────────────────────
#
# Создаются при первой инициализации и редактируемы. Не удаляются: установка без
# «Наблюдателя» оставила бы владельца без способа дать кому-то доступ только на
# чтение, и он выдал бы полные права.
def _views(*domains: str) -> list[str]:
    return [f"{d}.{VIEW}" for d in domains if VIEW in DOMAINS.get(d, ())]


def _full(*domains: str) -> list[str]:
    return [f"{d}.{a}" for d in domains for a in DOMAINS.get(d, ())]


_ALL_DOMAINS = tuple(DOMAINS)

BUILTIN_ROLES: tuple[dict, ...] = (
    {
        "id": "admin",
        "name": "Администратор",
        "description": "Всё, кроме управления пользователями и ролями.",
        "permissions": normalize(
            [p for p in ALL_PERMISSIONS if p not in ("admin.users", "admin.roles")]
        ),
    },
    {
        "id": "operator",
        "name": "Оператор",
        "description": "Разворачивает и обслуживает ноды. Без секретов, покупок и "
                       "управления установкой.",
        "permissions": normalize(
            _views(*_ALL_DOMAINS)
            + _full("deploy", "certs", "monitoring", "hosts")
            + ["automation.edit", "automation.execute", "configs.edit",
               "library.create", "library.edit", "stats.edit",
               "assistant.execute", "account.edit", "deploy.credentials"]
        ),
    },
    {
        "id": "finance",
        "name": "Финансы",
        "description": "Биллинг и каталог хостингов. Покупать не может.",
        "permissions": normalize(
            _views(*_ALL_DOMAINS)
            + _full("billing", "hostings")
            + ["account.edit", "assistant.execute"]
            # billing.purchase НЕ входит: смотреть расходы и тратить деньги —
            # разные полномочия, и роль «Финансы» обычно про первое.
        ),
    },
    {
        "id": "viewer",
        "name": "Наблюдатель",
        "description": "Только просмотр. Без Хранилища.",
        "permissions": normalize(
            [p for p in _views(*_ALL_DOMAINS) if p != "vault.view"]
            + ["account.edit"]
        ),
    },
)


# ── разметка маршрутов ────────────────────────────────────────
#
# Формат: (префикс шаблона маршрута, {метод или "*": привилегия или кортеж}).
# Первое совпадение по `startswith` — значит СПЕЦИФИЧНОЕ идёт РАНЬШЕ общего.
# Кортеж означает «нужны все перечисленные».
_CREDS = "deploy.credentials"

RULES: tuple[tuple[str, dict[str, object]], ...] = (
    # ── личное ───────────────────────────────────────────────
    ("/api/auth/password", {"*": "account.edit"}),
    ("/api/api-tokens", {"GET": "account.view", "*": "account.edit"}),
    ("/api/settings/appearance", {"*": "account.edit"}),

    # ── администрирование ────────────────────────────────────
    ("/api/users", {"*": "admin.users"}),
    ("/api/roles", {"*": "admin.roles"}),
    ("/api/audit", {"*": "admin.users"}),
    ("/api/export", {"*": "admin.export"}),
    ("/api/import", {"*": "admin.export"}),
    # Автобэкап настраивает, КУДА уезжает весь архив аккаунта, и умеет отправить
    # его прямо сейчас — это та же власть, что у экспорта.
    ("/api/settings/auto-backup", {"*": "admin.export"}),
    ("/api/updates/status", {"GET": "settings.view"}),
    ("/api/updates", {"*": "admin.infrastructure"}),

    # ── ассистент ────────────────────────────────────────────
    ("/api/ai/chat", {"*": "assistant.execute"}),
    ("/api/ai/compact", {"*": "assistant.execute"}),
    # Все ручки разговора — то же полномочие, что и сам чат.
    ("/api/ai/chat", {"*": "assistant.execute"}),
    ("/api/ai/config", {"GET": "assistant.view", "*": "assistant.edit"}),
    ("/api/ai/models", {"GET": "assistant.view"}),
    ("/api/ai/tools", {"GET": "assistant.view"}),
    ("/api/ai/prompts", {"GET": "assistant.view", "*": "assistant.edit"}),
    ("/api/mcp/config", {"GET": "assistant.view", "*": "admin.infrastructure"}),
    ("/api/mcp/status", {"GET": "assistant.view"}),
    ("/api/cliproxy/config", {"GET": "assistant.view", "*": "admin.infrastructure"}),
    ("/api/cliproxy/status", {"GET": "assistant.view"}),
    ("/api/cliproxy/start", {"*": "admin.infrastructure"}),
    ("/api/cliproxy/stop", {"*": "admin.infrastructure"}),
    ("/api/cliproxy/accounts", {"GET": "assistant.view", "*": "assistant.edit"}),
    ("/api/cliproxy/oauth", {"GET": "assistant.view", "*": "assistant.edit"}),

    # ── деплой нод ───────────────────────────────────────────
    ("/api/deploy/stop", {"*": "deploy.execute"}),
    ("/api/deploy", {"*": ("deploy.execute", _CREDS)}),
    ("/api/node/detect", {"*": ("deploy.view", _CREDS)}),
    ("/api/node/step", {"*": ("deploy.execute", _CREDS)}),
    ("/api/task", {"GET": "deploy.view"}),
    ("/api/templates", {"GET": "deploy.view", "POST": "deploy.create",
                        "*": "deploy.edit"}),
    ("/api/remnawave", {"GET": "deploy.view"}),
    ("/api/stats/node-speedtest/history", {"GET": "monitoring.view"}),
    ("/api/stats/node-speedtest", {"*": ("monitoring.execute", _CREDS)}),
    ("/api/stats/node", {"*": ("deploy.view", _CREDS)}),

    # ── панель Remnawave ─────────────────────────────────────
    ("/api/panel/detect", {"*": ("panel.view", _CREDS)}),
    ("/api/panel/metrics", {"*": ("panel.view", _CREDS)}),
    ("/api/panel/env/read", {"*": ("panel.view", _CREDS)}),
    ("/api/panel/env/write", {"*": ("panel.edit", _CREDS)}),
    ("/api/panel", {"*": ("panel.execute", _CREDS)}),
    ("/api/backup/status", {"*": "panel.view"}),
    # Восстановление перезаписывает БД панели поверх живой — это операция уровня
    # установки, а не обслуживания.
    ("/api/backup/restore", {"*": ("admin.infrastructure", _CREDS)}),
    ("/api/backup", {"*": ("panel.execute", _CREDS)}),
    ("/api/sync/groups", {"GET": "panel.view", "POST": "panel.create",
                          "PATCH": "panel.edit", "DELETE": "panel.edit"}),
    ("/api/migrate", {"*": ("panel.execute", _CREDS)}),
    ("/api/replace-domain", {"*": ("panel.execute", _CREDS)}),

    # ── сертификаты и домены ─────────────────────────────────
    # Выгрузка отдаёт приватный ключ — это раскрытие секрета, а не чтение данных.
    ("/api/certs/download", {"*": ("vault.reveal", _CREDS)}),
    ("/api/certs/deploy", {"*": ("certs.execute", _CREDS)}),
    # Автоскан доменов: ходит по SSH — тот же класс действия и креды, что деплой.
    ("/api/certs/scan-domains", {"*": ("certs.execute", _CREDS)}),
    ("/api/domains", {"GET": "certs.view", "POST": "certs.create",
                      "*": "certs.edit"}),

    # ── шаблоны хостов ───────────────────────────────────────
    ("/api/hosts", {"GET": "hosts.view", "POST": "hosts.create", "*": "hosts.edit"}),

    # ── конфиги и страницы подписок ──────────────────────────
    ("/api/config-templates/import/panel", {"GET": "configs.view",
                                            "*": "configs.create"}),
    ("/api/config-templates", {"GET": "configs.view", "POST": "configs.create",
                               "*": "configs.edit"}),
    ("/api/subpage-configs", {"GET": "configs.view", "POST": "configs.create",
                              "*": "configs.edit"}),
    ("/api/subpages/baselines", {"GET": "configs.view", "*": "configs.execute"}),
    ("/api/subpages", {"GET": "configs.view", "POST": "configs.create",
                       "*": "configs.edit"}),

    # ── мосты: правка routing в config-профилях панели (затрагивает всех её
    # пользователей) — create/edit на уровне оператора, как automation.
    ("/api/bridges", {"GET": "bridges.view", "POST": "bridges.create",
                      "DELETE": "bridges.edit", "*": "bridges.edit"}),

    # ── автоматизация ────────────────────────────────────────
    # ⚠️ Правило выполняется фоновым `rules_loop` — вне запроса и вне привилегий.
    # Поэтому `automation.create/edit` — полномочие уровня оператора, а не
    # наблюдателя: тот же класс «отложенного выполнения», что в CLAUDE.md §20h.
    ("/api/rules", {"GET": "automation.view", "POST": "automation.create",
                    "*": "automation.edit"}),
    ("/api/traffic-rules", {"GET": "automation.view", "POST": "automation.create",
                            "*": "automation.edit"}),

    # ── мониторинг и доступность ─────────────────────────────
    ("/api/checker/instances/deploy", {"*": ("monitoring.execute", _CREDS)}),
    ("/api/checker/instances", {"GET": "monitoring.view", "POST": "monitoring.create",
                                "*": "monitoring.edit"}),
    ("/api/checker/start", {"*": "admin.infrastructure"}),
    ("/api/checker/stop", {"*": "admin.infrastructure"}),
    ("/api/checker/update", {"*": "admin.infrastructure"}),
    ("/api/checker/check", {"*": "monitoring.execute"}),
    ("/api/checker", {"GET": "monitoring.view"}),
    ("/api/subscriptions", {"GET": "monitoring.view", "POST": "monitoring.create",
                            "PATCH": "monitoring.edit", "DELETE": "monitoring.edit"}),
    ("/api/server-monitor/servers/sync-deployed", {"*": "monitoring.edit"}),
    ("/api/server-monitor/import", {"*": "monitoring.create"}),
    ("/api/server-monitor/servers", {"GET": "monitoring.view",
                                     "POST": "monitoring.create",
                                     "*": "monitoring.edit"}),
    ("/api/server-monitor", {"GET": "monitoring.view"}),
    ("/api/testservers/deploy", {"*": ("monitoring.execute", _CREDS)}),
    ("/api/testservers", {"GET": "monitoring.view", "POST": "monitoring.create",
                          "*": "monitoring.edit"}),
    ("/api/speedtest/history", {"GET": "monitoring.view"}),
    ("/api/speedtest", {"*": ("monitoring.execute", _CREDS)}),

    # ── статистика ───────────────────────────────────────────
    ("/api/stats/users/widgets", {"GET": "stats.view", "*": "stats.edit"}),
    ("/api/stats/users/hidden", {"*": "stats.edit"}),
    ("/api/stats/users", {"GET": "stats.view"}),

    # ── каталог хостингов ────────────────────────────────────
    ("/api/hostings", {"GET": "hostings.view", "POST": "hostings.create",
                       "*": "hostings.edit"}),
    ("/api/subscription-analyze/to-hostings", {"*": "hostings.create"}),
    ("/api/subscription-analyze", {"*": "hostings.view"}),

    # ── инфра-биллинг ────────────────────────────────────────
    ("/api/infra-billing/providers/{uuid}/order-options", {"GET": "billing.view"}),
    ("/api/infra-billing/providers/{uuid}/order-quote", {"*": "billing.view"}),
    ("/api/infra-billing/providers/{uuid}/order", {"*": "billing.purchase"}),
    ("/api/infra-billing/providers/{uuid}/sync", {"*": "billing.execute"}),
    ("/api/infra-billing/providers/{uuid}/import-services", {"*": "billing.execute"}),
    ("/api/infra-billing/api-tokens/{tid}/verify", {"*": "billing.execute"}),
    ("/api/infra-billing/settings", {"GET": "billing.view", "*": "billing.edit"}),
    ("/api/infra-billing/adapters", {"GET": "billing.view"}),
    ("/api/infra-billing/dashboard", {"GET": "billing.view"}),
    ("/api/infra-billing", {"GET": "billing.view", "POST": "billing.create",
                            "PATCH": "billing.edit", "PUT": "billing.edit",
                            "DELETE": "billing.edit"}),

    # ── Cloudflare ───────────────────────────────────────────
    ("/api/cloudflare/domains/register", {"*": "billing.purchase"}),
    ("/api/cloudflare/domains/search", {"*": "cloudflare.view"}),
    ("/api/cloudflare/domains/check", {"*": "cloudflare.view"}),
    ("/api/cloudflare/domains", {"GET": "cloudflare.view", "*": "cloudflare.edit"}),
    ("/api/cloudflare/config", {"GET": "cloudflare.view", "*": "cloudflare.edit"}),
    ("/api/cloudflare/test", {"*": "cloudflare.view"}),
    ("/api/cloudflare", {"GET": "cloudflare.view"}),

    # ── HAProxy / NodeFlow ───────────────────────────────────
    ("/api/haproxy/deploy", {"*": "admin.infrastructure"}),
    ("/api/haproxy/stop", {"*": "admin.infrastructure"}),
    ("/api/haproxy/config", {"GET": "haproxy.view", "*": "haproxy.edit"}),
    ("/api/haproxy/test", {"*": "haproxy.view"}),
    ("/api/haproxy/local", {"GET": "haproxy.view"}),
    # ⚠️ Дженерик-прокси в чужую панель: что именно за ним стоит, мы не знаем,
    # поэтому размечаем по методу — единственное, что здесь достоверно.
    ("/api/haproxy/proxy", {"GET": "haproxy.view", "POST": "haproxy.edit",
                            "PUT": "haproxy.edit", "PATCH": "haproxy.edit",
                            "DELETE": "haproxy.edit"}),

    # ── библиотека и медиа ───────────────────────────────────
    ("/api/library/folders/rename", {"*": "library.edit"}),
    ("/api/library/folders", {"*": "library.create"}),
    ("/api/library/notes", {"GET": "library.view", "POST": "library.create",
                            "*": "library.edit"}),
    ("/api/library/upload", {"*": "library.create"}),
    ("/api/library/reorder", {"*": "library.edit"}),
    ("/api/library/files", {"GET": "library.view"}),
    ("/api/library/graph", {"GET": "library.view"}),
    ("/api/library", {"GET": "library.view", "*": "library.edit"}),
    ("/api/media/upload", {"*": "library.create"}),
    ("/api/media", {"GET": "library.view", "*": "library.edit"}),

    # ── Хранилище ────────────────────────────────────────────
    # Значение секрета — отдельная привилегия: видеть список записей и видеть
    # пароли это разные права.
    ("/api/vault/{entry_id}/reveal", {"*": "vault.reveal"}),
    ("/api/vault/{entry_id}/download", {"*": "vault.reveal"}),
    ("/api/vault/schemas", {"GET": "vault.view"}),
    ("/api/vault", {"GET": "vault.view", "POST": "vault.create", "*": "vault.edit"}),

    # ── инфраструктура ───────────────────────────────────────
    ("/api/certwarden/server", {"GET": "settings.view", "*": "admin.infrastructure"}),
    ("/api/certwarden", {"*": "admin.infrastructure"}),
    ("/api/netbird/control-plane", {"GET": "settings.view",
                                    "*": "admin.infrastructure"}),
    ("/api/netbird", {"*": "admin.infrastructure"}),

    # ── настройки (самый общий префикс — последним) ──────────
    ("/api/settings/remnawave/check", {"*": "settings.view"}),
    ("/api/settings", {"GET": "settings.view", "*": "settings.edit"}),
)

#: Маршруты вне модели привилегий, каждый по своей причине. Список закрытый:
#: тест покрытия требует, чтобы всё остальное было размечено.
EXEMPT: tuple[str, ...] = (
    "/api/health",          # liveness, он же healthcheck compose — данных не отдаёт
    "/api/auth/login",      # сам вход
    "/api/auth/state",      # «нужна ли первичная настройка» — один булев факт
    "/api/auth/bootstrap",  # создание первого владельца, одноразово
    "/api/auth/me",         # кто я и что мне можно; без него клиент не построит UI
    "/api/webhooks",        # капабилити = валидная HMAC-подпись
)


def required(route_path: str, method: str) -> Optional[tuple[str, ...]]:
    """Привилегии, нужные для `(шаблон маршрута, метод)`.

    `()` — маршрут открыт любому аутентифицированному (см. `EXEMPT`).
    `None` — маршрут не размечен: доступ только суперпользователю.
    """
    if route_path.startswith(EXEMPT):
        return ()
    method = (method or "GET").upper()
    for prefix, table in RULES:
        if not route_path.startswith(prefix):
            continue
        perm = table.get(method, table.get("*"))
        if perm is None:
            # Префикс совпал, но метод не описан — это пробел в разметке, а не
            # разрешение. Пусть решает суперпользователь.
            return None
        return (perm,) if isinstance(perm, str) else tuple(perm)
    return None


def holds(user_permissions: Sequence[str], needed: Iterable[str]) -> bool:
    have = set(user_permissions)
    return all(p in have for p in needed)


def missing(user_permissions: Sequence[str], needed: Iterable[str]) -> list[str]:
    have = set(user_permissions)
    return [p for p in needed if p not in have]


def catalogue() -> dict:
    """Каталог для UI ролей: домены с действиями + особые привилегии."""
    return {
        "actions": [{"id": a, "title": ACTION_TITLES[a]} for a in ACTIONS],
        "domains": [
            {"id": d, "title": DOMAIN_TITLES.get(d, d), "actions": list(actions)}
            for d, actions in DOMAINS.items()
        ],
        "special": [{"id": p, "title": t} for p, t in SPECIAL.items()],
    }
