# Волна 5 · План L — Импорт/экспорт данных панели

> Фича: перенос данных для миграции на новую версию панели или в другую инсталляцию. **Два независимых
> среза.** Срез 1 — наши **per-account данные node-assistant** (настройки/шаблоны/правила/подписки/хостинги/
> хосты/чекеры/тест-серверы/SQLite-истории/…) в один портируемый архив (`.tar.gz`: `manifest.json` + json + вложения).
> Срез 2 — данные **самой Remnawave-панели** (пользователи/хосты/сквады/config-profiles) через Remnawave API:
> экспорт из одной панели → импорт в другую/новую версию.
> Затрагивает: `services/export_service.py` (новый — сборка/разбор архива среза 1), `services/panel_export.py`
> (новый — снимок/восстановление среза 2 через `remnawave_client`), `api/export_io.py` (новый роутер
> `/api/export`, `/api/import`, под `require_account`), `frontend/src/components/settings/DataTransfer.tsx`
> (новый — вкладка «Экспорт/импорт»). Переиспускает: `services/storage.py` (11 json), явные `*_store.py`
> (hostings/sync/subpages + 5 SQLite), Fernet-волты (`rules_store`/`infra_billing_store`/`netbird`),
> `accounts.data_dir(id)`, `remnawave_client` (`list_hosts`/`list_internal_squads`/`list_config_profiles`/
> `get_users_in_squad`/`create_host`/`create_config_profile`), `migrate.py` `_remnawave_client(url,token)` +
> `net_guard.is_safe_url` (шаблон «клиент из произвольной панели per-request»).

## Контекст (как есть)

- **Единого экспорт/импорт-эндпоинта нет** — ни для наших данных, ни для панельных (проверено: в `main.py`
  28 data-роутеров, `export`/`import` среди них отсутствует).
- **Per-account хранилище рассыпано по трём слоям** (R9, сверено с кодом):
  - **11 json через `storage.py`** (единая воронка, `_dir(account_id)`): `settings.json`, `templates.json`,
    `traffic_rules.json`, `subscriptions.json`, `domains.json`, `hosts.json`, `checkers.json`, `rules.json`,
    `testservers.json`, `certwarden.json`, `netbird.json`.
  - **3 json со своими путями (в обход `storage.py`)**: `hostings.json` (`hostings_store._path`, атомарно
    temp+`os.replace`, MAX 500), `panel_groups.json` (`sync_store._path`, атомарно), `subpages/index.json` +
    `subpages/<page_id>.html` (`subpage_store`, lock, membership-guard от traversal, 512KiB/100).
  - **5 SQLite per-account**: `infra_billing.db` (**ContextVar-only**, `api_tokens.secret_enc` Fernet BLOB),
    `rules_secrets.db` (Fernet BLOB `secrets.secret_enc`), `server_monitor.db`, `node_speedtests.db`,
    `user_stats.db`. Все, кроме `infra_billing_store`, уже принимают явный `account_id`.
- **Секрет-несущие поля** (критично для политики секретов):
  - в `settings.json`: `remnawave.api_token` (**plaintext**, `models/settings.py:8`),
    `deploy_defaults.cloudflare_api_key` (**plaintext**, :16), `xray_checker.subscription_url` (plaintext,
    несёт токен, :51), `mcp.auth_token_enc` (**Fernet**, :70), `ai.api_key_enc` (**Fernet**, :81).
  - `netbird.json` — PAT в Fernet-поле; `subscriptions.json`/`checkers.json` — url/base_url с токенами.
  - Fernet-волты: `infra_billing.db`/`rules_secrets.db` BLOB. Все Fernet-ключи = `SHA-256(encryption_key)`
    (`rules_store._fernet`, :49-51) → **сырой копипаст переносим ТОЛЬКО при том же `ENCRYPTION_KEY`**.
- **GLOBAL (НЕ per-account, из среза 1 исключить):** `accounts.json` (реестр логинов/хешей),
  `xray_checker_metrics.db` (общая инфра, изоляция on-read по тегу имени), `mcp_owner.json`, `.legacy_migrated`.
- **Client-side (не на сервере, экспорту среза 1 недоступно):** `deploy_jobs_<id>`, `panel_jobs_<id>`,
  `xray_profile_<id>`, `ni_*` в браузерном localStorage (карточки нод/панелей + Xray-профили + UI-настройки).
- **Remnawave (срез 2):** `RemnavaveClient(base_url, token)` **stateless, per-call** (`_req` открывает свой
  `httpx.AsyncClient`). Клиент из произвольной пары `(url, token)` per-request — готовый шаблон
  `migrate.py:31 _remnawave_client` (+ `net_guard.is_safe_url`). Есть read: `list_hosts`,
  `list_internal_squads`/`list_external_squads`, `list_config_profiles`/`get_config_profile`, `list_nodes`,
  `get_users_in_squad` (пагинация `GET /api/users?size=500&start=`). Есть write: `create_host`,
  `create_config_profile`/`update_config_profile`. **Нет** `create_user` / bulk-import users → срез-2-импорт
  пользователей требует НОВЫХ методов клиента.

## Развилки (закреплены)

- **Два среза — раздельные операции и раздельный UI.** Срез 1 (наши данные) и срез 2 (Remnawave-панель) не
  смешиваются в одном архиве: разные форматы, разная политика секретов, разные риски импорта.
- **Формат среза 1 — `.tar.gz`** с `manifest.json` (версия формата `format_version=1`, `exported_at`,
  `source_account_id` для справки, список включённых стора + чек-суммы) + `data/<store>.json` + вложения
  (`subpages/*.html`). SQLite экспортируются как **дампы строк в json** (не бинарь файла — портируемо между
  версиями схемы, идемпотентный ре-инсерт). Версионирование формата обязательно.
- **Политика секретов среза 1 (дефолт):** экспорт **БЕЗ секретов** (плейн-поля `api_token`/`cloudflare_api_key`/
  `subscription_url`/PAT/Fernet-волты **исключаются или зануляются**, помечаются в manifest как `stripped`).
  Опция «включить секреты» → архив шифруется **паролем оператора** (PBKDF2→Fernet, пароль в запросе, на диск/в
  БД не пишется) — тогда плейн-секреты и **расшифрованные** волты (re-encrypt под пароль-ключ) кладутся внутрь.
  Так перенос между инсталляциями с РАЗНЫМ `ENCRYPTION_KEY` работает. «В фоне не переспрашивать»: дефолт —
  без секретов; шифрование — только по явному флагу + паролю.
- **Идемпотентность импорта среза 1:** merge по стабильным id (upsert), не слепая замена. Режим `mode`:
  `merge` (дефолт, дополняет/обновляет по id) | `replace` (полная замена стора — **двойной confirm**).
- **Срез 2 — экспорт read-only снимок**, импорт — только **аддитивный `create_*`** (хосты/config-profiles/
  сквады/пользователи), НИКОГДА не удаляет и не перезаписывает существующее в целевой панели без явного
  confirm. Пользователи среза 2 импортируются в НОВЫХ методах клиента (см. Ф2); при отсутствии `create_user`
  в целевой версии API — деградация: экспортировать пользователей в json, импорт пометить «не поддержано».
- **Целевая панель среза 2 — любая пара `(url, token)` per-request** (SSRF-guard), не обязательно активная
  «главная» (совместимо с Планом K — селектор панелей: можно выбрать источник/приёмник из реестра).

## Стратегия

Ф1 (backend: срез 1 — экспорт/импорт наших per-account данных) → Ф2 (backend: срез 2 — снимок/восстановление
Remnawave-панели через API) → Ф3 (frontend: вкладка «Экспорт/импорт»).

---

### Ф1 — Срез 1: экспорт/импорт данных node-assistant → verify: pytest

`services/export_service.py` (чистая сборка/разбор, без FastAPI) + `api/export_io.py` (`/api/export`, `/api/import`,
под `require_account`):
- **Инвентарь стора — единый реестр** `_STORES` (имя → load/save + признак secret-bearing + признак SQLite),
  покрывающий ВСЕ три слоя (11 `storage.*` + hostings/sync/subpages + 5 SQLite). Обход через
  `accounts.data_dir(id)` напрямую, НЕ только `storage.*` (R9: 3 json + 5 SQLite её минуют).
- **SQLite → json-дамп:** каждый store экспортирует свои строки (`server_monitor`/`speedtest`/`user_stats` —
  прямые SELECT; `rules_secrets`/`infra_billing` — только если включены секреты, см. ниже). Для этого добавить
  в соответствующие `*_store.py` функции `export_rows(account_id)`/`import_rows(rows, account_id, mode)`
  (идемпотентный `INSERT OR REPLACE` по PK). `infra_billing_store` — рефактор на явный `account_id`
  (сейчас ContextVar-only) ИЛИ вызывать под скопированным ContextVar; отметить как единственную точку.
- **`build_archive(account_id, stores, include_secrets, password) -> bytes`** — `manifest.json`
  (`format_version=1`, `exported_at`, включённые стора, per-store чек-суммы) + `data/<store>.json` + вложения
  `subpages/*.html`. `include_secrets=False` (дефолт) → секрет-поля зануляются/исключаются, помечаются
  `stripped` в manifest. `include_secrets=True` → требует `password`; плейн-секреты + расшифрованные
  Fernet-волты кладутся внутрь, весь архив шифруется PBKDF2→Fernet под паролем (пароль нигде не персистится).
- **`restore_archive(account_id, blob, password, mode, stores)`** — валидирует `format_version`, распаковывает,
  upsert по стабильным id (`merge`) или полная замена стора (`replace`). Секции с `stripped`-секретами не
  затирают существующие секреты пустым (тихий merge, как `/api/panel/env/write`). Незнакомый store в архиве →
  пропуск с предупреждением (forward-compat).
- **Роуты:** `POST /api/export` (тело: `stores[]`, `include_secrets`, `password?`) → `StreamingResponse`
  `application/gzip`, имя `node-assistant-export-<ts>.tar.gz`. `POST /api/import` (multipart: файл + `mode` +
  `password?` + `stores[]?`) → отчёт `{applied:{store:count}, skipped, warnings}`. **`replace` требует
  `confirm=true`** (иначе 400).
- Секреты НИКОГДА в логах; пароль — только в теле запроса, в память на время операции.
- verify: `backend/tests/test_export_io.py` — round-trip (export→import в чистый аккаунт, все стора совпали),
  политика секретов (без пароля секреты `stripped`; с паролем — расшифровка round-trip), идемпотентность
  (двойной импорт `merge` не дублирует), `replace` без confirm → 400, битый/чужой `format_version` → 422,
  изоляция (импорт под аккаунтом B не течёт в A). `python -m py_compile`.

---

### Ф2 — Срез 2: снимок/восстановление Remnawave-панели → verify: pytest + smoke

`services/panel_export.py` + расширение `api/export_io.py`:
- **Клиент из произвольной панели per-request** — переиспользовать паттерн `migrate.py._remnawave_client(url,
  token)` (+ `net_guard.is_safe_url`). Источник и приёмник — любые пары `(url, token)` из тела запроса
  (совместимо с Планом K — селектор панелей может подставлять их из реестра).
- **`snapshot_panel(client) -> dict`** — read-only снимок: `hosts` (`list_hosts`), `internal_squads`/
  `external_squads` (`list_*`), `config_profiles` (`list_config_profiles` + `get_config_profile` по uuid),
  `users` (`get_users_in_squad`-пагинация `GET /api/users?size=500`). Обёртка `format_version=1` +
  `source_panel_url` (для справки). Токены/секреты пользователей НЕ логируются.
- **`restore_panel(client, snapshot, confirm, sections)`** — **только аддитив**: `create_config_profile`/
  `create_host` (существующие есть), сквады — `create`+`add_all_users_to_*` (методы есть). **Пользователи**
  требуют НОВОГО метода клиента `create_user(payload)` (`POST /api/users`) + опц. bulk — добавить в
  `remnawave_client.py`; если целевая версия API не принимает — секция `users` помечается «не поддержано»,
  снимок отдаётся json-ом для ручного переноса. НИЧЕГО не удаляет/не перезаписывает; коллизии по имени/uuid →
  пропуск с отчётом.
- **Роуты:** `POST /api/export/panel` (тело: `panel_url`, `api_token`, `sections[]`) → снимок-json (скачивается
  как `panel-snapshot-<ts>.json`). `POST /api/import/panel` (тело: целевые `panel_url`/`api_token`, снимок,
  `sections[]`, `confirm=true` обязателен) → отчёт `{created:{section:count}, skipped, unsupported}`.
- verify: `backend/tests/test_panel_export.py` — `snapshot_panel`/`restore_panel` на замоканном клиенте
  (аддитивность, коллизии пропускаются, `users` без `create_user` → unsupported), SSRF-guard отклоняет
  приватный url, confirm обязателен. Ручной smoke при живой панели (у меня две панели — источник→приёмник).

---

### Ф3 — Frontend: вкладка «Экспорт/импорт» → verify: tsc + preview

- `frontend/src/components/settings/DataTransfer.tsx` — новая вкладка Settings (рядом с MCP/AI; регистрация в
  `Settings.tsx`). **Два блока:**
  - **«Данные node-assistant» (срез 1):** чек-лист стора (что переносить), тумблер «включить секреты» → поле
    пароля (+ амбер-предупреждение «без пароля секреты не переносятся; пароль нигде не сохраняется»), кнопка
    «Экспортировать» (скачивание `.tar.gz`). Импорт: выбор файла + `mode` (merge/replace), пароль (если архив
    шифрован), **двойной confirm на `replace`** (перезапись стора необратима), отчёт `applied/skipped/warnings`.
  - **«Панель Remnawave» (срез 2):** выбор панели-источника (из Плана K-реестра или ручной `url`+`token`),
    чек-лист секций → «Снимок панели» (скачивание json). Импорт: панель-приёмник + файл снимка + секции +
    **confirm**, отчёт `created/skipped/unsupported`.
- CSP-self-contained: без внешних ассетов; тема через CSS-var токены (skin×mode), цвета не хардкодить.
  Пароль/токены — `type=password`; секреты не эхаются в сообщения об ошибках (как `formatError` без `input`).
- verify: `tsc` (в docker-билде), preview — экспорт наших данных → импорт в свежий аккаунт совпал; снимок
  панели скачивается; `replace` требует двойной confirm.

## РАЗВЕДКА (факты — сверено с кодом)

- **Слои per-account хранилища (R9, подтверждено чтением файлов):** 11 json `storage.py`
  (`storage.py:36-124`); 3 json в обход (`hostings_store._path`, `sync_store._path`, `subpage_store._dir`);
  5 SQLite (`infra_billing_store` — ContextVar-only `:33-38`; `rules_store` `rules_secrets.db`;
  `server_monitor_store`; `speedtest_store`; `user_stats_store` — последние три с явным `account_id`).
- **Fernet-волты keyed `SHA-256(encryption_key)`** (`rules_store._fernet`, `models/settings.py` `*_enc`) →
  сырой перенос файлов портируем ТОЛЬКО при совпадающем `ENCRYPTION_KEY`; кросс-инстанс → decrypt-on-export/
  re-encrypt-on-import (обоснование политики секретов Ф1).
- **GLOBAL-исключения** (`accounts.py`, `metrics_store.py`, `mcp_server.py`): `accounts.json`,
  `xray_checker_metrics.db`, `mcp_owner.json`, `.legacy_migrated` — не per-account, из среза 1 исключить.
- **Remnawave client (срез 2):** read `list_hosts`/`list_internal_squads`/`list_external_squads`/
  `list_config_profiles`/`get_config_profile`/`get_users_in_squad`; write `create_host`/`create_config_profile`/
  `add_all_users_to_*_squad`. **`create_user` отсутствует** — добавить для импорта пользователей.
  Per-request клиент + SSRF-guard: `migrate.py:31` + `net_guard.is_safe_url`.
- Все data-роуты под `require_account` (`main.py` `_auth`); ContextVar `current_account` копируется в
  `create_task`/`to_thread` → фоновой обход стора корректно резолвит аккаунт.

## Критерии готовности плана L

- **Срез 1:** `/api/export` собирает `.tar.gz` (manifest+json+вложения) по выбранным стора со всех трёх слоёв
  хранилища; `/api/import` идемпотентно (merge/replace, replace под двойным confirm) восстанавливает в целевой
  аккаунт; политика секретов работает (дефолт — stripped; с паролем — зашифрованный round-trip между разными
  `ENCRYPTION_KEY`); версионирование формата (`format_version`) + forward-compat на незнакомые стора.
- **Срез 2:** снимок Remnawave-панели (hosts/squads/config-profiles/users) через API; аддитивный импорт в
  другую панель (per-request `url`+`token`, SSRF-guard, confirm), коллизии пропускаются, `users` деградируют
  корректно при отсутствии `create_user`.
- **Изоляция + безопасность:** экспорт/импорт строго per-account (`data_dir(id)`); GLOBAL-файлы исключены;
  секреты не в логах, пароль не персистится; двойной confirm на разрушающих операциях.
- `pytest` (`test_export_io.py` + `test_panel_export.py`) + `tsc` + preview + ручной smoke (наши данные
  round-trip в чистый аккаунт; снимок панели источник→приёмник на живых панелях). CLAUDE.md обновить при
  реализации.
