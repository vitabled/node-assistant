"""Мост «ассистент → REST API панели».

Почему мост, а не полсотни рукописных инструментов: вся функциональность панели
уже опубликована как REST под `require_account`, и это единственный источник
правды, который не отстаёт от кода. Рукописный набор пришлось бы дописывать при
каждой новой ручке — и он молча устаревал бы. Мост же ходит по реальной таблице
маршрутов FastAPI, поэтому «все аспекты node-assistant» — это буквально всё, что
умеет панель, включая то, что появится завтра.

Транспорт — `httpx.ASGITransport` прямо в наше же приложение: без сокета, без
сети, с настоящей валидацией pydantic и настоящим `require_account`. Токен
аккаунта — обычный сессионный JWT (`accounts.issue_token`), тот же механизм, что
у MCP-контейнера.

⚠️ **Три границы, которые здесь и держатся:**

1. **Денилист (`DENY`) — жёсткий, он не зависит от режима.** Сюда попадает всё,
   что выдаёт секреты (`/vault/*/reveal`, `/certs/download`, `/panel/env`),
   тратит деньги (`/order`, `/domains/register`), переставляет инфраструктуру
   (`/deploy`, `/updates/apply`, `/backup/restore`) или позволяет агенту
   перенастроить самого себя (`/api/ai`, `/api/mcp`, `/api/cliproxy`,
   `/api/api-tokens`). Никакой промпт этого не открывает.
2. **Режим только-чтение (`readonly`, по умолчанию включён)** — всё, кроме
   `GET`, отклоняется до вызова.
3. **`DELETE` запрещён всегда.** Удаление необратимо, а модель ошибается в
   идентификаторах; цена ошибки несимметрична.

Проверка выполняется В МОМЕНТ ВЫЗОВА, а не только при выдаче списка
инструментов: модель вправе придумать путь, которого мы ей не показывали (или
подхватить его из результата другого инструмента), поэтому фильтр на витрине —
не граница авторизации. Та же логика, что у MCP-гейта в `ai_agent._run_tool`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

log = logging.getLogger("ai_bridge")

#: Потолок на один результат инструмента. Дальше режет ещё и `ai_agent`, когда
#: кладёт результат в историю, — здесь потолок нужен, чтобы не тащить в память
#: мегабайтный ответ ради первых строк.
MAX_RESULT_CHARS = 12_000

_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH")

#: (регексп пути, методы или "*", причина). Причина уходит модели дословно —
#: она должна понимать, ЧТО именно нельзя, и предложить пользователю сделать это
#: руками, а не пытаться обойти запрет другим путём.
DENY: tuple[tuple[str, str, str], ...] = (
    # — самоконфигурация агента и ключи шлюза —
    (r"^/api/ai(/|$)", "*", "настройки самого ассистента менять нельзя"),
    (r"^/api/mcp(/|$)", "*", "настройки MCP менять нельзя"),
    (r"^/api/cliproxy(/|$)", "*", "шлюз CLIProxyAPI недоступен ассистенту (там ключи)"),
    (r"^/api/api-tokens(/|$)", "*", "выпуск API-токенов недоступен ассистенту"),
    (r"^/api/auth(/|$)", "*", "аккаунты и вход недоступны ассистенту"),
    # — секреты —
    (r"^/api/vault/[^/]+/(reveal|download)$", "*",
     "значения секретов ассистенту не выдаются — откройте запись в «Хранилище»"),
    (r"^/api/certs/", "*",
     "сертификаты: выгрузка отдаёт приватный ключ, а выпуск идёт по SSH"),
    # Конфиги клиентов несут `realitySettings.privateKey` прямо в теле, причём
    # JSON-строкой внутри JSON — маскировать такое по именам полей ненадёжно,
    # поэтому раздел закрыт целиком (найдено состязательным ревью).
    (r"^/api/config-templates", "*",
     "пользовательские конфиги содержат приватные ключи REALITY"),
    # URL подписки — это КАПАБИЛИТИ: кто им владеет, тот скачал все конфиги.
    # Для перечисления есть ярлык `list_subscriptions`, он отдаёт их без токена.
    (r"^/api/subscriptions", "*",
     "URL подписки равносилен доступу к конфигам — используй list_subscriptions"),
    (r"^/api/panel/env(/|$)", "*", "переменные окружения панели содержат секреты"),
    (r"^/api/migrate(/|$)", "*", "миграция работает с чужими кредами"),
    (r"^/api/settings/auto-backup", "*",
     "автобэкап несёт токен бота и адрес чата — канал утечки"),
    (r"^/api/backup(/|$)", "*", "бэкап/восстановление панели недоступно ассистенту"),
    (r"^/api/export", "*", "экспорт данных аккаунта недоступен ассистенту"),
    (r"^/api/import", "*", "импорт данных аккаунта недоступен ассистенту"),
    # — прокси мимо наших правил —
    (r"^/api/haproxy/proxy/", "*",
     "сырой прокси в NodeFlow идёт мимо ограничений — используйте конкретные ручки"),
    (r"^/api/webhooks(/|$)", "*", "вебхуки не предназначены для ассистента"),
    # — деньги —
    (r"^/api/infra-billing/providers/[^/]+/order", "*",
     "покупка ресурсов подтверждается человеком в разделе «Услуги»"),
    (r"^/api/cloudflare/domains/register$", "*",
     "покупка домена подтверждается человеком в разделе «Домены»"),
    # — необратимые операции с инфраструктурой (GET у них и так нет) —
    (r"^/api/deploy", "*", "деплой ноды запускается человеком"),
    (r"^/api/node/", "*", "операции по SSH требуют учётных данных от человека"),
    (r"^/api/panel/", "*", "установка и управление панелью запускается человеком"),
    (r"^/api/replace-domain/", "*", "смена домена запускается человеком"),
    (r"^/api/certwarden/", "*", "Certwarden настраивается человеком"),
    (r"^/api/netbird/", "*", "Netbird настраивается человеком"),
    # Не только `apply`: `POST /api/updates/config` взводит `auto_update`, то
    # есть то же самое обновление, только по расписанию.
    (r"^/api/updates/", "POST,PUT,PATCH", "обновление перезапускает всю установку"),
    (r"^/api/testservers/deploy$", "*", "развёртывание тест-сервера идёт по SSH"),
    (r"^/api/checker/instances/deploy$", "*", "развёртывание чекера идёт по SSH"),
    (r"^/api/sync/groups/[^/]+/run$", "*", "синхронизация панелей запускается человеком"),
    (r"^/api/haproxy/(deploy|stop|config|test)$", "POST",
     "управление стеком NodeFlow запускается человеком"),
    # — настройки: чтение можно, запись нет —
    (r"^/api/settings/", "POST,PUT,DELETE",
     "настройки панели меняются человеком в разделе «Настройки»"),
    # ⚠️ Правило — это ОТЛОЖЕННОЕ действие: его выполнит фоновый `rules_loop`,
    # то есть уже вне денилиста, вне `readonly` и после того, как «загрязнение
    # вебом» сбросится вместе с ответом. Среди действий есть `node_disable`,
    # `hide_hosts` и `telegram` — то самое сочетание «изменение инфраструктуры +
    # внешний канал», из-за которого закрыт автобэкап. Читать правила можно.
    (r"^/api/rules", "POST,PUT,PATCH",
     "правила выполняются фоном, вне ограничений ассистента — заведите правило "
     "сами в разделе «Автоматизация»"),
    # — тяжёлые операции по SSH: только чтением их не сделать —
    (r"^/api/(stats/node|stats/node-speedtest|speedtest/)", "POST",
     "замеры по SSH требуют учётных данных от человека"),
)

_DENY_RX = tuple((re.compile(rx), methods, reason) for rx, methods, reason in DENY)

#: Значения этих полей вырезаются из ЛЮБОГО ответа моста. Причина конкретная:
#: `GET /api/settings` отдаёт `remnawave.api_token` открытым текстом (так он и
#: лежит в settings.json), а ответ моста целиком уезжает в чужой LLM-эндпоинт.
#:
#: ⚠️ Имя ключа НОРМАЛИЗУЕТСЯ (нижний регистр, прочь всё, кроме букв и цифр) —
#: первая версия сравнивала имя как есть и пропускала половину реальных полей
#: проекта: `privateKey`, `master_key`, `X-API-Key`, `passphrase`,
#: `access_key_id` (найдено состязательным ревью). Список — по подстроке,
#: потому что перечислять поля сорока моделей и поддерживать этот перечень в
#: актуальном состоянии никто не будет.
_SECRET_SUBSTRINGS = ("token", "key", "secret", "pass", "auth", "cred", "hmac",
                      "signature", "subscriptionurl")
#: Короткие имена, которые как ПОДСТРОКА поймали бы невинное: `pat` сидит в
#: `path`/`patch`, `pin` — в `ping`.
_SECRET_EXACT = {"pat", "pin", "psk", "seed", "otp"}
_SECRET_MASK = "«скрыто»"

#: Секрет умеет приехать и в ЗНАЧЕНИИ несекретного ключа: `xray_json_template`
#: у шаблона хоста — это СТРОКА с JSON внутри, и `realitySettings.privateKey`
#: лежит внутри неё; ошибка вендора приезжает вместе с URL, несущим креды в
#: query (приём Beget). Обход по ключам такое не видит в принципе.
_TEXT_SECRETS = (
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.?[A-Za-z0-9_\-]*"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\"(private_?key|password|passwd|secret|api[_-]?key|token|"
               r"passphrase)\"\s*:\s*\"[^\"]{4,}\""),
    re.compile(r"(?i)([?&](?:pass|passwd|password|token|api_?key|secret)=)[^&\s\"]+"),
)


def redact_text(text: str) -> str:
    """Замазывает секреты, приехавшие внутри строкового значения."""
    if len(text) < 12:
        return text
    for rx in _TEXT_SECRETS:
        text = rx.sub(_SECRET_MASK, text)
    return text


def _is_secret_key(key: str) -> bool:
    norm = re.sub(r"[^a-z0-9]", "", key.lower())
    if norm in _SECRET_EXACT:
        return True
    # Суффикс `_enc` — конвенция проекта для Fernet-шифротекста (`fields_enc`,
    # `api_key_enc`). Шифротекст сам по себе не секрет, но и в промпте ему
    # делать нечего: это чистый шум, который вдобавок можно унести наружу.
    if norm.endswith("enc"):
        return True
    return any(part in norm for part in _SECRET_SUBSTRINGS)


def scrub(value: Any, depth: int = 0) -> Any:
    """Рекурсивно вырезает секреты — и по имени поля, и по виду значения.

    ⚠️ Оставляем сам КЛЮЧ: модель должна видеть, что поле есть и заполнено, —
    иначе она решит, что панель не настроена, и начнёт советовать это исправить.
    """
    if depth > 12:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_secret_key(k):
                out[k] = _SECRET_MASK if v else v
            else:
                out[k] = scrub(v, depth + 1)
        return out
    if isinstance(value, list):
        return [scrub(v, depth + 1) for v in value[:200]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def deny_reason(method: str, path: str) -> str:
    """`""` — можно; иначе человеко-читаемая причина отказа."""
    method = (method or "GET").upper()
    if method == "DELETE":
        return ("удаление через ассистента запрещено — удалите вручную, "
                "если это действительно нужно")
    if method not in _ALLOWED_METHODS:
        return f"метод {method} не поддерживается"
    for rx, methods, reason in _DENY_RX:
        if not rx.search(path):
            continue
        if methods == "*" or method in methods.split(","):
            return reason
    return ""


def normalize_path(path: str) -> str:
    """Приводим то, что придумала модель, к КАНОНИЧЕСКОМУ пути нашего API.

    Модель регулярно присылает полный URL или путь без префикса — отказ с
    формальной претензией к формату тратит шаг агента на ровном месте.

    ⚠️ **Канонизация здесь — граница безопасности, а не удобство.** Первая
    версия снимала только хвостовой слэш, а `httpx` перед отправкой в ASGI сам
    схлопывает `.`/`..` и раскрывает процент-кодирование: денилист проверял одну
    строку, Starlette маршрутизировал другую, и `/api/vault/id/./reveal` или
    `/api/clipro%78y/config` проходили мимо ВСЕГО списка запретов (найдено
    состязательным ревью, воспроизведено). Поэтому путь приводится к той форме, в
    которой его увидит приложение, ТОЙ ЖЕ библиотекой — и дальше именно она и
    проверяется, и отправляется.
    """
    p = (path or "").strip()
    if p.startswith("http://") or p.startswith("https://"):
        p = httpx.URL(p).path
    if not p.startswith("/"):
        p = "/" + p
    # Дубли слэшей httpx НЕ схлопывает, а Starlette по такому пути маршрута не
    # найдёт — схлопываем сами, чтобы `//api/...` не стал способом спрятать путь
    # от денилиста.
    while "//" in p:
        p = p.replace("//", "/")
    if not p.startswith("/api"):
        p = "/api" + p
    # Канонизация тем же кодом, что повезёт запрос: dot-сегменты + %XX.
    p = httpx.URL("http://ai-bridge" + p).path
    if len(p) > 5 and p.endswith("/"):
        p = p.rstrip("/")
    return p


async def call(method: str, path: str, account_id: str,
               query: Optional[dict] = None, body: Optional[dict] = None,
               readonly: bool = True) -> dict:
    """Один вызов REST панели от имени аккаунта. Никогда не бросает."""
    from app.services import accounts

    method = (method or "GET").upper()
    path = normalize_path(path)

    if readonly and method != "GET":
        return {"ok": False, "error":
                "ассистент работает в режиме только для чтения — включите запись "
                "в «Настройки → AI», если действительно хотите разрешить изменения"}
    reason = deny_reason(method, path)
    if reason:
        return {"ok": False, "error": f"действие запрещено: {reason}"}

    try:
        token = accounts.issue_token(account_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"не удалось авторизоваться: {str(exc)[:120]}"}

    try:
        from app.main import app  # ленивый импорт: main импортирует api.ai → нас

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, timeout=60.0,
                                     base_url="http://ai-bridge") as client:
            # Пояс поверх подтяжек: сверяем путь, который РЕАЛЬНО уедет в
            # приложение, с тем, что мы проверили. `normalize_path` уже
            # канонизирует той же библиотекой, так что расхождения быть не
            # должно — но именно на этом расхождении и держался обход денилиста,
            # поэтому проверка стоит вплотную к отправке.
            final = client.build_request(method, path).url.path
            if final != path and deny_reason(method, final):
                return {"ok": False, "error":
                        "действие запрещено: путь ведёт к закрытой ручке"}
            r = await client.request(
                method, path,
                params={k: v for k, v in (query or {}).items() if v is not None},
                json=body if method != "GET" else None,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"вызов не удался: {str(exc)[:200]}"}

    ctype = (r.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            data: Any = scrub(r.json())
        except Exception:  # noqa: BLE001
            data = r.text[:MAX_RESULT_CHARS]
    elif ctype.startswith("text/"):
        data = r.text[:MAX_RESULT_CHARS]
    else:
        data = f"(двоичный ответ {len(r.content)} байт — покажите файл пользователю сам)"

    blob = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    truncated = len(blob) > MAX_RESULT_CHARS
    if truncated:
        data = blob[:MAX_RESULT_CHARS]

    out: dict[str, Any] = {"ok": r.status_code < 400, "status": r.status_code,
                           "data": data}
    if truncated:
        out["truncated"] = True
        out["hint"] = ("ответ обрезан — сузьте запрос параметрами или "
                       "спросите конкретную запись")
    return out


def endpoints(contains: str = "", limit: int = 200) -> list[dict]:
    """Каталог доступных ручек из РЕАЛЬНОЙ таблицы маршрутов FastAPI.

    Запрещённые не показываем вовсе: реклама того, на что мы всё равно ответим
    отказом, тратит шаги агента и провоцирует его искать обход.
    """
    try:
        from app.main import app
    except Exception as exc:  # noqa: BLE001
        log.info("ai_bridge.routes_failed", extra={"err": str(exc)[:200]})
        return []

    needle = (contains or "").strip().lower()
    rows: list[dict] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = sorted((getattr(route, "methods", None) or set())
                         - {"HEAD", "OPTIONS"})
        allowed = [m for m in methods if not deny_reason(m, path)]
        if not allowed:
            continue
        if needle and needle not in path.lower():
            continue
        doc = (getattr(route, "endpoint", None).__doc__ or "") \
            if getattr(route, "endpoint", None) else ""
        summary = " ".join(doc.strip().split())[:160]
        rows.append({"methods": allowed, "path": path, "summary": summary})
    rows.sort(key=lambda r: r["path"])
    return rows[:limit]
