# Node-installer: мониторинг, статистика, готовые ноды, хосты, SSL (реконсилировано под main @ af83e1a)

## Контекст

Развитие панели node-installer (FastAPI + React/Vite, **per-account isolated**) по ТЗ из 10+ пунктов.
Исходный план (та же дата) был написан на базе, отставшей от `main` на 43 коммита — до аккаунтов/авторизации,
apple-скина/мобилки, 13-шагового пайплайна и работ Ф1–Ф11. Ветка пересоздана от актуального `main`,
план переписан под реальную кодовую базу (разведка 4 агентами + сверка `api-1.json`).

## Карта кодовой базы (main @ af83e1a — что уже есть, читается каждой фазой, не переделывать)

- **Аккаунты + per-account изоляция** (§1b CLAUDE.md): `services/accounts.py` (`current_account` ContextVar,
  `data_dir(aid)`), `require_account` (`api/auth.py`) ставит ContextVar per-request. **Фоновые lifespan-задачи
  ContextVar НЕ наследуют** — обязаны итерировать `accounts.list_accounts()` и передавать `account_id` явно
  (эталон — `xray_checker.poller_loop`; per-account стор — `storage.py` с параметром `account_id: Optional`).
  Per-account SQLite-эталон — `infra_billing_store.py` (но у него нет explicit-`account_id`; для фонового
  сборщика брать паттерн `storage.py`).
- **Xray-checker = ОДИН общий контейнер + ОДНА общая метрик-БД (`xray_checker_metrics.db`, глобальная) +
  ОДИН `subs-aggregator`.** Изоляция аккаунтов — тегом в имени прокси `<account_id>:<sub_id>|<orig>`
  (`_parse_tag`/`_filter_by_account` в `api/xray_checker.py`). `proxy_samples(id,ts,stable_id,name,group_name,
  online,latency_ms)` — **колонки `checker_id` НЕТ**. `poller_loop` тикает раз в тик (один общий `_sample_once`),
  а не per-account. Управление чекером (`CheckerControls`) + мульти-подписки (`SubscriptionSelector`) сейчас
  на **Dashboard** (перенесены туда в Ф9 прошлой работы), не в Settings.
- **13 шагов деплоя** (`STEP_LABELS`/`DEPLOY_STEPS`), `StepProgress` уже рендерит **сворачиваемые группы**
  `STEP_GROUPS` (Оптимизация ОС 3-4 / Сеть 5-8 / Установка remnanode 10-13) поверх плоского `currentStep:int`.
  Нумерованного дерева с подшагами (3.1/3.2) НЕТ.
- **`node_ops.py`**: таксономия `Component = node_accelerator|trafficguard|remnanode|masking|warp|hysteria2|ssl|
  haproxy`, `Action = reinstall|reconfigure|uninstall`, `_reinstall` (диспатч на `pipeline.step_*`),
  `_UNINSTALL_SCRIPTS` (dict). Идеальная база для детекта. Шаги 1/2 и SSH-порт (5-8) намеренно НЕ manageable.
- **SSL-панель уже готова**: `CertsForm` (провайдер-селектор cloudflare/letsencrypt/zerossl + force),
  `DomainsPanel` (домены из `deploy_jobs_<id>` + ручные `/api/domains`, проба срока через `/api/stats/node`,
  green≥14д/amber<14д/red), `api/certs.py::POST /api/certs/deploy` (`build_ssl_script`/`ssl_needs_cf_dns`),
  `api/domains.py` (CRUD). **Нет** роутов листинга/скачивания сертов.
- **«Хосты» (Ф11) — чисто локальный стор** `accounts/<id>/hosts.json` (`HostTemplateBody` ~25 полей,
  `api/hosts.py` CRUD, `Hosts.tsx` редактор). **Никогда не применяется при деплое.** В `remnawave_client.py`
  **нет** `create_host`/`list_hosts`. `Template{id,name,config,is_default}` — БЕЗ поля hosts.
- **Инфра-биллинг**: вкладка `signin` и PIN-гейт **удалены** (аккаунты заменили). Сайдбар: **7** инфра-подтабов
  в аккордеоне `InfraGroup`. Индикатор «Remnawave • онлайн» — в **топбаре** (`App.tsx`), рядом с `AccountMenu`.
- **Remnawave API (`api-1.json` v2.8.0), релевантные эндпоинты для статистики:**
  - `GET /api/system/nodes/metrics` → `response.nodes[]{nodeUuid,nodeName,countryEmoji,providerName,
    **usersOnline:number**,inboundsStats[],outboundsStats[]}` — ЧИСТЫЙ live-сигнал «сколько юзеров онлайн на ноде».
  - `GET /api/bandwidth-stats/nodes/{uuid}/users` → `response{categories[],sparklineData[],**topUsers[]{username,total}**}`
    — кумулятивная нагрузка top-N юзеров по ноде (approx «кто пользовался нодой», НЕ «кто на ней сейчас»).
  - `GET /api/nodes/{uuid}` → `isConnected/isDisabled/usersOnline?`; `list_nodes()` уже есть.
  - **Нет** эндпоинта «юзер X на ноде Y прямо сейчас» (identity-level live). Клиент имеет только
    `get_users_in_squad` (per-squad uuids) + `list_nodes`.

## Закреплённые развилки (Alignment пройден ранее) + реконсиляция под данные

- **Стат-данные** — сборщик снимков в per-account SQLite + live из Remnawave. **Реконсиляция под реальность API:**
  первичный чистый сигнал — снимки `usersOnline` per-node (`/api/system/nodes/metrics`) → надёжно даёт
  «загрузку нод во времени / среднее / самые загруженные». Per-user «сессии/миграции» строим **best-effort**
  из периодических снимков topUsers-членства (`/api/bandwidth-stats/nodes/users`) с явной пометкой «оценка»
  (identity-level live-привязки в API нет). *Делаю best-effort из topUsers вместо точных сессий, потому что
  Remnawave не отдаёт «юзер↔нода сейчас» — точные сессии невозможны без агента на ноде.*
- **xray-checker** — РЕЕСТР инстансов + единая БД с `checker_id`; глобальный селектор (status-page) + per-widget
  (диаграммы d/e/f). Реестр **per-account** (`accounts/<id>/checkers.json`), встроенный общий local-инстанс =
  `checker_id='local'`. Метрик-БД остаётся глобальной; фильтрация по `checker_id` (селектор) И по account-тегу
  (изоляция) одновременно.
- **Готовые ноды** — read-only детект + доустановка ТОЛЬКО недостающих шагов через `skip_components` в пайплайне
  (по образцу существующих `install_vnstat/install_trafficguard/install_warp` гейтов).
- **Хосты** — источник host-def: расширяем деплой-`Template` полем `host_template_ids: list[str]` (ссылки на
  существующие `hosts.json`-шаблоны), при деплое для каждого включённого создаём Remnawave-host с
  `address=FQDN новой ноды`, `nodes=[node_uuid]`; ручное отключение чекбоксами в форме. `create_host` — новый
  метод клиента против `/api/hosts` Remnawave.
- **Настройки мониторинга** — ТЗ требует отдельную вкладку «Мониторинг» в Settings. *Переношу `CheckerControls`
  (конфиг локального чекера) с Dashboard во вкладку Settings→Мониторинг + туда же реестр, вместо сохранения на
  Dashboard, потому что ТЗ явно просит выделенную вкладку (это осознанно ревертит размещение Ф9-эпохи).*
  `SubscriptionSelector` и status-page остаются на Dashboard; глобальный `checker_id`-селектор — в шапке Dashboard.
- **SSL-панель + список доменов** — УЖЕ реализовано (`DomainsPanel`/`CertsForm`/`certs.py`/`domains.py`) →
  из плана **удалено**. Осталась только опциональная 2-колоночная раскладка (косметика) + скачивание сертов (Ф8).
- **Скачивание сертов** — новый роут: SSH-чтение выбранных файлов → zip; приватный ключ по чекбоксу +
  предупреждение; секреты не логировать; `domain` валидировать FQDN-allowlist.

## Стратегия (порядок ведёт зависимость)

- **Мониторинг**: Ф1 (бэкенд-реестр + `checker_id`) → Ф2 (UI: вкладка Settings→Мониторинг + реестр + глобальный селектор).
- **Статистика**: Ф3 (бэкенд: сборщик снимков `usersOnline` + best-effort topUsers + stats-роуты) → Ф4 (frontend виджеты).
- **Готовые ноды**: Ф5 (детект + `skip_components`) — независим (переиспользует существующие группы StepProgress, отдельное «дерево» не нужно).
- **Хосты**: Ф6 — независим.
- **UI-полировка**: Ф7 (плоский инфра-сайдбар + позиция индикатора онлайн) — независим.
- **Скачивание сертов**: Ф8 — независим (вешается на готовый `DomainsPanel`).

Общие контракты: `checker_id: str` (вводит Ф1, потребляют Ф2/Ф4); `/api/checker/*?checker_id=` (Ф1);
`/api/stats/users/{node-load,top-users,migrations}` (формы задаёт Ф3, потребляет Ф4); `skip_components`/детект-роут
(Ф5); `Template.host_template_ids` + `create_host` (Ф6).

## Фаза 1 — Мониторинг (бэкенд): реестр checker-инстансов + `checker_id`
<!-- circle: status=done order=10 deps=[] autonomy=auto obstacle="" -->

**Подход:** добавить `checker_id` в общую метрик-БД + per-account реестр инстансов; local общий чекер =
`checker_id='local'`. Фильтрация на чтении по `checker_id` (селектор) И account-тегу (изоляция) — сосуществует
с существующим tag-based механизмом, не заменяет его. (Отвергнуто: изолированные БД на инстанс — теряется единая
история; отдельный контейнер на аккаунт — не то, что просит ТЗ «единая БД + селекторы».)

**Файловый манифест:**
- `services/metrics_store.py` — миграция: `PRAGMA table_info(proxy_samples)` → если нет `checker_id`, `ALTER TABLE
  ADD COLUMN checker_id TEXT NOT NULL DEFAULT 'local'` + backfill старых строк `'local'`; индекс
  `(checker_id, ts)`. Во ВСЕ read-fns (`get_bars/get_uptime_30d/get_incidents/get_history/get_node_uptime`) +
  `record_samples` добавить опциональный `checker_id: Optional[str]=None` (None=агрегат по всем; запись требует
  явного). Ring-buffer `_RING`/`_META` ключевать по `(checker_id, stable_id)` (иначе remote/local смешаются).
- `services/checker_registry.py` (новый) — per-account стор `accounts/<id>/checkers.json` по образцу
  `storage.py` (explicit `account_id: Optional`). Запись `{id, name, kind:'local'|'remote', base_url, enabled,
  created_at}`. CRUD + `test_connection(base_url)` (HTTP `GET {base_url}/api/v1/status`). Local-инстанс —
  виртуальный встроенный (`id='local'`, не хранится), всегда в списке.
- `services/xray_checker.py` — HTTP-мост (`_get_json/fetch_proxies/fetch_status`) принимает `base_url` целевого
  инстанса; управление контейнером (`start/stop/update/container_state`) гейтить на local.
- `api/xray_checker.py` — `poller_loop`: на каждый аккаунт итерировать его реестр (`enabled` инстансы), для
  local — как сейчас (`_sample_once`, `checker_id='local'`); для remote — `fetch_proxies(base_url)` + записать
  с их `checker_id` и **тегнуть** имена `<account_id>:remote|<orig>` (чтобы `_filter_by_account` работал);
  недоступный инстанс → structured-log warn, цикл живёт. Роуты `/api/checker/*` принимают `?checker_id=`
  (прокинуть в read-fns И в `_filter_by_account`). Новые роуты: `GET/POST /api/checker/instances`,
  `DELETE /api/checker/instances/{id}`, `POST /api/checker/instances/{id}/test`, `POST /api/checker/instances/deploy`
  (деплой `kutovoys/xray-checker` на удалённый сервер по SSH — креды транзитные, не хранить).

**Шаги:** миграция БД (идемпотентна) → ring по (checker_id,stable_id) → реестр-стор → обобщить мост на base_url →
poller по инстансам с тегами → API реестра + `checker_id`-фильтры → structured-log на недоступность.

**Edge-cases:** БД без колонки (миграция идемпотентна) — `empty`; дубликат `base_url` (409) — `malformed`;
удаление инстанса с историей (строки оставить, скрыть из выборок) — `deleted-resource`; недоступный `base_url`
(offline, poller не падает) — `external-failure`; пустой реестр (агрегаты пусто, не 500) — `boundary`; поллер
без request-context не читает ContextVar (итерирует аккаунты явно) — `permission`; чужой `checker_id` → пусто —
`malformed`.

**Verify-гейт:** fake-checker (локальный http-server отдаёт `/api/v1/proxies`+`/api/v1/status`) → POST
`/api/checker/instances` → один тик поллинга → `sqlite3` проверяет строки с непустым `checker_id` → `curl
/api/checker/statuspage?checker_id=<id>` даёт данные инстанса, чужой id → пусто. `python -m py_compile` +
`pytest backend/tests/test_xray_checker.py`.

**Контракт:** `checker_id`; `/api/checker/instances*`; `/api/checker/*?checker_id=`.

## Фаза 2 — Мониторинг (frontend): вкладка Settings→Мониторинг + реестр + селектор
<!-- circle: status=done order=20 deps=[1] autonomy=auto obstacle="" -->

**Подход:** выделенная вкладка «Мониторинг» в Settings как дом чекер-настроек + реестра; глобальный `checker_id`
селектор в шапке status-page. (Отвергнуто: оставить `CheckerControls` на Dashboard — ТЗ требует вкладку.)

**Файловый манифест:**
- `frontend/src/components/Settings.tsx` — новая вкладка «Мониторинг»; **перенести `CheckerControls` с Dashboard**
  сюда (конфиг локального чекера, `POST /api/settings/xray-checker` не трогать).
- `frontend/src/components/monitoring/CheckerRegistry.tsx` (новый) — список инстансов (local + remote), формы
  «Подключить (URL)» и «Добавить (деплой по SSH)», enable/disable, удалить, «Проверить соединение»; реестр-API Ф1.
- `frontend/src/components/Dashboard.tsx` — убрать `CheckerControls` (переехал в Settings); добавить глобальный
  `checker_id`-селектор в шапку; прокинуть выбранный id в `/api/checker/{statuspage,incidents,status}` запросы.

**Edge-cases:** пустой реестр («нет серверов») — `empty`; деплой по SSH с неверными кредами (тост, форма жива) —
`external-failure`; недоступный URL при «Проверить» (красный) — `external-failure`; переключение селектора
рефетчит — `boundary`; узкий экран (мобилка) — `browser`; несохранённый чекер-конфиг — `malformed`.

**Verify-гейт:** headless playwright — Settings→«Мониторинг» показывает чекер-настройки+реестр, Deploy их НЕ
показывает; добавить инстанс (мок API) → в списке; Dashboard-селектор меняет `checker_id` в request URL. `tsc --noEmit`.

## Фаза 3 — Статистика (бэкенд): сборщик снимков + stats-роуты
<!-- circle: status=done order=30 deps=[] autonomy=auto obstacle="" -->

**Подход:** per-account SQLite-сборщик снимков `usersOnline` per-node (чистый сигнал) + best-effort topUsers-членство;
live-состояние из Remnawave сразу. (Отвергнуто: точные per-user сессии/миграции — API не даёт identity-level live;
«только xray-checker» — не знает юзеров.)

**Файловый манифест:**
- `services/remnawave_client.py` — `get_nodes_metrics()` (`GET /api/system/nodes/metrics` → nodes[] с usersOnline),
  `get_node_users_usage(node_uuid)` (`GET /api/bandwidth-stats/nodes/{uuid}/users` → topUsers). Разворот `{response}`.
- `services/user_stats_store.py` (новый) — per-account SQLite `accounts/<id>/user_stats.db` (паттерн `storage.py`,
  explicit `account_id`). Таблицы: `node_load_samples(ts, node_uuid, node_name, users_online)` (idx ts, node_uuid);
  `node_top_users(ts, node_uuid, username, total_bytes)` (для best-effort членства). retention 35 дн. Запросы:
  `node_load(window)` (ряд usersOnline по нодам во времени + текущее), `top_users(window)` (топ по нагрузке),
  `migrations(window)` (best-effort: смена доминирующей ноды у username между снимками → from→to + частота).
- `api/user_stats.py` (новый) — `/api/stats/users/node-load`, `/api/stats/users/top-users`,
  `/api/stats/users/migrations` (+ окно). Под `require_account`.
- `main.py` — в `lifespan` второй фоновый сборщик `collector_loop` по образцу `poller_loop` (итерирует аккаунты
  явно, per-account Remnawave-клиент из `storage.load_settings(aid)`, недоступность → log+skip тика).

**Edge-cases:** холодный старт (нет снимков → пустые массивы, не 500) — `empty`; Remnawave недоступен (skip, не
падать) — `external-failure`; удалённые ноды/юзеры в окне (игнор битых ссылок) — `deleted-resource`; фоновая
задача без ContextVar (явный `account_id`) — `permission`; дубль снимка в тик (уникальность `(ts,node_uuid)`) —
`boundary`; malformed ответ Remnawave (structured-log, skip) — `malformed-input`.

**Verify-гейт:** засидить `user_stats.db` фикстурами (несколько снимков, смена доминирующей ноды) → `curl
/api/stats/users/node-load` даёт ряд usersOnline; `/migrations` — корректный from→to. Мок Remnawave
(`/api/system/nodes/metrics`) → один тик сборщика пишет строку. `python -m py_compile` + pytest.

**Контракт:** `/api/stats/users/{node-load,top-users,migrations}` + формы ответов.

## Фаза 4 — Статистика (frontend): группа «Статистика» + виджеты
<!-- circle: status=done order=40 deps=[1,3] autonomy=auto obstacle="" -->

**Подход:** раздел «Пользователи» с виджетами на inline-SVG (как Dashboard/InfraDashboard, CSP self-contained),
шестерёнка настроек per-widget. Виджеты **реконсилированы под доступные данные**.

**Файловый манифест:**
- `frontend/src/components/Sidebar.tsx` — группа «Статистика» → раздел «Пользователи» (Tab `'stats-users'` +
  роутинг).
- `frontend/src/components/stats/UsersStats.tsx` (новый) — контейнер + виджеты:
  a. загрузка нод во времени (usersOnline, `/node-load`), b. среднее юзеров на ноду + самые загруженные
  (`/node-load` агрегат), c. топ юзеров по нагрузке (`/top-users`), d. best-effort миграции from→to
  (`/migrations`, с бейджем «оценка»), e. самые стабильные ноды (xray uptime%, `/api/checker/*?checker_id=`),
  f. самые быстрые (xray latency, `checker_id`).
- `frontend/src/components/stats/WidgetSettings.tsx` (новый) — поповер-шестерёнка: e/f — выбор `checker_id`
  (реестр Ф1), a–d — окно времени.

**Edge-cases:** пустые данные (плейсхолдер «данных пока нет») — `empty`; недоступный роут (тост/скелетон) —
`external-failure`; смена `checker_id`/окна рефетчит только свой виджет — `boundary`; огромные списки (top-N,
скролл) — `boundary`; узкий экран (колонка) — `browser`; approx-миграции помечены «оценка» — `malformed`.

**Verify-гейт:** headless playwright — `stats-users` (мок API) → присутствуют виджеты (DOM-маркеры), шестерёнка
открывается, смена `checker_id` в e/f меняет request URL. `tsc --noEmit`.

## Фаза 5 — Готовые ноды: детект шагов + доустановка недостающего
<!-- circle: status=pending order=50 deps=[] autonomy=auto obstacle="" -->

**Подход:** read-only детект (переиспользуя таксономию `Component` из `node_ops.py`) + `skip_components` в пайплайне
(по образцу `install_*` гейтов). Существующие группы `StepProgress` показывают статус — отдельное «дерево» НЕ нужно
(ТЗ-пункт «нумерация в дерево» уже покрыт группами; добавлю лишь явную иерархическую нумерацию лейблов, косметика).
(Отвергнуто: перекройка пайплайна; маршрут через N×`/api/node/step` — теряется единый Task/StepProgress и
SSH-dual-port фаза.)

**Файловый манифест:**
- `api/node_ops.py` — `_DETECT_SCRIPTS: dict[Component, builder]` (read-only пробы: node_accelerator
  `test -d /opt/node-accelerator`, trafficguard `test -d /opt/TrafficGuard-auto`, remnanode `docker ps ...`,
  masking маркер в `/var/www/html`, warp `wg show warp`, hysteria2 `test -s /opt/certbot/certs/live/*/fullchain.pem`,
  ssl `test -s ~/.acme.sh/{domain}_ecc/*.cer`, haproxy `systemctl is-active haproxy`) → `{component:
  present|absent|unknown}`. Роут `POST /api/node/detect` (`NodeOpRequest`-подобный, creds-per-request, одна SSH-сессия).
- `models/deploy.py` — `DeployRequest.skip_components: list[str]=[]`.
- `services/pipeline.py` — в `run_pipeline` перед каждым manageable-шагом: `if comp in req.skip_components:
  _begin_step + skip-log` (как `install_vnstat=false`). Аудит идемпотентности переиспользуемых `step_*`
  (в частности `_trafficguard_fallback` дублирует iptables — добавить pre-check перед `-A`).
- `frontend/src/components/StepProgress.tsx` — иерархическая нумерация лейблов групп (1,2,3.1,3.2,…) — косметика.
- `frontend/src/components/DeployDashboard.tsx`/`DeployForm.tsx` — кнопка «Добавить существующий сервер» →
  модалка: IP+SSH → `POST /api/node/detect` → чеклист компонентов (present предвыбраны как skip) → submit деплоя
  со `skip_components`.

**Edge-cases:** полу-выполненный шаг (unknown→решает юзер, absent→перезапуск) — `boundary`; сервер недоступен
(ошибка) — `external-failure`; нестандартный SSH-порт (пробовать оба) — `boundary`; порядок зависимостей (нельзя
пропустить сеть до …) сохранять — `race`; повторный запуск не ломает настроенное (идемпотентность) — `deleted-resource`;
пустой skip (полный деплой) — `empty`; malformed компонент в skip (игнор) — `malformed`.

**Verify-гейт:** unit-тесты парсеров `_DETECT_SCRIPTS` на фикстурных выводах команд (свежий бокс vs задеплоенный)
→ корректный map; тест `skip_components` пропускает шаг (skip-log, шаг instantly-done). `python -m py_compile` +
`tsc --noEmit` + pytest.

**Контракт:** `skip_components`, `/api/node/detect`.

## Фаза 6 — Хосты: авто-создание при деплое из шаблона
<!-- circle: status=pending order=60 deps=[] autonomy=auto obstacle="" -->

**Подход:** источник — существующие локальные host-шаблоны (`hosts.json`), связь через новое поле
`Template.host_template_ids`; при деплое `address=FQDN ноды`, `nodes=[node_uuid]`. (Отвергнуто: копия исходного
address; источник «из профиля Remnawave» — выбран локальный шаблон.) Host-def обязан нести inbound
(`inbound`), иначе `POST /api/hosts` Remnawave невалиден — валидировать при связывании.

**Файловый манифест:**
- `services/remnawave_client.py` — `create_host(**fields)` → `POST /api/hosts` (сверить обяз. поля по
  `CreateHostRequestDto` в `api-1.json`), разворот `{response}`; при необходимости `list_hosts`.
- `models/settings.py` — `Template.host_template_ids: list[str]=[]` (ссылки на `hosts.json`); обновить
  `TemplateCreate/Update`.
- `models/deploy.py` — `DeployRequest.disabled_host_template_ids: list[str]=[]`.
- `services/pipeline.py` — после `create_node` (uuid) для каждого включённого host-шаблона выбранного
  `template_id`: загрузить из `hosts.json`, `create_host(address=req.domain, nodes=[node_uuid], inbound=...,
  remark=<remark>+суффикс ноды, ...)`; ошибку per-host логировать и продолжать (аддитивно, деплой не валить).
  **Shell-safety** для `address/sni/host/path` не требуется (уходят в JSON API, не в bash) — но проверить.
- `frontend/src/components/DeployForm.tsx` — в секции Remnawave чекбокс-список host-шаблонов выбранного
  `template_id` (все вкл.); снятые → `disabled_host_template_ids`.
- `frontend/src/components/Settings.tsx` — в редакторе шаблонов мульти-селект привязанных host-шаблонов.

**Edge-cases:** шаблон без хостов (no-op) — `empty`; `create_host` 400 (лог, продолжить) — `external-failure`;
дубликат remark — `boundary`; `create_in_remnawave=false` (не создаём) — `permission`; `create_node` упал (skip
хостов) — `deleted-resource`; снятый чекбокс → НЕ создаём — `boundary`; host-def без inbound (невалиден) — `malformed`.

**Verify-гейт:** мок Remnawave (201 на `POST /api/hosts`) → цикл создания с фикстурным шаблоном → проверить
payload'ы (`address==domain`, `nodes==[uuid]`, inbound проброшен); снятый хост не создаётся. `python -m py_compile`
+ `tsc --noEmit` + pytest.

**Контракт:** `Template.host_template_ids`, `create_host`.

## Фаза 7 — UI-полировка: плоский инфра-сайдбар + позиция индикатора онлайн
<!-- circle: status=done order=70 deps=[] autonomy=auto obstacle="" -->

**Подход:** две малые независимые frontend-правки. (SSL-панель + список доменов из старого Ф8 — УЖЕ готовы,
удалены из плана.)

**Файловый манифест:**
- `frontend/src/components/Sidebar.tsx` — убрать аккордеон `InfraGroup`, вывести 7 инфра-подтабов
  (infra-dashboard/providers/projects/services/payments/settings/tokens) плоской секцией; Tab-union/роутинг сохранить.
- `frontend/src/App.tsx` — индикатор «Remnawave • онлайн» (сейчас в топбаре рядом с `AccountMenu`) переместить в
  верхний ЦЕНТР шапки, не задублировать (сначала grep «онлайн» — подтвердить точную JSX-позицию).

**Edge-cases:** активный инфра-таб подсвечен после расфлатчивания — `boundary`; единственный инстанс индикатора
(не дубль) — `boundary`; узкий экран/мобилка (индикатор в центре не ломает топбар) — `browser`; session-gated
инфра-табы остаются под `require_account` — `permission`; пустое состояние индикатора (Remnawave не настроен) — `empty`.

**Verify-гейт:** headless-скриншоты до/после (визуальные правки — по правилу достаточно скриншота). `tsc --noEmit`.

## Фаза 8 — Скачивание сертификатов из панели
<!-- circle: status=done order=80 deps=[7] autonomy=auto obstacle="" -->

**Подход:** роут читает серты по SSH → zip; приватный ключ по чекбоксу + предупреждение про HTTPS. Секреты не
логировать; `domain` — FQDN-allowlist перед подстановкой в путь. Вешается на готовый `DomainsPanel` (Ф8-стар уже готов).

**Файловый манифест:**
- `api/certs.py` — `POST /api/certs/download` body `{ip, ssh_user, ssh_password, ssh_port, domain, files:[...]}` →
  `SSHSession` читает выбранные (`/etc/ssl/certs/{domain}_fullchain.pem`, `/etc/ssl/private/{domain}.key`) →
  `StreamingResponse` zip (`attachment; filename="{domain}-certs.zip"`); один файл → `application/x-pem-file`;
  НЕ логировать ключ; валидировать `domain` FQDN-allowlist.
- `frontend/src/components/DomainsPanel.tsx` (или `CertsForm.tsx`) — у каждого домена кнопка «Скачать» + чекбоксы
  (fullchain/ключ/оба) → fetch → download blob (`a[download]`); SSH-креды из формы, не сохранять; предупреждение про ключ.

**Edge-cases:** файла нет на ноде (404 «не найден») — `deleted-resource`; нет прав на `/etc/ssl/private`
(понятная ошибка) — `permission`; домен без сертов — `empty`; SSH недоступен (таймаут→тост) — `external-failure`;
пустой выбор файлов (валидация) — `boundary`; path-инъекция в `domain` (FQDN-allowlist) — `malformed-input`.

**Verify-гейт:** мок `SSHSession` (по образцу backend-тестов со stubbed asyncssh) возвращает содержимое
фикстурных файлов → `POST /api/certs/download` собирает валидный zip с выбранными файлами; отсутствующий файл →
404; пустой `files` → 422. Headless-клик «Скачать» → инициируется download (сетевой запрос + blob).
`python -m py_compile` + `tsc --noEmit` + pytest.

## Риски

- **Сложность реестра checker (Ф1):** `checker_id` + account-тег фильтруются одновременно; ring-buffer теперь
  по `(checker_id,stable_id)`. Смягчение: local-путь не меняется семантически (`checker_id='local'`), remote —
  чисто аддитивный HTTP-поллинг; тесты на изоляцию по обоим измерениям.
- **Данные статистики (Ф3/Ф4):** identity-level «юзер↔нода сейчас» в API НЕТ — `usersOnline` даёт только счётчик
  per-node; сессии/миграции — best-effort из topUsers. Смягчение: чёткие виджеты на надёжном сигнале (загрузка
  нод), approx-виджеты помечены «оценка».
- **Идемпотентность шагов (Ф5):** `_trafficguard_fallback` дублирует iptables при повторе. Смягчение: pre-check
  перед `iptables -A`; unknown→юзер решает.
- **Приватный ключ по HTTP (Ф8):** осознанная операция над своей инфрой; предупреждение + не логировать.
- **Конфликт с Ф9-эпохой (Ф2):** перенос `CheckerControls` в Settings ревертит недавнее размещение на Dashboard —
  осознанно, по ТЗ; обновить CLAUDE.md §4b.

## Журнал

### Ф8 — done (2026-07-07)
`api/certs.py`: `POST /api/certs/download` (`DownloadCertRequest`, FQDN-allowlist `_DOMAIN_RE`, `_read_remote_file`
через тихий `ssh.get_output` — ключ не логируется; single→pem / multi→zip). `DomainsPanel.tsx`: per-row `DownloadCtl`
(чекбоксы fullchain/ключ, amber HTTPS-warning, saved creds для deployed / disabled для ручных). Self-review (security):
инъекция/секрет/auth/error — PASS. Применены LOW-фиксы: `fullmatch` (хвостовой `\n`) + `head -c 8 MiB` кап (self-OOM).
Verify: `test_certs.py` (18, incl. path/shell-инъекция `../`/`;`/`$()`, missing→404, empty→422) + backend **163 passed**.

### Ф4 — done (2026-07-07)
`stats/UsersStats.tsx` (6 inline-SVG виджетов: загрузка нод/среднее-busiest/топ-по-нагрузке/миграции-«оценка»/
стабильность/скорость) + `stats/WidgetSettings.tsx` (шестерёнка: период a–d, checker_id e–f, per-widget рефетч).
`Sidebar.tsx`: группа «Статистика» → Tab `stats-users`. `App.tsx`: CRUMB + рендер. Пустые/загрузка/ошибка-состояния,
стек 1-кол ≤820px. Verify: `tsc --noEmit` clean; визуал — общий скриншот-смоук после Ф5. Чисто аддитивный UI.

### Ф3 — done (2026-07-07)
`user_stats_store.py` (per-account SQLite, explicit account_id, node_load/top_users/migrations best-effort);
`api/user_stats.py` (роуты `/api/stats/users/{node-load,top-users,migrations}` под require_account + `collector_loop`
в lifespan); `remnawave_client.get_nodes_metrics/get_node_users_usage`; `main.py` — второй фоновый таск.
Self-review (code+security): **чисто, high/medium нет**. Применены: C1 — per-node fetch через `asyncio.gather`
(медленная нода не тормозит тик); b — кап `topUsers` (`_TOP_USERS_CAP=20`). Отклонены/pre-existing: `verify=False`
TLS и blind-SSRF через `panel_url` (не введено Ф3, тот же trust-model), C2 кумулятивные миграции (помечено «оценка»).
Verify: `test_user_stats.py` (8) + весь набор **154 passed**. Контракт для Ф4: формы ответов `/api/stats/users/*`.

### Ф2 — done (2026-07-07)
`monitoring/CheckerControls.tsx` (вынесен из Dashboard) + `monitoring/CheckerRegistry.tsx` (новый: список
local+remote, «Подключить по URL» с inline-ошибками 422/409/400, «Развернуть по SSH» `/instances/deploy`
creds-transient, enable/disable/test/delete per-instance, local залочен). `Settings.tsx`: новая вкладка
«Мониторинг» (`MonitoringTab` = CheckerControls + CheckerRegistry). `Dashboard.tsx`: убран CheckerControls,
добавлен глобальный `checker_id`-`<select>` (виден при >1 инстансе), `checker_id` проброшен в
`statuspage`/`incidents` (смена рефетчит). Verify: `tsc --noEmit` clean (Ф2+Ф7 вместе); скриншот-смоук — общий после Ф4.

### Ф7 — done (2026-07-07)
`Sidebar.tsx`: убран `InfraGroup`-аккордеон (стейт `infraOpen`/`ChevronDown`/`nested`-indent) → 7 инфра-подтабов
плоской секцией «Инфра-биллинг»; Tab-union/роутинг не тронуты. `App.tsx`: индикатор «Remnawave • онлайн»
(`.ni-clock`, единственный инстанс) переехал в абсолютный центр топбара (`AccountMenu` справа); `.ni-clock`
скрыт ≤820px (мобилка не ломается). Verify: `tsc --noEmit` clean + визуальная проверка в общем скриншот-смоуке
после Ф2/Ф4. Чисто визуально-структурная правка (без логики/навигации).

### Ф1 — done (2026-07-07)
Реализовано: `metrics_store` колонка `checker_id` + идемпотентная миграция + фильтры во всех read-fns + ring по
`(checker_id, stable_id)`; `checker_registry.py` (per-account стор, виртуальный `local` + remote CRUD,
`test_connection`, `remote_deploy_script`); `storage.load/save_checkers`; `xray_checker` HTTP-мост на `base_url`;
`api/xray_checker` — `_resolve_instance`, `?checker_id=` в `/status`/`/statuspage`/`/incidents`, CRUD-роуты
реестра + `/instances/deploy` (SSH), poller по local+remote инстансам. Self-review (code+security): **SSRF (HIGH)**
устранён новым `net_guard.py` (порт `_host_is_public` из subs-aggregator, гард на регистрации И на фетче,
`follow_redirects=False`); **shell-инъекция (LOW)** — `shlex.quote` в deploy-скрипте; C3 None-агрегат
задокументирован, C6 (последовательный remote-сэмплинг) deferred. Verify: `backend/tests/test_checker_registry.py`
(21 тестов, incl. SSRF-регресс) + весь набор **146 passed**. Контракт для Ф2/Ф4: `checker_id`,
`/api/checker/instances*`, `/api/checker/*?checker_id=`.
