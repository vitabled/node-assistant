# Волна 5 · План H — API-токены доступа

> Идея 11. Долгоживущие **per-account API-токены** для внешних интеграций (MCP-контейнер, скрипты, ИИ),
> чтобы они НЕ таскали JWT из текущей браузерной сессии. Выпуск/список/отзыв в Настройках; секрет
> показывается один раз, в хранилище — только необратимый хеш (как пароли аккаунтов). `require_account`
> начинает принимать `Authorization: Bearer <api-token>` наравне с сессионным JWT → тот же `account_id` +
> `current_account`. Там, где оркестраторы сейчас минтят сессионный JWT (`mcp_server.start` →
> `NODE_ASSISTANT_TOKEN`), переходим на выпускаемый управляемый API-токен.
> Затрагивает: `services/api_tokens.py` (новый), `api/api_tokens.py` (новый), правка `api/auth.py`
> (`require_account`) + `services/storage.py` (+`services/mcp_server.py`); frontend
> `components/settings/ApiTokensTab.tsx` (новый) + вкладка в `Settings.tsx`. Переиспользует паттерн
> `accounts._hash_password`/`storage.*` (per-account JSON) и «show-once + amber warning» из `McpTab`.

## Контекст (как есть)

- **Единственная точка резолва креденшла → аккаунт** — `require_account` (`api/auth.py:54`): парсит
  `Bearer <jwt>` → `accounts.account_id_from_token` (JWT HS256, `sub`, **без exp**, `accounts.py:173`) →
  проверка `accounts.get(account_id)` → `accounts.current_account.set(...)` → 401 при любой осечке. Это
  ЕДИНСТВЕННОЕ место, куда нужно добавить второй резолвер.
- **Хеширование секретов уже есть** — `accounts._hash_password` (`accounts.py:62`): sha256+base64 →
  `bcrypt`. Пароли верифицируются РЕДКО (логин). API-токен верифицируется на **КАЖДОМ** запросе → медленный
  bcrypt-per-request не годится (см. Развилки).
- **Per-account JSON-хранилище** — единая воронка `storage.py` (`_dir` резолвит `current_account` или явный
  `account_id`, `storage.py:16`); 11 файлов уже так лежат (`accounts/<id>/*.json`). Новый файл
  `api_tokens.json` встаёт по этому же паттерну.
- **Оркестратор MCP минтит сессионный JWT** — `mcp_server.start` (`services/mcp_server.py:201`)
  `na_jwt = accounts.issue_token(aid)` → пишет в 0600 env-file как `NODE_ASSISTANT_TOKEN` (`:216`); MCP
  ходит нашим read-only инструментарием в backend этим полным JWT. Это и есть «JWT из сессии», который идея
  просит заменить управляемым отзываемым токеном.
- **ИИ-агент** (`services/ai_agent.py`) вызывает инструменты **in-process** (`account_id` из сессии, не по
  HTTP) → токен ему НЕ нужен; в объёме плана только MCP.
- **Роутеры** — все под `_auth = [Depends(require_account)]` в `main.py:98-126`; новый роутер добавляется туда
  же одной строкой.
- **Frontend Settings** — сегментные вкладки `Settings.tsx:719` (`SubTab`); тело `sub === "..." && <Tab/>`
  (`:747-754`). «Show-once секрет + amber-предупреждение» уже реализовано в `settings/McpTab.tsx` (токен
  MCP) и в генерации пароля `AuthScreen` — переиспользовать паттерн. Auth к `/api` добавляется глобально
  `window.fetch`-интерцептором (`auth/apiClient.ts`) → per-call токен не нужен.

## Развилки (закреплены)

- **Хеш, а не Fernet-волт.** Секрет плейнтекстом НЕ храним и не возвращаем повторно (show-once), значит
  расшифровка не нужна → **HMAC-SHA256** (ключ = `settings.encryption_key`), hex-дайджест в хранилище,
  сравнение `hmac.compare_digest`. Именно HMAC (а НЕ bcrypt): токен = 32 случайных байта (256 бит энтропии),
  офлайн-перебор невозможен, а верификация идёт на каждом запросе → нужен быстрый MAC. (Fernet тут не к
  месту — он обратимый, а нам обратимость не нужна.)
- **account_id встроен в сам токен → O(1) резолв без глобального индекса.** Формат
  `nai_<account_id>_<secret_urlsafe>`. `require_account` парсит `account_id` из токена, грузит ТОЛЬКО этого
  аккаунта `api_tokens.json`, верифицирует HMAC секрета против сохранённых записей (constant-time). Полная
  per-account изоляция сохраняется (никаких общих `DATA_DIR`-файлов индекса). Встроенный uuid не чувствительнее
  JWT-`sub` (тоже account_id).
- **Поля токена (v1):** `id`, `name`, `prefix` (первые ~10 символов для отображения), `hash` (HMAC-hex),
  `readonly` (bool, дефолт **false**), `expires_at` (epoch, 0 = бессрочно), `created_at`, `last_used_at`.
  Никаких гранулярных скоупов в v1 — только `readonly` (зеркалит MCP/AI `readonly`).
- **`readonly`-энфорсмент — лёгкий middleware:** если запрос аутентифицирован readonly-токеном и метод не
  GET/HEAD/OPTIONS → 403. Флаг публикуется на ContextVar при резолве. НЕ трогаем 28 роутеров по одному.
- **`last_used_at` — best-effort с троттлингом** (запись не чаще раза в 60 с на токен), чтобы не плодить
  write-per-request; при сбое записи запрос не падает.
- **MCP переходит на выпускаемый readonly-API-токен** (Ф2): `start` генерит выделенный токен «mcp-container»,
  инъектит плейнтекст в env-file, хранит только хеш; на каждый `start` — ротация (отозвать прежний
  managed-токен этого аккаунта, выпустить новый). MCP-инструменты read-only → readonly-токен идеально ложится.
  Сессионный JWT остаётся fallback, если выпуск токена почему-то невозможен.
- **В фоне не переспрашивать:** имя обязательно, expiry опционально (дефолт бессрочно), readonly дефолт off;
  отзыв — двойной клик-confirm.

## Стратегия

Ф1 (backend: стор токенов + двойной резолвер в `require_account` + CRUD-роуты) → Ф2 (backend:
readonly-middleware + перевод MCP-оркестратора на управляемый токен) → Ф3 (frontend: вкладка «Токены API»).

---

### Ф1 — Backend: хранилище токенов + резолвер + CRUD → verify: pytest

- `services/api_tokens.py` (новый):
  - Константа префикса `TOKEN_PREFIX = "nai_"`; `_hmac(secret) -> hex` (HMAC-SHA256, key=`settings.encryption_key`).
  - `create(account_id, name, readonly, expires_in) -> (record_masked, plaintext)` — сгенерить
    `secret = secrets.token_urlsafe(32)`, собрать `token = f"{PREFIX}{account_id}_{secret}"`, записать
    `{id, name, prefix, hash, readonly, expires_at, created_at, last_used_at:0}` через
    `storage.load/save_api_tokens`; вернуть плейнтекст ОДИН раз.
  - `list(account_id) -> [masked]` (без `hash`), `revoke(account_id, token_id)`.
  - `resolve(token) -> Optional[Resolved{account_id, token_id, readonly}]` — только если токен начинается с
    `PREFIX`: распарсить `account_id` + `secret`, свериться, что аккаунт существует
    (`accounts.get`), найти запись с `compare_digest(hash, _hmac(secret))`, проверить `expires_at` (0=никогда),
    троттл-обновить `last_used_at`. Любая осечка → `None` (тихо, как `account_id_from_token`).
- `services/storage.py` (+2 функции по образцу `load_netbird`/`save_netbird`):
  `load_api_tokens(account_id=None) -> list` (`api_tokens.json` → `{"tokens":[]}`),
  `save_api_tokens(tokens, account_id=None)`.
- `api/auth.py::require_account` (правка): после парса bearer —
  `if token.startswith(api_tokens.TOKEN_PREFIX):` резолв через `api_tokens.resolve` (вернёт account_id +
  публикует `token_readonly` ContextVar); `else` — прежний JWT-путь. Далее общий
  `current_account.set(account_id)`. Импорт `api_tokens` в `auth.py` (циклов нет: `api_tokens` импортит
  `storage`+`accounts`, `auth` импортит `api_tokens`).
  Новый ContextVar `token_readonly: ContextVar[bool]` — рядом с `current_account` (в `accounts.py` или
  `api_tokens.py`), дефолт `False`; JWT-путь и не-readonly токен оставляют `False`.
- `api/api_tokens.py` (новый роутер `/api/api-tokens`, под `require_account`):
  `GET /` → `api_tokens.list`; `POST /` (`{name, readonly=false, expires_in?}`) → создать, вернуть
  `{token: <plaintext>, ...masked}` (единственный раз); `DELETE /{id}` → отозвать.
- `main.py`: `app.include_router(api_tokens_router.router, dependencies=_auth)` в блоке `:99-126`.
- verify: `backend/tests/test_api_tokens.py` — create→list не отдаёт `hash`/секрет; `resolve` валидного
  токена → тот account_id; истёкший/чужой/битый → None; **межаккаунтная изоляция** (токен A не резолвится в
  контексте B); `require_account` принимает API-токен как bearer (через `TestClient` + `Authorization`).
  `python -m py_compile` + `pytest`.

---

### Ф2 — Backend: readonly-энфорсмент + MCP на управляемый токен → verify: pytest + smoke

- **Readonly middleware** (`main.py`, `@app.middleware("http")` ИЛИ узкая зависимость): после отработки
  `require_account`, если `token_readonly.get()` и `request.method not in {GET,HEAD,OPTIONS}` → 403
  «readonly-токен: запись запрещена». (Middleware читает ContextVar, выставленный резолвером.)
- **MCP-оркестратор** (`services/mcp_server.py:201`): вместо `accounts.issue_token(aid)` —
  `api_tokens.mint_managed(aid, name="mcp-container", readonly=True)` (новый хелпер: отзывает прежний
  managed-токен с этим `name` у аккаунта, выпускает новый, возвращает **плейнтекст**), инъектить в env-file
  как `NODE_ASSISTANT_TOKEN`. Ротация на каждый `start`. Fallback на `issue_token`, если выпуск упал (лог).
  Обновить docstring модуля (сейчас говорит «freshly-issued JWT»).
- verify: `test_api_tokens.py` — `mint_managed` ротирует (старый перестаёт резолвиться, новый резолвится);
  readonly-токен на POST → 403, на GET → ок. MCP `smoke.mjs`/`test_mcp.py` — env-file несёт `nai_`-токен;
  `python -m py_compile` + `pytest`.

---

### Ф3 — Frontend: вкладка «Токены API» → verify: tsc + preview

- `components/settings/ApiTokensTab.tsx` (новый): список токенов (name, `prefix••••`, создан, посл.
  использование, срок/бессрочно, бейдж «только чтение»); «Создать токен» (имя + опц. срок днями + чекбокс
  «только чтение») → `POST /api/api-tokens` → показать плейнтекст **ОДИН раз** с кнопкой копирования и
  **amber-предупреждением** «токен показан один раз — скопируйте сейчас» (переиспользовать паттерн из
  `McpTab`/`AuthScreen`); отзыв — двойной клик-confirm → `DELETE /api/api-tokens/{id}`. Auth добавляется
  глобально интерцептором (`apiClient.ts`) — per-call токен не нужен. Тема — CSS-var токены, без хардкода
  цветов; CSP-self-contained.
- `Settings.tsx`: добавить в `tabs` (`:719`) `{ id: "tokens", label: "Токены API" }` (перед «Тема») и тело
  `{sub === "tokens" && <ApiTokensTab />}`; расширить тип `SubTab`; импорт компонента.
- verify: `ApiTokensTab.test.tsx` — пустой/список/создание-показ-один-раз/валидация-имени/отзыв; `tsc` +
  preview вкладки (создать токен, скопировать, отозвать).

## РАЗВЕДКА (факты кодовой базы, сверено)

- `require_account` — единственная точка резолва bearer→account, `api/auth.py:54-65`.
- Хеш-паттерн паролей — `accounts._hash_password` (`accounts.py:62`); `current_account` ContextVar
  (`accounts.py:52`), авто-копируется в `create_task`/`to_thread`. `issue_token`/`account_id_from_token`
  (`accounts.py:167,173`), JWT **без exp**.
- Per-account JSON воронка — `storage.py:16` (`_dir`), примеры одиночных dict-сторов `load/save_netbird`
  (`storage.py:118-124`).
- MCP минтит сессионный JWT в env-file — `mcp_server.py:201` (`na_jwt`), `:216` (`NODE_ASSISTANT_TOKEN`),
  0600 env-file `:209-218`. MCP-инструменты в backend read-only (mcp `src/tools/node-assistant.ts`).
- Роутеры под `_auth` — `main.py:98-126`. Settings-вкладки — `Settings.tsx:719-754`.
- Внешних источников нет — фича внутренняя (авторизация нашего же API); опора на существующие паттерны
  проекта (пароли/Fernet-волты/`storage`).

## Критерии готовности плана H

- `require_account` принимает `Bearer nai_...` наравне с сессионным JWT → тот же `account_id` +
  `current_account`; JWT-путь не сломан.
- Токены per-account (`accounts/<id>/api_tokens.json`), в хранилище только HMAC-хеш; секрет показывается один
  раз; отзыв работает; истёкший/чужой токен → 401.
- Readonly-токен блокирует мутирующие методы (403); MCP-контейнер получает управляемый readonly-API-токен
  (ротация на старте) вместо сырого сессионного JWT.
- Вкладка «Токены API» в Настройках: создать (имя/срок/readonly) → копировать один раз → отозвать.
- `pytest` (`test_api_tokens.py`, +правки `test_auth.py`/`test_mcp.py`) + `tsc` + preview + ручной smoke
  (curl `/api/health`-подобного GET и любого POST выпущенным токеном; отзыв → 401). Пайплайн (14 шагов) НЕ
  затронут. Обновить CLAUDE.md §1b/§5 при реализации.
