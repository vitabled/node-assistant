# Волна 5 · План M — Переход на микросервисную архитектуру

> **Статус (2026-07-21): РЕАЛИЗОВАНО (Ф1–Ф3 + Ф5). Ф4 — не делали (сам план помечает «по потребности»).**
> Отгружено отдельной фокус-сессией, как и предполагала отметка об отсрочке. Детали — CLAUDE.md §10.
>
> **Два осознанных отклонения от буквы плана (оба согласованы с пользователем):**
> 1. **Нет HTTP-прокси и нет `service_auth` (Ф1c).** Разведка показала, что все сторы — SQLite/JSON на общем
>    томе `node-data`, поэтому вынесенные процессы **не поднимают HTTP вообще**: они только выполняют фоновую
>    работу, а все чтения gateway делает сам с того же тома. Прокси-слой и контракт service-аутентификации были
>    бы спекулятивным кодом (CLAUDE.md §2). Ф1c заменён на `services/worker_lease.py` — механизм аренд, который
>    и обеспечивает опциональность/обратимость распила (критерий отката Ф5) лучше, чем env-гейт.
> 2. **В очередь заведены `deploy` и `node-op`, а не все 17 task-видов.** «Полный деплой со стримом логов» —
>    именно то, что перечислено в критериях готовности; остальные task-виды короткие и остались в gateway.
>    Добавление любого = 3 строки `job_runner.register(...)`.
>
> **Проверено:** `pytest` 602 passed (было 564; +38 новых); `docker compose config` и
> `docker compose --profile split config/build` — оба валидны и собираются; **`backend/tests/e2e/split_smoke.py`**
> — четырёхфазный многопроцессный харнесс: монолит → перехват обязанностей выделенными воркерами → **реальный
> `POST /api/deploy` уходит в очередь, 14-шаговый пайплайн крутится в ВОРКЕРЕ, логи приходят подписчику
> `/ws/logs/{task_id}` на GATEWAY** → смерть воркеров → gateway сам возобновляет мониторинг. Тот же деплой в
> монолит-режиме — без изменений (`queued=0`, без передачи, идентичный вид задачи).
>
> **Второй проход по итогам adversarial-ревью** (5 линз, 27 кандидатов, **8 подтверждено**) — коммит `b8be232`.
> Первый проход сделал happy path верно, а пути отказа — нет: существовало несколько способов оставить карточку
> висеть навсегда без вердикта, что ломало сам критерий отката, ради которого План M и затевался. Починено:
> аренда отдаётся на выключении (иначе gateway ждал TTL 180с), SIGTERM в воркере (PID 1 по умолчанию ИГНОРИРУЕТ
> SIGTERM), реапер осиротевших claim-ов, `cancel_requested` на claim, вердикт при недешифруемом payload,
> различение «отменил юзер» / «нас выключают» (иначе цикл висел вечно, а пайплайн крутился отцеплённым),
> дочитывание `task_logs` перед `done`, конкурентность воркера = `max_ssh_sessions` + 503 по глубине очереди,
> и `_bars` 1.5–2.0с → 111мс (covering-индекс вместо window-функции). Детали — CLAUDE.md §10.
>
> **Побочно исправлены два дефекта, найденные разведкой** (см. CLAUDE.md §10f): фоновый поллер никогда не
> сэмплил общий xray-checker (проглоченный `RuntimeError` из-за отсутствия account-контекста), и
> `metrics_store._bars` читал только in-process ring (в split-режиме бары замерли бы навсегда).

> Поэтапный (strangler-pattern) распил текущего монолита node-assistant на несколько сервисов по **существующим
> швам**, БЕЗ переписывания с нуля и без над-инжиниринга (simplicity-first, CLAUDE.md §2). Цель — вынести
> тяжёлые/долгоживущие подсистемы (SSH-деплой-пайплайн, мониторинг-лупы) в отдельные процессы для изоляции
> отказов, независимого рестарта и опц. масштабирования, сохранив per-account изоляцию и «секреты не в открытую».
> Реалистичная рамка: это **single-operator self-hosted** панель — полный микросервис — это осознанный
> оверхед; план делает распил **опциональным** (compose-профиль/фиче-флаг), с чёткими критериями «когда
> останавливаться», а не самоцелью. Затрагивает: `docker-compose.yml` (новые сервисы), `backend/app/main.py`
> (какие лупы/роутеры где живут), `services/task_store.py` (in-memory → разделяемое), `services/infra_billing_store.py`
> (единственный ContextVar-only стор → explicit `account_id`), новый общий контракт сервис-в-сервис аутентификации
> (переиспользует `settings.encryption_key`/JWT и **План H** API-токены). Переиспользует уже существующий
> **DooD-прецедент** (`services/xray_checker.py`, `services/mcp_server.py` — сиблинг-контейнеры на
> `node-assistant-net`, JWT из `accounts.issue_token`, секреты через 0600 env-file).

## Контекст (как есть)
- **Монолит + несколько сателлитов.** Один FastAPI-процесс `backend` (`docker-compose.yml`) обслуживает **28
  data-роутеров** под `require_account` (`main.py:99-126`: deploy/certs/stats/node_ops/settings/traffic_rules/
  xray_checker/infra_billing/subscriptions/domains/hosts/user_stats/testservers/panel_deploy/panel_metrics/backup/
  subpages/speedtest/rules/mcp/ai/panel_sync/migrate/server_monitor/hostings/replace_domain/certwarden/netbird)
  + 4 публичных (auth, ws, subscriptions.internal, rules.webhook) + `/api/health`.
- **5 фоновых лупов в одном lifespan** (`main.py:55-62`): `poller_loop` (xray-checker → метрики), `collector_loop`
  (Remnawave usersOnline → user_stats), `rules_loop` (правила xray_down/cron), `autostart_checker` (старт общего
  чекера на буте), `server_monitor.monitor_loop` (TCP/ICMP-пробы серверов). Все — в процессе `backend`, делят
  event loop с HTTP.
- **Уже есть 4 сателлита на общей сети `node-assistant-net`:** `subs-aggregator` (отдельный compose-сервис),
  `xray-checker` и `node-installer-mcp` (DooD — backend поднимает их через хостовый docker.sock и ходит по
  имени контейнера). Т.е. multi-service инфраструктура **частично уже здесь** — это рабочий прецедент распила.
- **Изоляция per-account держится на 3 связках (R9):**
  1. **ContextVar `current_account`** (`accounts.py:52`) — авто-копируется в `asyncio.create_task`/`to_thread`;
     **in-process**, между процессами НЕ передаётся. Все сторы КРОМЕ `infra_billing_store` уже принимают явный
     `account_id` (готовы к вызову вне запроса); `infra_billing_store` — ContextVar-only (`:35`), единственный
     рефактор для out-of-process.
  2. **Общая ФС** `DATA_DIR/accounts/<id>/` — 11 JSON через `storage.py` + 3 JSON со своими путями
     (hostings/sync/subpages) + 5 SQLite (infra_billing/rules_secrets/server_monitor/node_speedtests/user_stats).
     GLOBAL (не per-account): `accounts.json`, `xray_checker_metrics.db`, `mcp_owner.json`, `.legacy_migrated`.
  3. **Общий `settings.encryption_key`** — подписывает КАЖДЫЙ JWT И выводит КАЖДЫЙ Fernet-волт (SHA-256). Любой
     сервис, проверяющий токен ИЛИ читающий волт, нуждается в этом секрете.
- **`task_store.py` — in-memory** (`Task`-реестр + live-log SSE). WS-лог-стрим (`ws.router`) отдаёт логи по
  `task_id` из этой же памяти. Долгие операции (`deploy`, `node_ops`, `panel_deploy`, `panel_sync`, `migrate`,
  `replace_domain`) стримят в него из того же процесса → **разделить worker и WS-стрим нельзя без общего
  хранилища задач/логов**.
- **Frontend — один SPA** (`node-installer-frontend`, nginx), ходит на `/api` и `/ws` через тот же nginx-прокси.
- Деплой job-карточки и panel-карточки живут в браузерном `localStorage` (`deploy_jobs_<id>`/`panel_jobs_<id>`),
  НЕ на сервере — это упрощает распил (клиент хранит своё состояние сам).

## Развилки (закреплены)
- **Strangler, не rewrite.** Дробим по одному сервису за раз, монолит остаётся API-gateway и «домом по умолчанию»
  для всего, что ещё не вынесено. Никаких «переписать backend на event-bus» — только вынос существующего кода в
  отдельный процесс с тем же кодом.
- **Распил — опциональный, за compose-профилями.** Дефолтный `docker compose up` продолжает поднимать монолит
  (single-operator-friendly). Вынесенные сервисы включаются профилем (`--profile split` / env-флаг), backend
  проксирует к ним, когда они доступны, иначе исполняет сам (фолбэк). «В фоне не переспрашивать»: если сервис
  не поднят — работает встроенная реализация.
- **Общий том + общий `encryption_key` на первом этапе** (не per-service БД, не секрет-менеджер). Все сервисы
  монтируют `node-data` и получают `ENCRYPTION_KEY` из `.env` — это самый дешёвый корректный путь на одном хосте
  (SQLite WAL по общему тому между контейнерами одного хоста работает). Разделение владения данными и раздачу
  секрета через vault — вынести в «later»-бэклог, не в эту волну.
- **Сервис-в-сервис аутентификация = наш же JWT/`require_account`** + **План H API-токены** для машинных
  вызовов (долгоживущий отзываемый токен вместо сессионного). `account_id` между процессами передаётся ТОЛЬКО
  внутри подписанного токена/заголовка, никогда «на доверии».
- **Границы сервисов (закреплённый список кандидатов, по существующим швам):**
  - `gateway` — остаётся монолит-`backend`: auth, settings, лёгкие CRUD-роутеры, маршрутизация к сателлитам.
    **auth/accounts НЕ дробим** — это корень доверия, живёт в gateway.
  - `deploy-worker` — тяжёлый SSH-пайплайн и операции над узлами/панелями: `deploy`, `node_ops`, `panel_deploy`,
    `panel_sync`, `migrate`, `replace_domain`, `certwarden`, `netbird`, `testservers`, `speedtest`. Потребляет
    задачи из разделяемого стора, стримит логи туда же.
  - `monitoring` — 5 лупов + их сторы: `poller_loop`/`collector_loop`/`rules_loop`/`autostart_checker`/
    `monitor_loop`; читающие эндпоинты `/api/checker/*`, `/api/stats/*`, `/api/server-monitor/*`, `/api/rules`
    (evaluation). Метрик-DB (`xray_checker_metrics.db`, `user_stats.db`, `server_monitor.db`, `node_speedtests.db`).
  - `library` — файловое хранилище/извлечение/поиск (создаётся Планом C — **проектировать его сразу как
    отдельный сервис** проще, чем выносить потом).
  - `billing` — `infra_billing` (self-contained, свой DB, свой Fernet-волт) — кандидат «под конец».
  - `ai`/`mcp` — уже DooD-контейнеры; формализовать как сервисы (минимальные изменения).
- **Что НЕ дробить (закреплено):** auth/accounts; SPA (один фронт); мелкие CRUD (domains/hosts/traffic/templates/
  subscriptions/subpages/hostings — остаются в gateway, их вынос не окупается). Не вводить брокер сообщений
  (Kafka/RabbitMQ) — для нашей нагрузки достаточно разделяемого SQLite-стора задач или, максимум, Redis (и то —
  «later»).
- **Критерий остановки (закреплено):** останавливаемся, как только вынесены `deploy-worker` и `monitoring`
  (изоляция самого тяжёлого и самого «шумного» кода). Дальнейший распил (library/billing/ai) — по потребности,
  не обязателен для «готовности плана M».

## Стратегия
Ф1 (подготовка: убрать блокеры распила — infra_billing explicit-account, разделяемый task/queue-стор, контракт
сервис-аутентификации) → Ф2 (вынести `monitoring` — самый безопасный шов) → Ф3 (вынести `deploy-worker` + мост
логов/WS) → Ф4 (опц.: `library`/`billing`/`ai` как сервисы) → Ф5 (gateway-консолидация, наблюдаемость,
критерии отката) — одной строкой.

---
### Ф1 — Подготовка: развязать связки изоляции (БЕЗ распила) → verify: pytest (поведение не меняется)
- `services/infra_billing_store.py`: добавить явный параметр `account_id: Optional[str] = None` во ВСЕ публичные
  функции (сейчас ContextVar-only, `:35`), резолвя `account_id or accounts.current_account.get()` — по образцу
  `speedtest_store`/`user_stats_store`/`server_monitor_store`. Это единственный стор, мешающий вызову вне
  request-контекста. Роуты `api/infra_billing.py` не меняются (ContextVar-путь остаётся дефолтом).
- `services/task_store.py`: ввести **абстракцию хранилища задач/логов** с двумя реализациями за одним
  интерфейсом: `InProcessTaskStore` (текущая, дефолт) и `SharedTaskStore` (SQLite под `DATA_DIR/tasks.db`:
  таблицы `tasks(id, account_id, kind, status, step, …)` + `task_logs(task_id, ts, line)`, WAL). Выбор — env
  (`TASK_STORE=memory|shared`). Это разблокирует Ф3 (worker пишет логи, gateway их стримит) без изменения
  вызывающего кода. WS-стрим (`ws.router`) читает из выбранной реализации (для `shared` — tail по task_id).
- Контракт **сервис-в-сервис аутентификации**: helper `service_auth.py` — (a) выпуск/проверка внутреннего JWT
  через `accounts.issue_token`/`account_id_from_token` (уже есть), (b) приём **API-токена Плана H** в
  `require_account` (см. `wave5-h-api-tokens.md` — единая точка резолва `auth.py:54`). Внутренние вызовы между
  сервисами несут `Authorization: Bearer <token>` с `account_id` в `sub`; принимающий сервис резолвит так же,
  как HTTP-запрос от SPA. Никаких «доверенных заголовков без подписи».
- Задокументировать модель: общий том `node-data` (все сервисы монтируют), общий `ENCRYPTION_KEY` из `.env`
  (JWT + все Fernet-волты), общая сеть `node-assistant-net` (обращение по имени контейнера).
- verify: `pytest` (infra_billing/task_store — поведение идентично при дефолтных env; новые тесты на
  `SharedTaskStore` round-trip и на explicit-`account_id`); монолит стартует без новых сервисов как раньше.
---
### Ф2 — Вынести сервис `monitoring` → verify: pytest + docker compose + preview (дэшборды живы)
- Новый compose-сервис `monitoring` (профиль `split`) на `node-assistant-net`, тот же образ backend, но
  **entrypoint запускает ТОЛЬКО лупы** (`poller_loop`/`collector_loop`/`rules_loop`/`autostart_checker`/
  `monitor_loop`) + отдаёт читающие эндпоинты `/api/checker/*`, `/api/stats/*`, `/api/server-monitor/*`. Монтирует
  `node-data` (метрик-DB), получает `ENCRYPTION_KEY`, ходит в Remnawave/чекер как сейчас.
- `main.py`: лупы стартовать в lifespan **только если `RUN_WORKERS` не отдан сервису** (env-гейт) — в gateway
  при `--profile split` лупы выключены, их поднимает `monitoring`. Иначе (дефолт) — как сейчас, всё в одном.
- Gateway: при включённом профиле проксировать `/api/checker|stats|server-monitor` к `monitoring` (по имени
  контейнера), иначе обслуживать локально. Реюз паттерна DooD-«ходим по имени контейнера на общей сети».
- Тонкость: `autostart_checker`/`poller` управляют ОБЩИМ xray-checker-контейнером через docker.sock →
  `monitoring` тоже монтирует `/var/run/docker.sock` (как backend сейчас). MCP-owner-логика не затрагивается.
- verify: `docker compose --profile split config` валиден; поднять gateway+monitoring; дэшборд/статистика/
  server-uptime отдаются (preview); `pytest` на разделённых роутерах; выключение профиля → монолит-режим
  работает без регрессий.
---
### Ф3 — Вынести `deploy-worker` + мост логов/WS → verify: pytest + ручной полный деплой со стримом
- Новый compose-сервис `deploy-worker` (профиль `split`), тот же образ, entrypoint = воркер, читающий задачи из
  `SharedTaskStore` (Ф1). Монтирует `node-data`, имеет `ENCRYPTION_KEY`, docker.sock (для DooD-операций панели),
  SSH-исходящий доступ.
- Поток: SPA → gateway `POST /api/deploy` (и родственные) → gateway **создаёт задачу** в `SharedTaskStore` и
  возвращает `task_id` (не исполняет сам при `--profile split`) → `deploy-worker` берёт задачу, гоняет
  `pipeline.run_pipeline`/`node_ops`/`panel_*`, **стримит логи в `task_logs`** → gateway `ws.router` тейлит
  `task_logs` по `task_id` и отдаёт в тот же per-task SSE/WS, что и сейчас (фронт `useTaskStream` не меняется).
- **14-шаговый пайплайн НЕ меняется** (индекс-инвариант, §6) — переезжает целиком в worker как есть. SSH-креды
  по-прежнему per-request (передаются в теле задачи, живут в задаче до завершения, не персистятся в открытом
  виде дольше исполнения — сохранить это свойство: тело задачи с кредами хранить только на время выполнения,
  затирать по завершении, либо шифровать поле кредов Fernet-волтом).
- Фолбэк: при выключенном профиле gateway исполняет пайплайн в своём процессе через `InProcessTaskStore` (текущее
  поведение) — ноль изменений для монолит-деплоя.
- verify: `pytest` (генераторы задач, tail-стрим логов); **ручной полный деплой ноды** в split-режиме — карточка
  проходит 14/14, логи стримятся в реальном времени, FAILED/retry работают; тот же деплой в монолит-режиме.
---
### Ф4 — (опц.) `library` / `billing` / `ai`-`mcp` как сервисы → verify: pytest + preview
- `library` (План C) — проектировать сразу как отдельный сервис (файлы/извлечение/поиск), gateway проксирует
  `/api/library/*`. Хранилище файлов — свой подкаталог общего тома.
- `billing` — вынести `infra_billing` (свой DB + Fernet-волт) после Ф1-рефактора explicit-`account_id`;
  gateway проксирует `/api/infra-billing/*`.
- `ai`/`mcp` — формализовать существующие DooD-контейнеры как объявленные compose-сервисы (минимум изменений;
  уже сиблинги на сети). Связать с Планом J (opencliproxy) — шлюз AI тоже отдельный контейнер.
- verify: каждый вынос — независимо: `pytest` роутера + preview соответствующего раздела; профиль off → монолит.
---
### Ф5 — Gateway-консолидация, наблюдаемость, критерии отката → verify: полный `compose up --profile split`
- Gateway: единая таблица маршрутизации «роутер → локально | проксировать к сервису X» (одно место, env-driven),
  общий `/api/health`, агрегирующий здоровье сателлитов (переиспользовать `container_state`/`reachable`-паттерн).
- Наблюдаемость: каждый сервис логирует свой `service`-тег; gateway отдаёт сводный статус в дэшборд (как
  инфра-статусы сейчас). Без Prometheus-стека (мы и так скрейпим панельные метрики отдельно, §9e).
- **Критерии отката**: любой сервис, будучи выключенным (профиль off / контейнер down), НЕ роняет продукт —
  gateway исполняет фолбэк локально. Это и есть «безопасность распила»: split — оптимизация, не зависимость.
- verify: `docker compose --profile split up` — все флоу (деплой со стримом, дэшборд/статистика, панель-операции,
  библиотека, биллинг, AI-чат) работают; затем `docker compose up` (без профиля) — тот же продукт в монолите;
  оба зелёные.

## РАЗВЕДКА (факты)
- **Точки связывания изоляции (из инвентаря auth/сторов):** (i) `current_account` ContextVar (`accounts.py:52`)
  — in-process, между сервисами не передаётся → account_id только внутри подписанного токена; (ii) общая ФС
  `DATA_DIR/accounts/<id>/` — 11 JSON (`storage.py`) + 3 JSON со своими путями (hostings/sync/subpages) + 5
  SQLite (infra_billing/rules_secrets/server_monitor/node_speedtests/user_stats); (iii) общий `encryption_key`
  = подпись JWT + вывод всех Fernet-волтов (SHA-256).
- **Единственный стор, не готовый к out-of-process:** `infra_billing_store` (ContextVar-only, `:35`). Все прочие
  сторы уже принимают явный `account_id` (`speedtest_store`/`user_stats_store`/`server_monitor_store`/
  `rules_store`) — распил не требует их трогать.
- **`task_store` — in-memory**, WS-лог-стрим (`ws.router`, capability по `task_id`) читает из той же памяти →
  вынос воркера требует разделяемого стора задач/логов (SQLite достаточно для одного хоста; брокер не нужен).
- **DooD-прецедент уже в коде:** `services/xray_checker.py` и `services/mcp_server.py` поднимают сиблинг-контейнеры
  на `node-assistant-net` через хостовый `/var/run/docker.sock`, минтят JWT из `accounts.issue_token`, секреты
  отдают через **0600 `--env-file`** (не argv — вне `ps`/`/proc/cmdline`), ходят по имени контейнера. Это готовый
  шаблон сервис-в-сервис.
- **Уже существующие сервисы на общей сети:** `subs-aggregator` (обычный compose-сервис), `xray-checker`,
  `node-installer-mcp` (DooD). Сеть с явным именем `node-assistant-net` (`docker-compose.yml:98`,
  env `XRAY_CHECKER_NETWORK`).
- **Fernet-переносимость:** любой сервис, читающий волт (`*_enc` в settings, `infra_billing.db`/`rules_secrets.db`/
  `netbird.json`), должен иметь тот же `ENCRYPTION_KEY` — на одном хосте раздаётся через `.env` (общий), что
  корректно. Кросс-хостовый распил (разные ключи) — вне этой волны (связать с политикой шифрования Плана L).
- Источники: `backend/app/main.py:8-62,96-144` (роутеры + lifespan-лупы), `services/{accounts,task_store,
  infra_billing_store,xray_checker,mcp_server}.py`, `docker-compose.yml`, инвентарь per-account-сторов (R9).

## Критерии готовности плана M
- Ф1 выполнена: `infra_billing_store` принимает явный `account_id`; есть `SharedTaskStore` (SQLite) за
  интерфейсом с дефолтом `memory`; есть контракт сервис-аутентификации (JWT/`require_account` + API-токены
  Плана H). Монолит-режим (дефолтный `compose up`) не изменил поведение — `pytest` зелёный.
- Вынесены **как минимум** `monitoring` (Ф2) и `deploy-worker` (Ф3): при `--profile split` дэшборды/статистика
  и полный деплой со стримом логов работают через отдельные процессы; при выключенном профиле всё исполняется
  в монолите (фолбэк), без регрессий.
- 14-шаговый пайплайн и per-account изоляция сохранены; SSH-креды и секреты не оседают в открытом виде (env-file
  0600 / Fernet, как в DooD-прецеденте).
- Распил **опционален и обратим** (профиль/флаг + фолбэк) — ни один вынесенный сервис не является жёсткой
  зависимостью продукта; критерии отката из Ф5 выполнены.
- `docker compose --profile split config` валиден; `docker compose --profile split up` и `docker compose up`
  (монолит) оба поднимают рабочий продукт; `pytest` + preview ключевых флоу зелёные.
- Опциональные выносы (library/billing/ai — Ф4) задокументированы как «по потребности», не блокируют готовность.
