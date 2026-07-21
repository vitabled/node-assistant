# Волна 5 · План J — Интеграция OpenCLIProxy (MCP + все AI-провайдеры)

> Идея 14 (разведка R3): дать (a) встроенному ИИ-агенту (`services/ai_agent.py`, сейчас provider-agnostic:
> OpenAI-совместимый `/chat/completions` + Anthropic `/v1/messages`) доступ к ЛЮБОМУ AI-провайдеру через ЕДИНЫЙ
> шлюз **CLIProxyAPI** (`router-for-me/CLIProxyAPI`, разговорно «opencliproxy», MIT, порт 8317), который
> оборачивает OAuth-подписки CLI-агентов (Claude Code / ChatGPT-Codex / Gemini / Grok / Qwen / Kimi / iFlow)
> **и** обычные API-ключи в OpenAI/Anthropic/Gemini-совместимые эндпоинты; и (b) прогонять LLM-вызовы
> MCP-хоста (нашего агента) через тот же шлюз, а не только по офиц. API провайдеров.
> Затрагивает: `backend/app/services/ai_agent.py` (новый режим-шлюз + exempt в net_guard), `models/settings.py`
> (`AiConfig`: поле `gateway`/список моделей), `api/ai.py` (эндпоинт списка моделей + валидатор провайдера), опц.
> новый `services/cliproxy_server.py` + `api/cliproxy.py` (self-host DooD-контейнер, по образцу `mcp_server.py`),
> `docker-compose.yml` (сервис `cli-proxy` под profile), frontend `components/settings/AiChat.tsx` (выбор
> провайдера/шлюза/модели). Переиспользует: Fernet-волт `ai_agent.encrypt_key/decrypt_key`, `net_guard.is_safe_url`,
> DooD-паттерн `mcp_server.py`/`xray_checker.py`, ключ-инвариант `SHA-256(settings.encryption_key)`.

## Контекст (как есть)

- **`ai_agent.py` уже provider-agnostic.** `_provider_turn(config, key, messages, with_tools)` (стр. 198–211)
  ветвится на `config.provider`: `"anthropic"` → `_anthropic_turn` (`{base_url}/messages`, `system` top-level,
  заголовок `x-api-key`), иначе → `_openai_turn` (`{base_url}/chat/completions`, `tools`+`tool_choice:auto`,
  `Authorization: Bearer <key>`). Оба возвращают `{"text","tool_calls","raw"}`.
- **SSRF-гард на КАЖДОМ тёрне.** `_provider_turn` (стр. 205) зовёт `net_guard.is_safe_url(config.base_url)` —
  `base_url` пользовательский, сервер ходит по нему с ключом. `net_guard.is_safe_url` (net_guard.py:40) **режет
  приватные/loopback/link-local/reserved хосты** → обращение к CLIProxyAPI по container-name на `node-assistant-net`
  (`http://cli-proxy:8317`) будет зарублено. Прецедент exempt уже есть: `xray_checker._get_json` (стр. 203–208)
  зовёт `assert_safe_url` ТОЛЬКО для внешнего remote-URL, а локальный контейнер по имени — доверенный, exempt.
- **`AiConfig`** (`models/settings.py:73`): `enabled`, `provider` (openai|anthropic), `base_url`
  (`https://api.openai.com/v1`), `model` (`gpt-4o-mini`), `api_key_enc` (Fernet, наружу не отдаётся), `max_steps`,
  `readonly`. Ключ шифруется `ai_agent.encrypt_key`, дешифруется `decrypt_key` (стр. 61–71).
- **`api/ai.py`**: `_PROVIDERS=("openai","anthropic")` + валидатор `AiConfigBody.provider`; `GET/POST /api/ai/config`
  (ключ write-only, blank=keep, `has_key` вместо ключа), `POST /api/ai/chat` (стрим ndjson). Всё под `require_account`.
- **`api/ai.py` НЕ отдаёт список моделей** — модель вводится строкой вручную во фронте (`AiChat.tsx`, `DEFAULTS`
  на провайдера, инпут `model`).
- **MCP** (`services/mcp_server.py`) — это MCP-**сервер** (read-only инструменты в наш backend + Remnawave), у него
  **нет собственного вызова LLM-провайдера**. DooD-оркестрация: один общий контейнер `node-installer-mcp` на
  `node-assistant-net`, env через 0600 `--env-file`, Fernet-волт `McpConfig.auth_token_enc`. Компоуз-сервис `mcp`
  профиль-гейтед `mcp-build` (docker-compose.yml:62), backend его `docker run`-ит.
- **Компоуз-паттерн self-host** уже отработан на `mcp`/`subs-aggregator`: сервис с `profiles:[...]`/`expose`,
  сеть по явному `name: node-assistant-net`, backend монтирует docker.sock и рулит сиблингом по container-name.

## Развилки (закреплены)

- **CLIProxyAPI = шлюз, а НЕ ещё один «провайдер».** Не плодим 8 адаптеров: агент уже говорит на двух форматах,
  которые CLIProxyAPI отдаёт (OpenAI + Anthropic). Достаточно нового режима `gateway="cliproxy"` в `AiConfig`,
  где `base_url` → CLIProxyAPI, `provider` (openai|anthropic) выбирает формат протокола, `model` = имя/alias из
  конфига CLIProxyAPI. По умолчанию `gateway="none"` (прямые вызовы, как сейчас).
- **«MCP через opencliproxy» переформулируется корректно.** MCP-сервер не трогаем — перенаправлять надо LLM-хоста,
  т.е. НАШ `ai_agent`. `mcp_server.py` остаётся как есть (задокументировать это в CLAUDE.md при реализации).
- **Self-host CLIProxyAPI — опционально, DooD, по образцу `mcp`.** Дефолт — указать на ВНЕШНИЙ CLIProxyAPI (уже
  развёрнутый оператором) через `base_url` + client-`api-key` в Fernet-волте. Self-host (Ф2) — отдельный тумблер,
  общий контейнер на `node-assistant-net`, exempt в net_guard как для локального чекера/mcp. В фоне не переспрашивать.
- **Клиентский `api-key` CLIProxyAPI** кладётся в тот же `AiConfig.api_key_enc` Fernet-волт (это обычный ключ).
  Management secret-key CLIProxyAPI (для self-host, Ф2) — отдельное Fernet-поле, наружу не отдаётся (маска).
- **Список моделей** тянем с CLIProxyAPI `GET /v1/models` (Ф1) — фронт показывает селектор вместо ручного инпута
  когда `gateway="cliproxy"`. Провал запроса → graceful, ручной ввод остаётся fallback.

## Стратегия

Ф1 (backend: режим-шлюз в `ai_agent` + net_guard-exempt + `/api/ai/models`) → Ф2 (backend: опц. self-host
CLIProxyAPI как DooD-контейнер + management-волт) → Ф3 (frontend: выбор шлюза/провайдера/модели + self-host).

---

### Ф1 — Backend: CLIProxyAPI как шлюз в ai_agent → verify: pytest + py_compile

- **`models/settings.py::AiConfig`** — добавить:
  - `gateway: str = "none"` (`none` | `cliproxy`) — режим шлюза;
  - опц. `gateway_internal: bool = False` — флаг «шлюз на нашем `node-assistant-net`» (включает net_guard-exempt);
    ставится автоматически при self-host (Ф2), иначе false → внешний URL гоняется через SSRF-гард как обычно.
  - (провайдер openai|anthropic оставляем — он выбирает ФОРМАТ протокола к шлюзу).
- **`ai_agent._provider_turn`** (стр. 205) — заменить безусловный `is_safe_url` на: **exempt для внутреннего
  шлюза** ровно как `xray_checker._get_json` (стр. 203–208):
  - если `config.gateway == "cliproxy"` и `config.gateway_internal` и хост `base_url` == известный внутренний
    контейнер (`cli-proxy` на `_network()`), то SSRF-гард пропускаем (доверенный container-name);
  - иначе — `is_safe_url` как сейчас (внешний CLIProxyAPI/прямой провайдер всё равно проверяется на каждом тёрне).
  - вынести проверку в маленький хелпер `_check_base_url(config)` (raise `AgentError`), чтобы логика exempt была
    в одном месте и покрывалась тестом.
- **`ai_agent`: список моделей** — новая функция `async def list_models(config, key) -> list[str]`: `GET
  {base_url}/v1/models` (OpenAI-формат; CLIProxyAPI отдаёт `GET /v1/models`), заголовок `Authorization: Bearer
  <key>`, парсит `data[].id`; не бросает (пустой список при ошибке), тот же `_check_base_url`/`redact`.
- **`api/ai.py`**:
  - `AiConfigBody` — добавить `gateway: str = "none"` (+валидатор на `("none","cliproxy")`), пробросить в
    `save_config`/`_public`. `_PROVIDERS` не трогаем (формат протокола остаётся).
  - новый `GET /api/ai/models` → `{models: [...]}` через `ai_agent.list_models(cfg, decrypt_key(cfg.api_key_enc))`;
    только для `gateway="cliproxy"` (иначе `{models:[]}`), под `require_account`.
- verify: `python -m py_compile`; `backend/tests/test_ai.py` — добавить кейсы: (1) `gateway=cliproxy` +
  `gateway_internal` + внутренний хост → SSRF-гард НЕ блокирует (turn доходит до httpx-мока); (2) внешний
  `gateway=cliproxy` хост-приват → блок как раньше; (3) `list_models` парсит `/v1/models` и не бросает на 500;
  (4) `_public` не отдаёт ключ, отдаёт `gateway`.

---

### Ф2 — Backend: опц. self-host CLIProxyAPI (DooD) → verify: pytest + docker compose config

- **`docker-compose.yml`** — новый сервис `cli-proxy` по образцу `mcp` (docker-compose.yml:62): образ
  CLIProxyAPI (собираем локально из `./cli-proxy` контекста ИЛИ pin официального образа — зафиксировать в
  разведке), `profiles: ["cliproxy-build"]` (compose его сам не стартует — backend `docker run`-ит), сеть
  `app-net`/явный `name: node-assistant-net`. Порт 8317 — **не пробрасывать наружу** по умолчанию (доступ по
  container-name с backend), как MCP `_CONTAINER_HTTP_PORT`.
- **`services/cliproxy_server.py`** — DooD-оркестратор по образцу `mcp_server.py`:
  - `CONTAINER_NAME="node-installer-cliproxy"`, `_docker(...)`/`_NO_DOCKER`/`container_state()`/`reachable()`/
    `logs()`/`start()`/`stop()` — 1:1 паттерн mcp;
  - `start(account_id)` пишет `config.yaml` CLIProxyAPI через 0600-файл (как mcp `--env-file`, но CLIProxyAPI
    конфиг — YAML): `host/port`, `api-keys` (наш сгенерённый клиентский ключ = кладём в `AiConfig.api_key_enc`),
    блоки провайдеров (из настроек аккаунта — API-ключи Gemini/OpenAI/Anthropic/Grok, если заданы), опц.
    `remote-management.secret-key` (Fernet-волт);
  - Fernet-волт management secret-key: `McpConfig`-подобные хелперы `ensure_mgmt_token`/`read_mgmt_token`
    (ключ = `SHA-256(settings.encryption_key)`), новое поле в конфиге (см. ниже);
  - **⚠️ один общий контейнер** несёт креды последнего включившего аккаунта — задокументировать как в mcp
    (owner-marker при желании; для дефолт-single-operator достаточно).
- **Конфиг** — новый `CliProxyConfig` на `AppSettings` (по образцу `McpConfig`): `enabled`, `port` (8317),
  `image`, `mgmt_secret_enc` (Fernet ciphertext, наружу маска), + список включённых upstream-провайдеров/ключей
  (сами ключи — Fernet). При включении self-host выставлять `AiConfig.gateway="cliproxy"`,
  `gateway_internal=True`, `base_url="http://node-installer-cliproxy:8317/v1"`.
- **`api/cliproxy.py`** (`/api/cliproxy`, под `require_account`): `GET/POST /config` (enable/провайдер-ключи;
  секреты write-only), `GET /status` (container_state+reachable, Docker-absent → `{ok,warning}` не 500),
  `POST /start`/`/stop`. Зарегистрировать роутер в `main.py` под `_auth`.
- verify: `docker compose config` (сервис `cli-proxy` валиден); `python -m py_compile`;
  `backend/tests/test_cliproxy.py` — генерация `config.yaml` (ключи не в argv/логе), Fernet at-rest
  (mgmt_secret_enc не plaintext), status-no-docker graceful, config CRUD + изоляция per-account.

---

### Ф3 — Frontend: выбор шлюза/провайдера/модели + self-host → verify: tsc + preview

- **`components/settings/AiChat.tsx`** (расширить существующую вкладку):
  - селектор **«Шлюз»**: «Прямой провайдер» (`gateway=none`) / «CLIProxyAPI» (`gateway=cliproxy`);
  - при `cliproxy`: показать поле Base URL (по умолч. внешний CLIProxyAPI-URL), поле клиентского `api-key`
    (в тот же `api_key`), и **селектор модели** из `GET /api/ai/models` (вместо ручного инпута; при пустом
    ответе — fallback на ручной ввод), + подпись формата протокола (openai|anthropic — как модель разруливается
    prefix/alias на стороне CLIProxyAPI);
  - блок **self-host** (Ф2): тумблер «Развернуть CLIProxyAPI локально», статус контейнера
    (`GET /api/cliproxy/status`), кнопки Старт/Стоп, поля upstream-ключей провайдеров (type=password), endpoint
    по container-name (read-only). Docker-absent → warning-плашка, не ошибка.
  - тема через CSS-var токены (как сейчас в файле), без хардкода цветов; без внешних CDN.
- **`AiChat.test.tsx`** — расширить: рендер селектора шлюза, переключение none↔cliproxy показывает/прячет
  селектор моделей, self-host-блок гейтится на статус.
- verify: `tsc --noEmit` (в docker-билде фронта); preview — переключить шлюз, увидеть список моделей, прогнать
  чат через CLIProxyAPI; (self-host — при наличии docker.sock) старт контейнера, reachable.

## РАЗВЕДКА (факты R3)

- **Проект = `router-for-me/CLIProxyAPI`** (Go, MIT ©2025). Доки `help.router-for.me`, deepwiki
  `deepwiki.com/router-for-me/CLIProxyAPI`, `config.example.yaml` в репо. «opencliproxy / open cli proxy /
  cli proxy api» — разговорные имена того же проекта.
- **Что это:** self-hosted прокси, оборачивает OAuth-подписки CLI-агентов (Claude Code, ChatGPT/Codex,
  Gemini CLI/Antigravity, Grok, Qwen Code, Kimi, iFlow) **и** обычные API-ключи в **единый набор
  OpenAI/Anthropic/Gemini/Codex-совместимых эндпоинтов**. Один локальный сервер, **порт по умолчанию 8317**,
  несколько протоколов одновременно поверх общего пула аккаунтов; round-robin/fill-first, session-affinity,
  мульти-аккаунт ротация + failover при quota-exceeded, hot-reload конфига.
- **Инференс-эндпоинты** (аутентификация клиента = `Authorization: Bearer <api-key>` из списка `api-keys`
  конфига; для Anthropic-пути работает и `x-api-key`):
  - OpenAI: `POST /v1/chat/completions`, `/v1/completions`, `/v1/responses`, `GET /v1/models`.
  - Anthropic: `POST /v1/messages`, `/v1/messages/count_tokens`.
  - Gemini: `POST /v1beta/models/{model}:generateContent` / `:streamGenerateContent`, `GET /v1beta/models`.
- **Модели задаются/алиасятся в конфиге** (`models: [{name, alias}]`) — фактический список = что прописано;
  источник истины — `config.example.yaml`. `GET /v1/models` отдаёт текущий список.
- **MCP — важный нюанс:** в ядре CLIProxyAPI **нативной поддержки MCP НЕТ** (ни транспорта, ни конфига). Он не
  MCP-клиент. Связь обратная: community-MCP-серверы используют CLIProxyAPI как LLM-бэкенд. Вывод: наш
  `mcp_server.py` (MCP-сервер без LLM-вызова) перенаправлять НЕ на что — через CLIProxyAPI ходит LLM-хост
  (наш `ai_agent`), который потребляет MCP-инструменты и шлёт LLM-запросы в шлюз.
- **Management/Admin REST API** (отдельно, под `/v0/management`): монтируется ТОЛЬКО если задан
  `remote-management.secret-key` (пустой → весь `/v0/management` = 404); удалённый доступ требует
  `remote-management.allow-remote: true`. Пути: `GET /v0/management/usage`, `GET|PUT /v0/management/config[.yaml]`,
  `GET /v0/management/api-keys` и др.
- **Деплой:** Docker + docker-compose (Dockerfile и compose в репо) — годится под наш DooD-паттерн. Конфиг
  `config.yaml` (`host`, `port` 8317, `tls`, `auth-dir`, `api-keys`, блоки провайдеров, `routing`,
  `remote-management`). Есть Go SDK `sdk/cliproxy`.
- **Точки интеграции в коде** (подтверждено чтением): `services/ai_agent.py` (режим-шлюз + exempt в net_guard —
  прецедент exempt: `xray_checker._get_json` стр. 203–208), `services/mcp_server.py` — БЕЗ изменений по части
  провайдера.
- Источники: `github.com/router-for-me/CLIProxyAPI`, `help.router-for.me/introduction/what-is-cliproxyapi`,
  `github.com/router-for-me/CLIProxyAPI/blob/main/config.example.yaml`,
  `deepwiki.com/router-for-me/CLIProxyAPI` (+ `.../4.4-management-api`), `help.router-for.me/configuration/basic`.
- **Открыто к проверке при реализации:** точный тег/имя официального Docker-образа CLIProxyAPI (для pin в
  compose) и формат стрима `/v1/responses` (нам не нужен — используем chat/completions + messages).

## Критерии готовности плана J

- `ai_agent` умеет режим `gateway="cliproxy"`: `base_url` → CLIProxyAPI, формат протокола openai|anthropic,
  клиентский ключ из Fernet-волта; SSRF-гард сохраняется для внешнего шлюза и **exempt-ится** только для
  внутреннего контейнера (как локальный чекер/mcp).
- `GET /api/ai/models` тянет список моделей с CLIProxyAPI; фронт показывает селектор (fallback — ручной ввод).
- Опц. self-host CLIProxyAPI поднимается DooD-контейнером на `node-assistant-net` (по образцу `mcp_server.py`),
  management secret-key + upstream-ключи провайдеров — в Fernet-волте, наружу маска; Docker-absent → warning.
- `pytest` (`test_ai.py` расширен + новый `test_cliproxy.py`) + `python -m py_compile` + `tsc` + `docker compose
  config` зелёные; ручной smoke: чат через CLIProxyAPI на реальный upstream, переключение провайдера/модели.
- Разведка R3 записана в CLAUDE.md (§8d/§9-блок) при реализации; зафиксировать «MCP через шлюз = LLM-хост, не
  MCP-сервер». Кросс-план: API-токены доступа — план H (`2026-07-21-wave5-h-api-tokens.md`), пресеты промптов
  агента — план I (`2026-07-21-wave5-i-ai-instructions.md`).
