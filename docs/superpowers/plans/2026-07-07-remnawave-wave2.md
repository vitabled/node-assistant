# Remnawave-модуль (Волна 2): движок правил · MCP+ИИ · синхронизация · миграция · Профили

## Контекст

Продолжение программы node-installer (FastAPI + React/TS/Vite/Tailwind, per-account isolated). Волна 1 —
фундамент (speed-тесты, установка панели/подписки, каталог подписок, бэкап). **Волна 2 — отложенное тяжёлое:**
(1) единый движок правил «условие→действие» (разделы «Уведомления» + «Автоматизация»); (2) MCP-сервер наружу
+ встроенный ИИ-агент (вкладка Settings «mcp»); (3) синхронизация панелей по приоритету (бэкап→restore
standby); (4) интерактивная миграция marzban→remnawave; (5) раздел «Профили» = порт xray-config-ui-editor.

**Волна 2 предполагает, что Волна 1 применена** (см. план `2026-07-07-remnawave-panel-wave1.md`): существуют
сайдбар-группа «Remnawave» с заглушками `rw-migration`/`rw-profiles` (Волна 2 заменяет их реальным контентом),
`panel_jobs_<id>` + `PanelWidget` (синхронизация группирует их), `backup_service.py` + `/api/backup/*` (синк
переиспользует), `test_tools.py`. Если Волна 1 не отработала — фазы деградируют (создают недостающие каркасы
защитно) и пишут расхождение в журнал.

Закреплённые развилки (Alignment + дефолты, в фоне не переспрашивать):
- **Миграция** — ОБЁРТКА официального `remnawave/migrate` (Go, AGPL-3.0, актив., v2.1.0) для юзеров: читает
  Marzban по его REST API (URL + admin-креды), пишет в Remnawave по API (Bearer), с `--preserve-status`/
  `--preserve-subhash`/`--internal-squad`. Плюс тонкие хелперы: миграция Reality-inbounds (Marzban
  `/api/core/config` → Remnawave config-profiles) и чтение `jwt.secret_key` для legacy-ссылок. Цель = панель из
  `panel_jobs` ИЛИ сторонняя (URL+токен). *Ревизия дефолта под разведку: зрелый официальный мигратор надёжнее
  ручного DB-ридера (`proxies.settings`/inbounds — ORM-версионны). AGPL ок — запускаем бинарь отдельным
  процессом, исходники НЕ вендорим.*
- **ИИ-агент** — провайдеры: OpenAI-совместимый (`base_url/api_key/model`, покрывает OpenAI/ollama/LM Studio)
  + отдельно Anthropic. *Вместо litellm-слоя — не тащим тяжёлую зависимость ради двух клиентов.*
- **MCP** — И сервер наружу (форк `TrackLine/mcp-remnawave`, бамп API + инструменты node-assistant + HTTP/SSE
  транспорт), И встроенный чат-агент, который зовёт те же инструменты.
- **Синхронизация** — standby по расписанию (+ кнопка) забирает свежий бэкап primary и восстанавливает; БЕЗ
  авто-failover (переключение трафика — вручную). *Авто-failover отвергнут: split-brain/ложные срабатывания —
  прод-риск.*
- **Движок правил MVP** — триггеры: нода down в xray-checker N мин, Remnawave webhook-события, cron; действия:
  telegram, скрыть/показать хосты, disable/enable нод/юзеров. «Уведомления» = подмножество с action=telegram.

## Стратегия (порядок ведёт зависимость)

- **Движок правил**: Ф1 (бэкенд: стор правил + эвалюатор + telegram + actions + webhook-приёмник) → Ф2
  (frontend: «Автоматизация» билдер + «Уведомления»).
- **MCP+ИИ**: Ф3 (MCP-сервер: форк+бамп API + node-assistant tools + HTTP-транспорт + деплой + вкладка mcp с
  endpoint/токеном) → Ф4 (встроенный ИИ-агент: провайдер-конфиг + agent-loop + чат-UI, зовёт MCP-инструменты).
- **Синхронизация**: Ф5 (бэкенд: стор групп/приоритетов + оркестратор standby-синка + планировщик) → Ф6
  (frontend: группировка виджетов + приоритеты + статус/триггер).
- **Миграция**: Ф7 (бэкенд: marzban-ридер + маппер + Remnawave-writer) → Ф8 (frontend: дэшборд миграции).
- **Профили**: Ф9 (порт xray-config-ui-editor: схемы + стор + модалки + JSON-редактор + ajv; топология/воркеры —
  опционально/отложено).

Общие контракты: `rules` стор + `POST /api/rules/*` + webhook-приёмник `/api/webhooks/remnawave` (Ф1,
потребляет Ф2); MCP endpoint+токен + node-assistant tool-набор (Ф3, потребляет Ф4); группы/приоритеты панелей
(Ф5, потребляет Ф6); marzban-маппер + `POST /api/migrate/*` (Ф7, потребляет Ф8).

**Секреты at-rest — модуль-скоупное исключение (как инфра-биллинг):** движку правил (фон) нужен telegram
bot-token, ИИ-агенту — provider api_key, MCP — свой токен, синку — доступ к бэкапам. Фоновые эвалюаторы/агент
не имеют per-request кредов → эти секреты хранятся **зашифрованными Fernet** (ключ = SHA-256
`settings.encryption_key`, паттерн `infra_billing_store`), НЕ в открытом виде, в клиент не возвращаются (маска).
Это осознанно расширяет исключение no-secrets-at-rest на данный модуль. SSH-креды панелей/нод остаются
per-request (localStorage), в БД не пишутся.

## Карта кодовой базы (что уже есть — читается каждой фазой)

**Ядро (как в Волне 1):** `services/accounts.py` (`current_account` ContextVar, per-account `DATA_DIR/accounts/<id>/`),
`require_account` (`api/auth.py`); фоновые lifespan-задачи ContextVar НЕ наследуют → итерировать
`accounts.list_accounts()` с явным `account_id` (эталон `xray_checker.poller_loop`). `storage.py` (per-account
JSON), `infra_billing_store.py` (per-account SQLite + **Fernet-vault** секретов — эталон для секретов at-rest).
`ssh_manager.SSHSession`, `task_store.Task` (стрим `/ws/logs/{id}`), `metrics_store` (xray-checker down-сигнал),
`checker_registry.py`, `remnawave_client.py` (Bearer-токен панели; методы `list_nodes`/`get_users_in_squad`/
`create_node`/`get_internal_squad`/`add_inbounds_to_internal_squad`; **добавить** `create_user`/`update_host`/
`list_hosts` где нужно). `main.py::lifespan` (реестр фоновых задач). `api-1.json` — Remnawave OpenAPI v2.8.0.

**Frontend:** `Sidebar.tsx` (Tab-union, группы), `Settings.tsx` (вкладки), `App.tsx` (`<Toaster/>`, роутинг,
топбар), `hooks/useTaskStream.ts`, `auth/store.ts` (per-account ключи). Инлайн-SVG стиль виджетов
(Dashboard/InfraDashboard), без внешних chart-либ.

**Внешние источники (сверено разведкой Волны 1, факты для фаз):**
- **`TrackLine/mcp-remnawave`** (Ф3): TS + `@modelcontextprotocol/sdk ^1.12.1` (high-level `McpServer`), пути/DTO
  из `@remnawave/backend-contract ^2.6.27` → **апдейт API = бамп пакета до 2.8.x** (нет хардкод-URL). 153 tool'а
  в 20 модулях (`users/nodes/hosts/inbounds/squads/subscriptions/...`), read-only фильтр (`REMNAWAVE_READONLY`).
  Auth: `REMNAWAVE_BASE_URL`+`REMNAWAVE_API_TOKEN` (Bearer) + опц. `X-Api-Key`. **Транспорт stdio only → добавить
  HTTP/SSE** для внешних клиентов. MIT, Docker есть.
- **`bropines/xray-config-ui-editor`** (Ф9): React19/Vite7/Tailwind4 + Zustand5+Immer, **без бэкенда** (SPA).
  Ядро (портируется чисто): `core/xray/schemas/*` (Zod→ajv, секции inbound/outbound/routing/dns/policy/reverse/
  fakedns/...), `store/configStore.ts`, `components/editors/*Modal.tsx` (GUI-формы), CodeMirror6 raw-JSON,
  генераторы (X25519 `tweetnacl`, WARP), `link-parser/generator`. **Тяжёлое/опционально:** топология
  (`@xyflow/react`+`dagre`), web-воркеры (geo/proto). MIT (© bropines).
- **`distillium/remnawave-backup-restore`** (Ф5): `/opt/rw-backup-restore`, `rw-backup`; `create_backup` (pg_dumpall
  + tar `/opt/remnawave`), `restore_backup` (ДЕСТРУКТИВЕН — чистит том `remnawave-db-data`), `setup_auto_send`
  (cron), аплоад TG/S3/GDrive. Волна 1 обернула это в `backup_service.py` + `/api/backup/*`.
- **Remnawave webhooks** (Ф1 — источник триггеров): `.env` `WEBHOOK_ENABLED/WEBHOOK_URL/WEBHOOK_SECRET_HEADER`;
  HMAC-SHA256, заголовки `X-Remnawave-Signature`/`X-Remnawave-Timestamp`; payload `{scope,event,timestamp,data}`;
  события `node.{connection_lost,connection_restored,created,disabled,...}`, `user.*`, `service.*`, `crm.*`.
- **Миграция marzban→remnawave** (Ф7, сверено разведкой):
  - **Официальный `remnawave/migrate`** (github.com/remnawave/migrate, Go, **AGPL-3.0**, v2.1.0 2026-01-31): читает
    Marzban по **REST API** (`--panel-url`+`--panel-username/password`), пишет Remnawave по API
    (`--remnawave-token` Bearer, батчами). Мигрирует ТОЛЬКО per-user: `username,status,shortUuid,trojanPassword,
    vlessUuid,ssPassword,trafficLimitBytes,trafficLimitStrategy,expireAt,createdAt,description` + squad-назначение.
    Флаги: `--preserve-status` (иначе все ACTIVE), `--preserve-subhash` (сохранить sub-hash), `--internal-squad`/
    `--external-squad` (UUID-список), `--batch-size`, env-эквиваленты. **НЕ мигрирует:** inbounds/hosts/configs,
    админов, ноды, историю трафика; `YEAR`→`NO_RESET`.
  - **Reality-inbounds** (что тул НЕ умеет): `ryabkov82/vff-remnawave-auto` (Ansible) тянет из Marzban
    `GET /api/core/config` `realitySettings{privateKey,shortIds,serverNames}` и патчит существующий Remnawave
    config-profile inbound того же tag (`GET /api/config-profiles/{uuid}`). Не добавляет/не удаляет inbounds.
  - **Legacy-ссылки:** `subscription-page` env `MARZBAN_LEGACY_LINK_ENABLED=true`+`MARZBAN_LEGACY_SECRET_KEY`
    (+`REMNAWAVE_API_TOKEN`+`CUSTOM_SUB_PREFIX`) → старые marzban-ссылки резолвятся в новых юзеров. Секрет:
    `SELECT secret_key FROM jwt LIMIT 1;` на Marzban-БД (единственное оправданное прямое чтение БД).
  - **Marzban data model:** SQLite `/var/lib/marzban/db.sqlite3` или MySQL; таблицы `users`/`proxies`(settings
    JSON: vlessUuid/пароли)/`inbounds`(tag)/`hosts`/`admins`/`jwt`(secret_key)/`node_user_usages`(история).
  - **Remnawave target:** `POST /api/users` — required `username`+`expireAt`; опц. креды/лимиты/статус +
    `activeInternalSquads: string[]` (сквады+config-profiles надо создать ЗАРАНЕЕ, тул их не строит).

## Риски

- **Секреты at-rest:** telegram-токен/AI-ключ/MCP-токен неизбежны в БД (фон не имеет per-request кредов) →
  ТОЛЬКО Fernet-vault (паттерн `infra_billing_store`), маска в ответах, не логировать. Слабый `ENCRYPTION_KEY`
  в проде = утечка — предупредить в доке.
- **Движок правил — ложные/повторные срабатывания:** нода «мигает» в xray-checker → флаппинг действий.
  Смягчение: дебаунс/гистерезис (N минут подряд), идемпотентность действий, кулдаун на правило, dry-run режим.
- **Actions против прод-панели** (скрыть хосты, disable ноды): необратимо влияет на юзеров. Смягчение: правило
  по умолчанию `disabled`, явное включение, «обратное действие» при восстановлении (connection_restored), лог.
- **MCP наружу:** сервер даёт AI-клиенту контроль над remnawave/node-assistant. Смягчение: токен-гейт на
  HTTP-транспорте, read-only режим по умолчанию, scoping инструментов.
- **Синк restore ДЕСТРУКТИВЕН** (чистит том БД standby): двойной confirm, синк только на явно помеченный standby,
  никогда на primary; проверка «это standby, а не primary» перед restore.
- **Миграция пишет в прод-панель** (создаёт юзеров): dry-run + предпросмотр + confirm; идемпотентность по
  username. Без `--preserve-status`/`--preserve-subhash` официальный тул делает всех ACTIVE и меняет sub-hash —
  всегда ставить оба флага. Inbounds/Reality/история трафика тулом НЕ переносятся → отдельный шаг Reality +
  `MARZBAN_LEGACY_LINK_ENABLED` (иначе клиентские ссылки/Reality-ключи ломаются). Сквады+config-profiles создать
  ДО миграции. **AGPL-бинарь запускать процессом (SSH/контейнер), исходники не вендорить.**
- **Профили-порт:** Tailwind v4 у источника vs версия node-assistant (сверить, избежать конфликта конфига);
  топология/воркеры тяжёлые — вынести в опциональную под-фазу, не блокировать ядро.

## Фаза 1 — Движок правил (бэкенд): стор + эвалюатор + telegram + actions + webhook-приёмник
<!-- circle: status=pending order=10 deps=[] autonomy=auto obstacle="" -->

**Подход:** единый rules-engine (триггеры→условия→действия) вместо двух систем — «Уведомления» = его частный
случай (action=telegram). Триггеры MVP: xray-checker down N мин, Remnawave webhook-события, cron. Отвергнуто:
отдельные нотификации + отдельная автоматизация — дублирование эвалюатора и UI.

**Файловый манифест:**
- создать `backend/app/services/rules_store.py` — per-account стор правил `accounts/<id>/rules.json`
  `Rule{id, name, enabled, trigger{type:'xray_down'|'webhook'|'cron', params}, conditions[](and/or),
  actions[]{type:'telegram'|'hide_hosts'|'show_hosts'|'node_disable'|'node_enable'|'user_disable'|'user_enable',
  params}, cooldown_sec, dry_run}`; секреты (telegram bot-token) — в Fernet-vault (паттерн `infra_billing_store`).
- создать `backend/app/services/rule_actions.py` — исполнители действий: `send_telegram`, `hide_hosts`/
  `show_hosts` (Remnawave `PATCH /api/hosts` bulk disable/enable по профилям ноды), `node_disable/enable`,
  `user_disable/enable` (через `remnawave_client`, +новые методы). Идемпотентно, с логом.
- создать `backend/app/services/rule_engine.py` — эвалюатор: чистая функция `evaluate(rule, event/state)`
  (тестируемо) + гистерезис/кулдаун; `services/telegram.py` — отправка (bot-token из vault, не логировать).
- изменить `backend/app/main.py` — lifespan-задача `rules_loop` (итерирует аккаунты, тикает xray-checker/cron
  триггеры, вызывает эвалюатор→actions); паттерн `poller_loop`.
- создать `backend/app/api/rules.py` — `GET/POST /api/rules`, `PATCH/DELETE /api/rules/{id}`, `POST /api/rules/
  {id}/test` (dry-run), `POST /api/webhooks/remnawave` (HMAC-verify по `WEBHOOK_SECRET_HEADER`, парс события →
  прогон правил). Роутер в `main.py`; webhook-приёмник — под capability-верификацией подписи (не `require_account`).

**Шаги:** стор+vault → actions-исполнители → эвалюатор+telegram → lifespan `rules_loop` → API + webhook-приёмник.

**Edge-cases:** флаппинг ноды (гистерезис N мин + кулдаун); дубль действия (идемпотентность); неверная
HMAC-подпись webhook (401, не прогонять); недоступный Remnawave/telegram (лог, не падать); правило `enabled=false`
(скип); dry-run (не выполнять, вернуть план); пустой набор правил; секрет в логах (redactor).

**Verify-гейт (исполняемый смоук):** юнит `evaluate` на фикстурных (xray_down 6 мин → сработало, 3 мин → нет,
кулдаун); `curl POST /api/webhooks/remnawave` с валидной/невалидной HMAC → 200/401 и прогон; `POST /api/rules/
{id}/test` (dry-run) с моком Remnawave/telegram → вернул план действий, ничего не отправил. `python -m py_compile`.

**Контракт:** `POST /api/rules/*` + webhook-приёмник + таксономия trigger/action. следующий шаг: Ф2 строит UI
билдера правил и «Уведомления» поверх этих роутов.

## Фаза 2 — Движок правил (frontend): «Автоматизация» билдер + «Уведомления»
<!-- circle: status=pending order=20 deps=[1] autonomy=auto obstacle="" -->

**Подход:** визуальный билдер «если (and/or) → то» в новой группе «Автоматизация»; «Уведомления» — упрощённый
раздел над настройками (правила с action=telegram + конфиг бота). Отвергнуто: единый экран — ТЗ явно просит
раздел «Уведомления» отдельно (над настройками) и группу «Автоматизация».

**Файловый манифест:**
- изменить `frontend/src/components/Sidebar.tsx` — группа «Автоматизация» с разделом `automation` + пункт
  «Уведомления» (`notifications`, над «Настройки»); Tab-union; `App.tsx` роутинг.
- создать `frontend/src/components/automation/RuleBuilder.tsx` — список правил + редактор: выбор триггера
  (xray_down N мин / webhook-событие / cron), конструктор условий (and/or), список действий (telegram текст с
  плейсхолдерами `$hostname` и т.п. / hide_hosts / node_disable / …), тумблеры enabled/dry_run, «Проверить»
  (dry-run). Инлайн-SVG/формы, var-токены темы.
- создать `frontend/src/components/automation/Notifications.tsx` — конфиг telegram-бота (токен→vault, chat_id) +
  быстрые нотиф-правила (обёртка над `/api/rules` с action=telegram).

**Шаги:** сайдбар-группа+пункт → RuleBuilder (триггеры/условия/действия/dry-run) → Notifications (бот+нотифы).

**Edge-cases:** пустой список правил; невалидное правило (нет действия/триггера — блок сохранения); токен-поле
маскируется (показывать `••••`); dry-run показывает план без выполнения; плейсхолдеры действия
(валидировать); мобильная верстка; session-gate.

**Verify-гейт:** headless — открыть `automation`: создать правило (мок `/api/rules`) → сохранилось; «Проверить»
показывает dry-run-план; `notifications` рендерит конфиг бота; токен замаскирован. `tsc --noEmit`.

**Контракт:** лист домена. следующий шаг: none.

## Фаза 3 — MCP-сервер: форк + бамп API + node-assistant tools + HTTP-транспорт
<!-- circle: status=pending order=30 deps=[] autonomy=auto obstacle="" -->

**Подход:** форкнуть `TrackLine/mcp-remnawave` в репо (`mcp/` под-проект), обновить API (бамп
`@remnawave/backend-contract` до 2.8.x), добавить модуль инструментов node-assistant + HTTP/SSE транспорт (для
внешних клиентов), деплой как контейнер; вкладка Settings «mcp» выдаёт endpoint+токен. Отвергнуто: писать MCP с
нуля — 153 готовых инструмента Remnawave, пути из контракт-пакета (апдейт = бамп).

**Файловый манифест:**
- создать `mcp/` (форк TrackLine/mcp-remnawave, MIT) — `package.json` (бамп `@remnawave/backend-contract`
  `^2.6.27`→2.8.x, `@modelcontextprotocol/sdk` latest), пофиксить TS/DTO-разломы от бампа.
- создать `mcp/src/tools/node-assistant.ts` — инструменты нашей панели (deploy-статусы, per-node stats,
  speedtest, certs, hosts, rules) через второй HTTP-клиент (`NODE_ASSISTANT_BASE_URL`/`NODE_ASSISTANT_TOKEN`,
  JWT Bearer — наш `require_account`); переиспользовать read-only фильтр `registerAllTools(..., readonly)`.
- изменить `mcp/src/index.ts` — добавить **HTTP/SSE транспорт** (в дополнение к stdio) с токен-гейтом; env
  `MCP_HTTP_PORT`/`MCP_AUTH_TOKEN`.
- изменить `docker-compose.yml` (корень) — сервис `mcp` на `node-assistant-net`; изменить `backend/app/services/`
  — оркестрация контейнера (по образцу `xray_checker.py`), выдача endpoint/токена.
- создать `backend/app/api/mcp.py` — `GET/POST /api/mcp/config` (вкл/выкл, генерация `MCP_AUTH_TOKEN`→vault,
  выдача endpoint URL), `GET /api/mcp/status`; `frontend/src/components/settings/McpTab.tsx` — вкладка «mcp»:
  статус, endpoint+токен (копировать), read-only тумблер, инструкция подключения внешнего AI-клиента.

**Шаги:** форк+бамп API (починить разломы) → node-assistant tool-модуль → HTTP/SSE транспорт+токен → контейнер+
оркестрация → API/вкладка mcp (endpoint/токен).

**Edge-cases:** бамп контракта ломает DTO (починить по компайл-ошибкам); node-assistant токен невалиден (401);
HTTP-транспорт без токена (403); контейнер не поднялся (статус «off», не 502); read-only режим (только 69
безопасных инструментов); секрет-токен в vault, маска в UI.

**Verify-гейт (исполняемый смоук):** `npm run build` в `mcp/` проходит после бампа; поднять MCP по HTTP →
MCP-`initialize`/`tools/list` возвращает инструменты (Remnawave + node-assistant); без токена → 403;
`GET /api/mcp/status` = running. `python -m py_compile` (backend) + `tsc --noEmit` (frontend вкладки).

**Контракт:** MCP endpoint + токен + node-assistant tool-набор. следующий шаг: Ф4 (встроенный агент) зовёт эти
же инструменты через MCP-клиент.

## Фаза 4 — Встроенный ИИ-агент: провайдер-конфиг + agent-loop + чат-UI
<!-- circle: status=pending order=40 deps=[3] autonomy=auto obstacle="" -->

**Подход:** встроенный агент (backend agent-loop с function-calling) зовёт MCP-инструменты Ф3; провайдеры
OpenAI-совместимый + Anthropic; чат в вкладке «mcp». Отвергнуто: только внешний MCP-клиент — ТЗ просит «оба»
(и наружу, и встроенный).

**Файловый манифест:**
- создать `backend/app/services/ai_agent.py` — провайдер-абстракция (`{base_url, api_key, model}` для
  OpenAI-совместимого + отдельный Anthropic-клиент); agent-loop с tool-calling, инструменты = проксирование в
  локальный MCP (stdio/HTTP Ф3) ИЛИ прямые вызовы наших сервисов; лимит шагов/токенов. Ключ провайдера — в
  Fernet-vault, не логировать.
- создать `backend/app/api/ai.py` — `GET/POST /api/ai/config` (провайдер/модель/ключ→vault), `POST /api/ai/chat`
  (стрим ответа + видимые tool-calls); под `require_account`, роутер в `main.py`.
- создать `frontend/src/components/settings/AiChat.tsx` — чат в вкладке «mcp» (под McpTab Ф3): история, ввод,
  стрим ответа, отображение вызванных инструментов + результат; конфиг провайдера (маска ключа).

**Шаги:** провайдер-клиенты → agent-loop (tool-calling через MCP) → API config/chat → чат-UI + конфиг.

**Edge-cases:** неверный ключ/модель (понятная ошибка); провайдер недоступен/таймаут; зацикливание агента
(лимит шагов); опасное действие агента (те же confirm/read-only гейты, что у MCP); пустой ключ (агент выключен);
ключ в логах (redactor); большой контекст (обрезка истории).

**Verify-гейт:** мок провайдера (локальный OpenAI-совместимый http-стаб, отдающий tool-call затем финал) →
`POST /api/ai/chat` выполняет tool-call через MCP и возвращает ответ; неверный ключ → понятная ошибка.
Frontend headless — чат рендерит стрим + tool-call. `python -m py_compile` + `tsc --noEmit`.

**Контракт:** лист домена. следующий шаг: none.

## Фаза 5 — Синхронизация панелей (бэкенд): группы/приоритеты + standby-синк + планировщик
<!-- circle: status=pending order=50 deps=[] autonomy=auto obstacle="" -->

**Подход:** бэкап→restore по приоритету (переиспользуя `backup_service` Волны 1): standby по расписанию берёт
свежий бэкап ближайшего-высшего primary и восстанавливает. Без авто-failover. Отвергнуто: live-репликация
PostgreSQL — сложно/рисково; авто-failover — split-brain.

**Файловый манифест:**
- создать `backend/app/services/sync_store.py` — per-account стор групп `accounts/<id>/panel_groups.json`
  `Group{id, name, members[]{panel_key, priority:int, role:'primary'|'standby'}}`; определение
  «ближайший-высший primary» для standby.
- создать `backend/app/services/panel_sync.py` — оркестратор: на standby — забрать свежий бэкап primary (через
  `backup_service`: `create_backup` на primary → перенос → `restore_backup` на standby, ДЕСТРУКТИВНО), с
  проверкой «цель — standby, не primary». SSH-креды per-request/из `panel_jobs` (клиент инициирует).
- изменить `backend/app/main.py` — lifespan `sync_loop` (по расписанию группы, где включён авто-синк).
- создать `backend/app/api/panel_sync.py` — `GET/POST /api/sync/groups`, `PATCH/DELETE`, `POST /api/sync/{group}/
  run` (ручной синк, стрим-Task, confirm-флаг). Роутер в `main.py`.

**Шаги:** стор групп/приоритетов → оркестратор standby-синка (переиспользуя backup_service) → планировщик
lifespan → API (CRUD + ручной run с confirm).

**Edge-cases:** primary недоступен (пропустить тик, лог); restore на standby ДЕСТРУКТИВЕН (проверка роли +
confirm; НИКОГДА на primary); версии PG primary/standby различаются (проверка `backup_meta`); группа из одной
ноды (нет источника); приоритеты-дубли (валидация); синк во время активного использования standby (окно/лог).

**Verify-гейт:** юнит «ближайший-высший primary» на фикстурных приоритетах; `curl POST /api/sync/{group}/run`
(мок backup_service) → вызвал create→restore в нужном порядке, отказал если цель=primary. `python -m py_compile`.

**Контракт:** группы/приоритеты + `POST /api/sync/*`. следующий шаг: Ф6 строит UI группировки виджетов.

## Фаза 6 — Синхронизация (frontend): группировка виджетов + приоритеты + статус
<!-- circle: status=pending order=60 deps=[5] autonomy=auto obstacle="" -->

**Подход:** пометка виджетов панелей (из `panel_jobs`, Волна 1) в группу + расстановка приоритетов + статус/
ручной синк, на дэшборде установки (Tab `rw-install`). Отвергнуто: отдельный экран — синк логично рядом с
виджетами панелей.

**Файловый манифест:**
- изменить `frontend/src/components/rw/PanelDashboard.tsx` — режим «группировки»: пометить несколько
  `PanelWidget` одной группой (`/api/sync/groups`), выставить приоритеты, роль primary/standby.
- изменить `frontend/src/components/rw/PanelWidget.tsx` — бейдж группы/приоритета/роли + статус последнего синка +
  кнопка «Синхронизировать сейчас» (confirm, стрим).
- создать `frontend/src/components/rw/SyncGroupPanel.tsx` — панель управления группой (список членов, приоритеты,
  расписание вкл/выкл, история синков).

**Шаги:** UI группировки (пометка+приоритеты+роли) → бейджи/статус в виджете → панель группы + ручной синк.

**Edge-cases:** нет панелей (пусто); один член (нельзя синкать); confirm на ручной синк (деструктив на standby);
primary недоступен (статус ошибки); мобильная верстка; конфликт приоритетов (валидация в UI).

**Verify-гейт:** headless — создать группу из 2 мок-виджетов, задать приоритеты/роли (мок `/api/sync/*`) →
сохранилось; «Синхронизировать сейчас» → confirm + стрим. `tsc --noEmit`.

**Контракт:** лист домена. следующий шаг: none.

## Фаза 7 — Миграция marzban→remnawave (бэкенд): обёртка remnawave/migrate + Reality/legacy-хелперы
<!-- circle: status=pending order=70 deps=[] autonomy=auto obstacle="" -->

**Подход:** оркестрировать официальный бинарь `remnawave/migrate` (юзеры, Marzban API→Remnawave API) + тонкие
хелперы для того, что он НЕ умеет (Reality-inbounds, legacy-secret). Отвергнуто: ручной DB-ридер юзеров —
`proxies.settings`/inbounds ORM-версионны, официальный тул нормализует их и держит контракт актуальным. AGPL:
бинарь как отдельный процесс, исходники не вендорим.

**Файловый манифест:**
- создать `backend/app/services/marzban_migrate.py` — обёртка: получить бинарь `remnawave-migrate` (docker-образ
  ИЛИ скачать релиз), запуск с `--panel-type=marzban --panel-url/username/password --remnawave-url/token
  --preserve-status --preserve-subhash --internal-squad=<uuids> --batch-size`, стрим stdout в Task; парс итога
  (создано/пропущено). Marzban admin-креды + Remnawave-токен — per-request (или vault, если цель = сторонняя
  сохранённая). НЕ логировать креды/токен.
- создать `backend/app/services/marzban_reality.py` — хелпер Reality-inbounds: Marzban `GET /api/core/config` →
  извлечь `realitySettings{privateKey,shortIds,serverNames}` по tag → пропатчить существующий Remnawave
  config-profile inbound того же tag (через `remnawave_client`: `GET /api/config-profiles`+`{uuid}` → PATCH).
  Не добавляет/не удаляет inbounds (предупредить, если tag не найден). Плюс `read_legacy_secret(marzban_ssh)` —
  `SELECT secret_key FROM jwt LIMIT 1` (единственное прямое чтение БД, для `MARZBAN_LEGACY_SECRET_KEY`).
- изменить `backend/app/services/remnawave_client.py` — при необходимости методы config-profiles
  (`list_config_profiles`/`get_config_profile`/`patch_config_profile_inbound`) для Reality-хелпера.
- создать `backend/app/api/migrate.py` — `POST /api/migrate/preview` (dry-run: подключиться к Marzban API,
  вернуть счётчики юзеров/inbounds/tag-матчинг + отчёт «что не переносится», ничего не писать),
  `POST /api/migrate/reality` (перенос Reality-inbounds), `POST /api/migrate/run` (запуск бинаря, стрим-Task,
  confirm-флаг), `GET /api/migrate/legacy-secret` (для настройки legacy-ссылок). Цель = панель из `panel_jobs`
  (Bearer из её данных) ИЛИ сторонняя (URL+токен). Роутер в `main.py`.

**Шаги:** обёртка бинаря (получить+запуск+стрим+парс) → Reality-хелпер (core/config→config-profile PATCH) +
legacy-secret → методы config-profiles в клиенте → API preview/reality/run/legacy-secret.

**Edge-cases:** бинарь недоступен/не тянется (понятная ошибка, не 500); Marzban API-креды неверны (401);
Remnawave-токен неверен (401); без `--preserve-*` — предупредить (дефолтим оба ВКЛ); tag inbound'а нет в
Remnawave (Reality-хелпер пропускает + отчёт; сквады/профили создать заранее); повтор run (тул идемпотентен по
username); прерывание (тул батчами — переустойчив); сторонняя цель — недоступна; секреты в логах (redactor).

**Verify-гейт (исполняемый смоук):** мок Marzban API (локальный http-стаб `/api/admin/token`+users) +
мок Remnawave → `curl POST /api/migrate/preview` возвращает счётчики + отчёт потерь без записи; юнит парсера
вывода бинаря на фикстурном stdout; Reality-хелпер на фикстурном `/api/core/config` формирует корректный PATCH.
`python -m py_compile`.

**Контракт:** `POST /api/migrate/{preview,reality,run}` + `GET /api/migrate/legacy-secret`. следующий шаг: Ф8 —
дэшборд миграции поверх (источник Marzban API, цель, Reality-шаг, legacy-подсказка).

## Фаза 8 — Миграция (frontend): интерактивный дэшборд
<!-- circle: status=pending order=80 deps=[7] autonomy=auto obstacle="" -->

**Подход:** дэшборд-визард в разделе `rw-migration` (заглушка Волны 1 → реальный контент): источник = Marzban
**API** (URL + admin login/pass), цель (панель из `panel_jobs` ИЛИ сторонняя URL+токен), шаги предпросмотр →
Reality-inbounds → миграция юзеров → legacy-ссылки. Отвергнуто: CLI-only — ТЗ просит «интерактивно».

**Файловый манифест:**
- создать `frontend/src/components/rw/Migration.tsx` — визард: (1) источник Marzban (URL + admin login/password),
  цель (селект панели из `panel_jobs` ИЛИ сторонняя URL+токен), опции `preserve_status`/`preserve_subhash` (дефолт
  вкл), выбор internal-squad(ов); (2) «Предпросмотр» (`/api/migrate/preview` → счётчики юзеров/inbounds + отчёт
  «что НЕ переносится: inbounds/Reality/история»); (3) «Перенести Reality» (`/api/migrate/reality`); (4)
  «Мигрировать юзеров» (confirm + стрим `useTaskStream`); (5) блок legacy-ссылок (`/api/migrate/legacy-secret` →
  показать secret + подсказку env `MARZBAN_LEGACY_LINK_ENABLED` для subscription-page). Лог результата.
- изменить `frontend/src/App.tsx` / `Sidebar.tsx` — заменить заглушку `rw-migration` на `Migration`.

**Шаги:** визард источник/цель/опции → предпросмотр (счётчики + что-не-переносится) → Reality-шаг → миграция
юзеров (confirm+стрим) → legacy-подсказка.

**Edge-cases:** неверные Marzban admin-креды (401); цель недоступна (401); пустой источник (нет юзеров);
предпросмотр показывает потери ДО запуска (inbounds/Reality/история); tag без совпадения в Reality-шаге (отчёт);
confirm на миграцию (пишет в прод); секреты/токены маскируются; длинный список; мобильная верстка.

**Verify-гейт:** headless — визард рендерит источник Marzban-API/цель/опции; «Предпросмотр» (мок
`/api/migrate/preview`) → счётчики + отчёт потерь; «Мигрировать» → confirm + стрим; legacy-блок показывает secret
маскированно. `tsc --noEmit`.

**Контракт:** лист домена. следующий шаг: none.

## Фаза 9 — Профили: порт xray-config-ui-editor (ядро)
<!-- circle: status=pending order=90 deps=[] autonomy=auto obstacle="" -->

**Подход:** портировать ЯДРО `bropines/xray-config-ui-editor` (MIT) как раздел `rw-profiles`: схемы + Zustand-стор
+ секционные модалки + raw-JSON-редактор + ajv-валидация + импорт/экспорт; синк в Remnawave через НАШ бэкенд.
Топология (React Flow+dagre) и web-воркеры (geo/proto) — опционально/отложено (тяжёлые). Отвергнуто: iframe чужого
SPA — нужен единый скин/аккаунт-контекст; стек совпадает (React/Vite/Tailwind) → чистый порт.

**Файловый манифест:**
- создать `frontend/src/components/profiles/core/xray/schemas/*` — портировать Zod-схемы Xray (inbound/outbound/
  routing/dns/policy/reverse/...) как есть; `profiles/store/configStore.ts` (Zustand+Immer); `profiles/core/
  validators` (ajv); `profiles/core/generators` (X25519 tweetnacl, WARP); `profiles/core/link-parser|generator`.
- создать `frontend/src/components/profiles/editors/*Modal.tsx` — секционные GUI-редакторы + `SectionJsonModal`
  (raw-JSON через CodeMirror6) + `DiagnosticsPanel` + `DropZone` (импорт/экспорт).
- создать `frontend/src/components/profiles/Profiles.tsx` — оболочка раздела; заменить заглушку `rw-profiles`
  (App.tsx/Sidebar.tsx). Синхронизация конфига в Remnawave — через наш `remnawave_client` (бэкенд-прокси, НЕ
  прямой browser→panel CORS).
- изменить `frontend/package.json` — добавить зависимости (Zustand, Immer, ajv, CodeMirror6, tweetnacl, …),
  сверить Tailwind-версию во избежание конфликта конфига. **Отложено (не в этой фазе):** `@xyflow/react`+`dagre`
  (топология), web-воркеры geo/proto.

**Шаги:** порт схем+стор+валидаторы+генераторы → секционные модалки+JSON-редактор+диагностика+импорт/экспорт →
оболочка раздела + замена заглушки → синк через бэкенд-прокси.

**Edge-cases:** невалидный конфиг (ajv-ошибки в DiagnosticsPanel, блок синка); конфликт Tailwind v4 (сверить/
адаптировать); большой конфиг (виртуализация списков); импорт битого JSON (отклонить); синк упал (тост); раздел
без выбранного профиля (пусто). Топология/geo — при отсутствии считать фичу «недоступна», не ломать ядро.

**Verify-гейт:** headless — открыть `rw-profiles`: загрузить фикстурный Xray-JSON (DropZone) → секции
распарсились, невалидный конфиг подсвечивается ajv; раскрыть секционную модалку, отредактировать, экспортировать.
`tsc --noEmit`. (Проверка ядра; топология вне гейта.)

**Контракт:** лист домена (Волна 2 завершена). следующий шаг: none.

## Журнал

### Ф1 — движок правил (бэкенд) — ГОТОВО (commit bc19134)
- `services/rule_engine.py` (чистый эвалюатор), `rules_store.py` (JSON + Fernet-vault), `rule_actions.py`, `telegram.py`, `api/rules.py` (gated CRUD + `/test` dry-run + ungated HMAC webhook + `rules_loop`). `remnawave_client`: enable/disable node+user, list/bulk hide-show hosts. `config.webhook_secret_header`, `main.py` wiring.
- Self-review (2 сабагента: code+security) применён. Значимые фиксы:
  - **HIGH** cooldown был per-rule → сделан **per-(rule,node)** (`cooldown_scope`, `mark_fired` read-modify-write); `_xray_down_events` эмитит событие на КАЖДУЮ down-ноду (была только worst → правила с node-фильтром для не-worst не срабатывали).
  - **MED/HIGH** `hide/show_hosts` без селектора прятал ВСЕ хосты → селектор обязателен, иначе отказ.
  - **LOW** anti-replay по подписанному телу (±300с); GC token_ref при `update_rule`; `redact()` во всех exc-логах цикла; валидация uuid до URL-интерполяции.
- Тесты: `test_rule_engine/rule_actions/rules_api`. Полный backend — **413 passed**.

### Ф9 — редактор Xray-профилей (фронтенд) — ГОТОВО (commit d3aa71b)
- `components/profiles/**` (форк bropines/xray-config-ui-editor): `core/{types,schema,validators,diagnostics,crypto,factories,warp,links}`, `store/configStore`, модалки + `Profiles.tsx`. Роут `rw-profiles` в `App.tsx`. Деп: zustand/immer/ajv/tweetnacl/@codemirror.
- Self-review применён. Значимые фиксы:
  - **MED** `crypto`: Math.random → **CSPRNG** для UUID/shortId/spiderX (REALITY-материал).
  - **MED** `links`: реализован **vmess://** парсер (был в placeholder/генераторе, но не парсился).
  - **MED** `validators`+`DiagnosticsPanel`: enum-нарушения (закрытые enum) → **warning**, не блокер синка; `validateBalancer` учитывает ajv-ошибки; убран неиспользуемый `ajv-formats`.
  - **LOW** лимит импорта 5 МБ; лейбл «не синхронизировано».
- Тесты: `crypto/configStore/links(vmess)/validators`. Полный frontend — **167 passed**, tsc чист.

### Ф2 — фронтенд движка правил — ГОТОВО (commit после d3aa71b)
- `components/automation/`: `rulesApi.ts` (общий клиент), `RuleBuilder.tsx` (билдер триггер/условия/действия + dry-run), `Notifications.tsx` (упрощённые telegram-нотифы), `RuleBuilder.test.tsx`. Проводка Sidebar («Автоматизация» группа + «Уведомления» над «Настройки») + App.tsx.
- Self-review применён. Значимые фиксы:
  - **HIGH** «Проверить» на новом правиле создавал orphan-правило + orphan vault-токен при отмене → добавлен stateless `POST /api/rules/test` (dry-run драфта без персиста); фронт превьюит не сохраняя.
  - **MED** `listRules` бросает при не-OK (ошибка ≠ пустой список); `in`-условие → список (иначе backend деградирует в substring); Notifications валидирует minutes>0; toggle/delete-ошибки → toast; 422 форматируется без эха `input` (утечка plaintext-токена).
  - **MED/LOW** пустой bot_token при token_ref вычищается (не затирает vault); служебные `_`-ключи strip; пустое поле условия блокирует сохранение; dry-run-план показывает цели не-telegram действий; убран мёртвый `EMPTY_TRIGGER`.
  - **security** токен existing → `••••`/`type=password`, plaintext не рендерится/не логируется; XSS чисто.
- Тесты: `RuleBuilder.test.tsx` (10) + backend `test_draft_test_endpoint_does_not_persist`. Frontend **177 passed**, backend **414 passed**, tsc чист, build успешен.

### Ф3 — MCP-сервер — ГОТОВО (commits beb2f25 Ф3a + 76573c7 Ф3b)
- **Ф3a** `mcp/` — форк TrackLine/mcp-remnawave (MIT): бамп `@remnawave/backend-contract` 2.6.27→**2.9.14** + починка разломов (`USERS.GET_BY.{TELEGRAM_ID,EMAIL,TAG,SUBSCRIPTION_UUID}`/`HOSTS.BULK.{SET_INBOUND,SET_PORT}` удалены; `IP_CONTROL`→`CONNECTIONS`), zod 3.x. `tools/node-assistant.ts` (read-only в наш backend), Streamable HTTP транспорт (session-based, Bearer-гейт). Smoke: **156 инструментов**, initialize+tools/list, 403.
- **Ф3b** `services/mcp_server.py` (DooD-оркестрация) + `api/mcp.py` + `settings/McpTab.tsx` + compose-сервис `mcp`.
- Self-review (2 сабагента) применён. Значимые фиксы:
  - **HIGH**: owner-маркер (`mcp_owner.json`) — статус аккаунта-не-владельца больше не врёт «running/reachable» (показывает «foreign»); `stop()` не рушит чужой контейнер.
  - **HIGH**: McpTab порт-поле контролируемое+валидация.
  - **MED security**: секреты через `--env-file` 0600 (не argv).
  - **MED**: единая запись settings; GET без сайд-эффекта; `_docker` ловит OSError; `container_state` дизамбигуирует зависший демон; McpTab `res.ok`+422-формат.
  - **security/LOW**: timing-safe токен, кап сессий, `_decrypt` узкий except, guard образа.
  - **Отклонено (обоснованно)**: JWT-exp (правка auth-ядра §1b), DNS-rebinding (Bearer-гейт достаточен), extract docker_cli.
- Тесты: `test_mcp.py` (9), MCP `smoke.mjs`. Backend **423**, frontend **177**, tsc/build чисто.

### Ф4 — встроенный ИИ-агент — ГОТОВО (commit 5b8b4a8)
- `services/ai_agent.py` (agent-loop, OpenAI-совместимый + Anthropic, read-only tools) + `api/ai.py` (config/chat ndjson-стрим) + `settings/AiChat.tsx` (под MCP-вкладкой). `AiConfig` в модели.
- Self-review применён: **HIGH SSRF** (base_url → `net_guard.is_safe_url` фетч-тайм); **HIGH** Anthropic system→top-level; **HIGH** guard парсинга ответа; MED last-step tools-off, unknown-provider→422, `_cfg`-дедуп; frontend patchLast-иммутабельность/AbortController/id-матч; cap истории 4000. Отклонено: extract crypto (4 модуля), httpx-per-turn.
- Тесты: `test_ai.py` (18), `AiChat.test.tsx` (3). Backend **441**, frontend **180**.

### Ф5 — синхронизация панелей (backend) — ГОТОВО (commit f75bd96)
sync_store (группы/приоритеты/nearest_higher_primary) + panel_sync (backup→SFTP-релей→restore) + api/panel_sync. Self-review HIGH: restore восстанавливал ЛОКАЛЬНЫЙ бэкап standby → добавлен реальный SFTP-перенос бандла + restore конкретного бандла; in-flight lock; backup-fail-stops-restore. `test_panel_sync.py` (17).

### Ф6 — синхронизация (frontend) — ГОТОВО (commit cf13a3c)
`rw/SyncGroupPanel.tsx` в PanelDashboard. Self-review HIGH: fresh re-load групп перед вычислением nearest-primary (устаревшие роли); модалка всегда закрываема; guard двойного клика. `SyncGroupPanel.test.tsx` (6).

### Ф7 — миграция (backend) — ГОТОВО (commit c6de305)
marzban_migrate (API+docker-обёртка+парсер) + marzban_reality (Reality-патч+legacy) + api/migrate. Self-review MED: SSRF-гард на каждом фетче Marzban + гард remnawave_url; образ пиннится server-side (не произвольный docker через DooD); security=reality форсится. `test_migrate.py` (18).

### Ф8 — миграция (frontend) — ГОТОВО (commit a4c71a3)
`rw/Migration.tsx` — 5-секционный визард. Self-review (2-агентный заблокирован session-лимитом → самостоятельно): секреты type=password/не логируются, confirm перед migrate, любая операция блокирует все кнопки. `Migration.test.tsx` (5).

### ВОЛНА 2 ЗАВЕРШЕНА
Все 9 фаз готовы (Ф1-Ф9), закоммичены с per-фазным code+security ревью и применёнными фиксами. Backend **474 passed**, frontend **191 passed**, MCP smoke 156 инструментов, tsc/build чисто. Доки CLAUDE.md §8 в синхроне.
