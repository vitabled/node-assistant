# Remnawave-модуль (Волна 1): speed-тесты · установка панели/подписки · переменные · каталог подписок · бэкап

## Контекст

Крупная программа расширения node-installer (FastAPI + React/TS/Vite/Tailwind, per-account isolated). Полное
ТЗ — ~6 тяжёлых доменов: (1) тесты характеристик/скорости нод; (2) деплой Remnawave-панели и страницы
подписок (в т.ч. на разные серверы) с виджет-дэшбордом и синхронизацией по приоритету; (3) миграция
marzban→remnawave; (4) Переменные / Каталог подписок / Резервное копирование / Профили; (5) MCP-сервер +
встроенный ИИ-агент; (6) единый движок правил (Уведомления + Автоматизация).

**Решение по объёму (утверждено): волнами, с фундамента.** ЭТОТ план = **Волна 1** — фундамент, который не
несёт нерешённого системного риска. Тяжёлое/рисковое отложено в отдельные планы Волны 2+ (см. «Отложено»).

Закреплённые развилки (Alignment пройден, в фоне не переспрашивать):
- **Синхронизация панелей** (Волна 2) — механизм = бэкап→restore по приоритету (переиспользуя distillium),
  НЕ live-репликация PostgreSQL. В Волне 1 закладывается только отображение backup-статуса в виджете.
- **MCP/ИИ** (Волна 2) — И MCP-сервер наружу (расширенный из TrackLine/mcp-remnawave), И встроенный чат-агент.
- **Уведомления + Автоматизация** (Волна 2) — единый rules-engine (triggers: xray-checker/метрики/события;
  actions: telegram, remnawave-операции, скрытие хостов); «Уведомления» = его подмножество с action=telegram.
- **Speed-тесты** — расширенные: (а) характеристики нод (CPU/RAM/диск) + Ookla speedtest-cli (внешний канал);
  (б) **матрица iperf3 «любой↔любой»** между парами нода↔нода / нода↔тест-сервер / тест-сервер↔тест-сервер
  (бэкенд оркестрирует: SSH к обеим сторонам — одна `iperf3 -s`, другая `iperf3 -c`); (в) **тест скорости через
  xray-впн-ссылку** — источник (любая нода ИЛИ тест-сервер, выбирается в UND) поднимает xray-client с ссылкой
  как outbound и гонит speedtest/iperf3 через туннель (выбирается в UI). **Метрики пары выбираются пользователем** (в настройках
  и при запуске): 1=iperf3 throughput, 2=+ping/jitter, 3=+traceroute. Тест-серверы регистрируются/деплоятся во
  вкладке Settings «Сервера для тестирования». Раздел запуска матрицы — «Тесты скорости» в группе «Статистика».
  Инструменты тестов (iperf3 + speedtest-cli + xray-client) ставятся при деплое ЛЮБОГО ресурса (нода/панель/
  подписка) через тумблер `install_test_tools` (дефолт вкл, как `install_vnstat`).
- **Учётки серверов панели/подписки** — как у деплой-нод: «jobs» в браузерном localStorage `panel_jobs_<id>`
  (включая SSH-креды, только клиент); бэкенд получает креды в каждом запросе, на сервере НЕ хранит
  (соблюдает no-secrets-at-rest).

## Стратегия (порядок ведёт зависимость)

- **Speed-тесты**: Ф1 (тест-серверы + общий инсталлер тест-инструментов + Settings-вкладка) → Ф2 (тесты нод:
  характеристики/speedtest/xray-link в карточке + тумблер `install_test_tools` в деплое ноды) → Ф2b («Тесты
  скорости»: матрица iperf3 «любой↔любой» + xray-link, раздел в группе «Статистика»).
- **Remnawave-раздел**: Ф3 (сайдбар-группа «Remnawave» + скелет 6 разделов; «Миграция»/«Профили» — заглушки
  Волны 2) → Ф4 (бэкенд-пайплайн установки панели/подписки) → Ф5 (каталог подписок/Orion) → Ф6 (дэшборд
  установки: виджет-рамка 2 подрамки + деплой-формы + статус) → Ф7 (модалка управления: Компоненты +
  Статистика) → Ф8 (Переменные: .env-редактор) → Ф9 (Резервное копирование).

Общие контракты: тест-сервер-реестр + `POST /api/testservers/*` + общий скрипт `test_tools_install_script`
(Ф1, потребляют Ф2/Ф2b/Ф4); `POST /api/speedtest/*` (матрица + xray-link, Ф2b); `panel_jobs_<id>`
localStorage-схема + `PanelDeployRequest` (Ф4/Ф6, потребляют Ф7/Ф8/Ф9); каталог подписок
`GET /api/subpages` (Ф5, потребляет Ф6); сайдбар-группа Remnawave + Tab-union (Ф3, потребляют все
Remnawave-фазы). Группа «Статистика» — из плана `2026-07-07-dashboard-monitoring-stats-hosts.md`; Ф2b
добавляет в неё раздел «Тесты скорости», создавая группу если её ещё нет (защитно).

**Отложено на Волну 2+ (отдельные планы, здесь НЕ реализуется):** синхронизация панелей по приоритету
(бэкап→restore + группировка виджетов); Миграция marzban→remnawave; MCP-сервер + встроенный ИИ-агент;
движок правил (Уведомления над настройками + раздел Автоматизация); Профили (порт bropines/xray-config-ui-editor).
Заготовки под них (заглушки разделов, поля статуса) закладываются в Волне 1, чтобы Волна 2 не переверстывала.

## Карта кодовой базы (что уже есть — читается каждой фазой, не переделывать)

**Деплой-инфра (эталон для установки панели):**
- `services/pipeline.py` — `run_pipeline`, `step_*` (`step_node_accelerator`/`step_traffic_guard`/
  `step_system_optimize`/`step_ssh_dualport_verify`/`step_ssl`/`step_remnanode`/`step_sni_masking`/`step_warp`/
  `step_certbot_ssl`), `_begin_step(task, idx, label)`, `build_ssl_script`/`ssl_needs_cf_dns`,
  `_effective_open_ports`. Пишет docker-compose/nginx из шаблонов, `docker compose up -d`.
- `services/ssh_manager.py` — `SSHSession(ip, port)`, `run_script` (стримит `bash -s 2>&1` построчно в Task),
  `get_output` (тихий вывод, для секретов — без Task-лога).
- `services/task_store.py` — `Task`, `STEP_LABELS` (13), стрим через `/ws/logs/{task_id}`.
- `services/storage.py` — per-account JSON под `DATA_DIR/accounts/<id>/` (параметр `account_id: Optional` для
  фоновых вызовов). `services/checker_registry.py` — ЭТАЛОН per-account реестра (`accounts/<id>/checkers.json`,
  CRUD + `test_connection` + `remote_deploy_script`); копировать его паттерн для тест-серверов/каталога.
- `services/net_guard.py` — `is_safe_url`/`host_is_public` (SSRF-гард) для любых URL-фетчей.
- `services/xray_checker.py` — ЭТАЛОН оркестрации контейнера через `docker` CLI (DooD): `docker run -d …`.
- `models/deploy.py` — `DeployRequest` (валидаторы `ip`/`domain`/`email`/`open_ports`, `validate_by_mode`).
- `api/deploy.py` (`POST /api/deploy`, стрим-Task), `api/node_ops.py` (`Component`/`Action`,
  `_reinstall`→`pipeline.step_*`, `_UNINSTALL_SCRIPTS`, `POST /api/node/step`, `POST /api/node/detect`),
  `api/stats.py` (`POST /api/stats/node` — `securityStats` fail2ban + `trafficStats` vnstat + `certInfo`),
  `api/certs.py` (`build_ssl_script`), `api/settings.py`, `api/hosts.py`, `api/domains.py`.
- `main.py` — `lifespan` (фоновые задачи: `poller_loop`, `collector_loop` — итерируют `accounts.list_accounts()`
  с явным `account_id`, ContextVar в lifespan НЕ наследуется), роутеры под `Depends(require_account)`.

**Frontend:**
- `components/DeployDashboard.tsx` — jobs в localStorage `deploy_jobs_<id>` (`auth/store.ts::deployJobsKey`),
  `addJob`/`retryJob`/`removeJob` через функциональный `setState`; ЭТАЛОН для `PanelDashboard`.
- `components/DeployForm.tsx` — секции/режимы, `validateForm` (экспорт для тестов), префилл из настроек,
  `Collapsible`/`SectionLabel`, `FIELD_SECTION` авто-раскрытие ошибочной секции.
- `components/DeployCard.tsx` — `useTaskStream` (SSE), per-node stats (5-мин поллинг `/api/stats/node`),
  `ManageBlock`/`OpStreamModal` (второй `useTaskStream`), `CertBlock`; ЭТАЛОН для виджета панели + модалки.
- `components/StepProgress.tsx` — `DEPLOY_STEPS`/`RENEW_STEPS`, сворачиваемые `STEP_GROUPS`.
- `components/Sidebar.tsx` — Tab-union, группы, аккордеон `InfraGroup`; индикатор «Remnawave • онлайн» — в
  топбаре (`App.tsx`).
- `components/Settings.tsx` — вкладки (Тема/Мониторинг/Deploy/…); `components/monitoring/CheckerRegistry.tsx`
  — ЭТАЛОН UI реестра (список + «Подключить по URL» + «Развернуть по SSH» + test/delete).
- `hooks/useTaskStream.ts`, `auth/store.ts` (per-account ключи), `App.tsx` (`<Toaster/>`, топбар).

**Внешние источники (сверено разведкой; факты для фаз):**
- **Remnawave install:** панель = docker-compose `/opt/remnawave` из 3 контейнеров — `remnawave/backend:2`
  (`127.0.0.1:3000` app + `:3001` metrics), `postgres:18.4` (`remnawave-db`, том `remnawave-db-data`, TZ=UTC!),
  `valkey/valkey:9-alpine` (unix-socket). Секреты: `openssl rand -hex 64` (JWT), `-hex 24` (PG-пароль).
  Ключевые `.env`: `POSTGRES_{USER,PASSWORD,DB}`, `DATABASE_URL`, `REDIS_*`, `JWT_AUTH_SECRET`,
  `JWT_API_TOKENS_SECRET`, `JWT_AUTH_LIFETIME`, `APP_PORT`, `METRICS_PORT/USER/PASS`, `FRONT_END_DOMAIN`,
  `SUB_PUBLIC_DOMAIN`, `PANEL_DOMAIN`, `IS_TELEGRAM_NOTIFICATIONS_ENABLED`+`TELEGRAM_BOT_TOKEN`+
  `TELEGRAM_NOTIFY_*`, `WEBHOOK_ENABLED`/`WEBHOOK_URL`/`WEBHOOK_SECRET_HEADER` (HMAC-SHA256, заголовки
  `X-Remnawave-Signature`/`-Timestamp`). Всё слушает `127.0.0.1` → **reverse-proxy обязателен**, только в
  корне домена (sub-path не поддержан). Прокси: **caddy/nginx/traefik/angie** (SSL авто или acme.sh).
  **Страница подписок** = отдельный контейнер `remnawave/subscription-page:latest` (`127.0.0.1:3010`), свой
  `.env`: `APP_PORT`, `REMNAWAVE_PANEL_URL` (bundled `http://remnawave:3000` / separate `https://panel…`),
  `REMNAWAVE_API_TOKEN` (создаётся в Dashboard→API Tokens), `CUSTOM_SUB_PREFIX`, `TRUST_PROXY`. Панель↔подписка
  связываются `SUB_PUBLIC_DOMAIN`. **Официально поддержан «separate server»** для подписки → это и есть «деплой
  разных элементов на разные серваки».
- **Orion** (страница подписок): один статический `index.html`, БЕЗ сборки; ставится volume-mount'ом в
  `remnawave/subscription-page` (`./index.html:/opt/app/frontend/index.html`) + рестарт контейнера. Данные
  подписки инжектит сам контейнер (EJS `<%= panelData %>`), кастомизация — JSON `/assets/.app-config-v2.json`.
  → форма каталога хранит/деплоит ровно ОДИН html-файл.
- **distillium backup-restore** (Резервное копирование): интерактивный bash, ставится в `/opt/rw-backup-restore`
  (`rw-backup`); бэкапит PG (`pg_dumpall -c`|`pg_dump --clean`) + весь `/opt/remnawave` (tar) → бандл
  `remnawave_backup_TS.tar.gz`; аплоад Telegram/GDrive/S3; расписание = host **cron**; restore ДЕСТРУКТИВЕН
  (чистит том `remnawave-db-data`) → нужен confirm-гейт. Секреты (BOT_TOKEN/S3) — в `config.env` chmod 600 НА
  ЦЕЛЕВОМ сервере (не в нашей БД). Функции: `create_backup`/`restore_backup`/`setup_auto_send`/
  `send_{telegram,s3,google_drive}_document`.
- **Отложенные (Волна 2), факты зафиксированы:** `TrackLine/mcp-remnawave` — TS + `@modelcontextprotocol/sdk`,
  153 tool'а, пути из `@remnawave/backend-contract` (апдейт API = бамп пакета `^2.6.27`→2.8.x), MIT, stdio.
  `bropines/xray-config-ui-editor` — React19/Vite/Tailwind/Zustand+Immer, ядро `core/xray/schemas/*`+ajv+модалки,
  тяжёлое опционально (React Flow topology, web-workers), MIT.

## Риски

- **Установка панели — секреты и деструктив:** генерация JWT/PG-паролей и `.env` идёт на ЦЕЛЕВОМ сервере (SSH),
  в нашу БД не пишется. Reverse-proxy обязателен (панель на `127.0.0.1`) — забыть = панель недоступна.
  Смягчение: пайплайн всегда ставит выбранный прокси + SSL, `.env` пишется на сервере, креды — per-request.
- **Разные серверы для панели vs подписки:** форма должна допускать 2 разных SSH-таргета; подписка в
  separate-mode требует публичный `REMNAWAVE_PANEL_URL` + API-токен панели. Смягчение: явные под-формы.
- **iperf3 тест-сервер:** открытый `5201` — потенциальная поверхность. Смягчение: UFW allow только с IP наших
  нод/бэкенда; тест-сервер — временный/управляемый.
- **speedtest-cli:** Ookla CLI требует принятия лицензии/репозитория; может не встать. Смягчение: fallback на
  `speedtest-cli` (python) или пропуск с пометкой.
- **xray-link тест:** источник поднимает xray-client с ссылкой как outbound + локальный SOCKS/HTTP inbound, гонит
  speedtest через прокси (`--proxy`/`curl --socks5`). Риски: битая/недоверенная ссылка (валидировать схему
  vless/trojan/ss/vmess перед парсингом; не выполнять произвольный конфиг), утечка default-route (только прокси-
  режим, НЕ tun — не трогаем маршруты хоста), таймаут туннеля. Смягчение: временный конфиг + прокси-only + kill
  после теста; ссылку не логировать (может нести креды).
- **Матрица «любой↔любой»:** тест пары требует SSH к ОБЕИМ сторонам (одна `iperf3 -s` эфемерно, другая `-c`) →
  нужны креды обеих сторон (из `deploy_jobs`/`panel_jobs`/тест-реестра). Смягчение: эфемерный iperf3-сервер на
  время теста + UFW allow только с IP второй стороны, потом откат.
- **localStorage-креды панелей:** SSH-пароль контрол-плейна в браузере (как у нод уже сейчас) — приемлемо по
  текущей архитектуре; сервер не хранит. Авто-бэкап по cron ставится НА ЦЕЛЕВОМ сервере (его секреты там же),
  не требует наших серверных кред.
- **distillium restore деструктивен** — двойной confirm в UI перед restore.

## Фаза 1 — Тест-серверы + общий инсталлер тест-инструментов + Settings-вкладка
<!-- circle: status=done order=10 deps=[] autonomy=auto obstacle="" -->

**Подход:** per-account реестр тест-серверов + деплой по SSH, по образцу `checker_registry.py`; ОБЩИЙ скрипт
установки тест-инструментов (iperf3 + speedtest-cli + xray-client) выделяем в переиспользуемый модуль — его
дёргают тест-сервер-деплой, деплой ноды (Ф2), деплой панели (Ф4). Отвергнуто: дублировать install-логику в
каждом пайплайне — единый источник дешевле в поддержке.

**Файловый манифест:**
- создать `backend/app/services/test_tools.py` — `test_tools_install_script(extras)` → bash: `apt install -y
  iperf3` + Ookla `speedtest` (репозиторий+accept-license, fallback python `speedtest-cli`) + `xray` core
  (скачать бинарь в `/usr/local/bin/xray`) + хелперы `iperf_server_script(port)`/`iperf_client_script(host,
  port,opts)`/`xray_link_speedtest_script(link,mode)` (парс vless/trojan/ss/vmess-ссылки → временный конфиг с
  outbound + SOCKS inbound → speedtest через прокси → kill). Ссылку НЕ логировать.
- создать `backend/app/services/testserver_registry.py` — per-account стор `accounts/<id>/testservers.json`
  `{id, name, ip, iperf_port(5201), created_at}`; CRUD (паттерн `checker_registry.py`); `deploy_script(ip,port)`
  = `test_tools_install_script` + systemd-юнит `iperf3 -s -p <port>` + `ufw allow` только с IP бэкенда/нод.
- создать `backend/app/api/testservers.py` — `GET/POST /api/testservers`, `DELETE /api/testservers/{id}`,
  `POST /api/testservers/deploy` (SSH-деплой, креды транзитные, стрим-Task); под `require_account`, роутер в `main.py`.
- создать `frontend/src/components/settings/TestServers.tsx` — список + «Развернуть по SSH» + «Добавить по IP» +
  delete (переиспользовать паттерн `monitoring/CheckerRegistry.tsx`).
- изменить `frontend/src/components/Settings.tsx` — вкладка «Сервера для тестирования» → `TestServers`.

**Шаги:** `test_tools.py` (инсталлер + iperf/xray-хелперы) → стор реестра (деплой = инсталлер + iperf-сервис) →
API + роутер в `main.py` → вкладка Settings + UI.

**Edge-cases:** пустой реестр; деплой с неверными кредами (ошибка+тост); дубликат IP; порт занят; сервер
недоступен; xray-бинарь не скачался (пометка «xray-тест недоступен», не валить деплой); удаление сервера,
используемого как таргет теста (Ф2/Ф2b деградируют «таргет удалён»).

**Verify-гейт (исполняемый смоук):** `curl POST /api/testservers` (мок SSH или локальный iperf3 в docker) →
запись появляется в `testservers.json`; `GET`/`DELETE` работают; юнит на `test_tools`: парсер vless-ссылки →
валидный xray-конфиг (не логируя ссылку). `python -m py_compile` + `tsc --noEmit`.

**Контракт:** реестр тест-серверов + `GET /api/testservers` + `test_tools.py` (инсталлер и iperf/xray-хелперы).
следующий шаг: Ф2 использует хелперы для проб ноды; Ф2b — для матрицы/xray-link; Ф4 — инсталлер в деплое панели.

## Фаза 2 — Тесты ноды: характеристики/speedtest/xray-link + тумблер install_test_tools
<!-- circle: status=done order=20 deps=[1] autonomy=auto obstacle="" -->

**Подход:** по образцу `POST /api/stats/node` — одна SSH-сессия, параллельные read/бенч-пробы, креды
per-request; история в per-account SQLite (отвергнуто: хранить креды и гонять по cron — нарушает no-secrets).
Тест-инструменты на ноду ставит деплой (тумблер), но проба всё равно гарантирует их наличие (ленивая доустановка).

**Файловый манифест:**
- создать `backend/app/services/speedtest_store.py` — per-account SQLite `accounts/<id>/node_speedtests.db`
  (`runs(ts,resource_key,kind,iperf_mbps,iperf_jitter,st_down,st_up,st_ping,xray_down,xray_up,cpu,ram_mb,disk)`),
  retention 90д (паттерн `metrics_store.py`, async `to_thread`, explicit `account_id`). Общий стор для Ф2 и Ф2b.
- изменить `backend/app/api/stats.py` — `POST /api/stats/node-speedtest` (creds + опц. `testserver_id` +
  опц. `xray_link` + `metrics:[1,2,3]`): SSH → характеристики (`nproc`/`lscpu`/`free -m`/`df -h /`) + Ookla
  `speedtest` (через `test_tools`, fallback) + iperf3 до выбранного тест-сервера (`iperf_client_script`) + при
  наличии `xray_link` — `xray_link_speedtest_script` (скорость через туннель). Записать в стор, вернуть текущий+историю.
- изменить `backend/app/models/deploy.py` — `DeployRequest.install_test_tools: bool = True` (тумблер).
- изменить `backend/app/services/pipeline.py` — новый шаг/врезка `step_test_tools` (гейт на `install_test_tools`,
  дёргает `test_tools.test_tools_install_script`); зарегистрировать в `STEP_LABELS`/`STEP_GROUPS` и в
  `node_ops.Component`/`_UNINSTALL_SCRIPTS` (как `install_vnstat`-гейт).
- изменить `frontend/src/components/DeployForm.tsx` — чекбокс «Установить инструменты тестирования»
  (`install_test_tools`, дефолт вкл) в секции «Оптимизация ОС».
- изменить `frontend/src/components/DeployCard.tsx` — блок «Характеристики и скорость»: значения + кнопка
  «Запустить тест» + селектор тест-сервера (`/api/testservers`) + поле xray-ссылки (опц.) + выбор метрик 1/2/3.

**Шаги:** стор истории → `install_test_tools` (модель+форма+шаг пайплайна) → эндпоинт проб (char+speedtest+iperf+
xray-link) → блок в карточке (селектор таргета/ссылки/метрик).

**Edge-cases:** инструменты не установлены (ленивая доустановка перед прогоном); тест-сервер удалён/недоступен
(ошибка, не 500); speedtest-cli не встал (fallback→skip); битая xray-ссылка (валидация схемы, отказ, не логировать);
`install_test_tools=false` (шаг пропускается со skip-логом, как `install_vnstat`); параллельные запуски; нода за
изменённым SSH-портом; пустая история.

**Verify-гейт:** мок SSH/фикстурные JSON iperf3+speedtest+xray → парсеры дают числа, запись в
`node_speedtests.db`; `curl POST /api/stats/node-speedtest` (мок) → 200; юнит на пропуск `step_test_tools` при
`install_test_tools=false`. Frontend headless — блок с селекторами. `python -m py_compile` + `tsc --noEmit`.

**Контракт:** `install_test_tools` (потребляет Ф4 для панели) + `speedtest_store` (потребляет Ф2b). следующий
шаг: Ф2b строит матрицу поверх `test_tools`-хелперов и `speedtest_store`.

## Фаза 2b — «Тесты скорости»: матрица iperf3 «любой↔любой» + xray-link
<!-- circle: status=pending order=25 deps=[1,2] autonomy=auto obstacle="" -->

**Подход:** раздел «Тесты скорости» в группе «Статистика» — интерактивный запуск iperf3 между любой парой
ресурсов (нода↔нода / нода↔тест-сервер / тест↔тест) + xray-link тест с выбранного источника. Бэкенд оркестрирует
две SSH-сессии (эфемерный `iperf3 -s` на одной стороне, `-c` на другой). Отвергнуто: гонять только до бэкенда —
ТЗ требует произвольные пары; и агент-демон на ресурсах — эфемерный iperf-сервер на время теста дешевле и без
секретов at-rest.

**Файловый манифест:**
- создать `backend/app/api/speedtest.py` — `POST /api/speedtest/pair` (body: сторона A/сторона B — каждая
  `{kind:'node'|'testserver', ip, ssh creds, ssh_port}` + `metrics:[1,2,3]`): SSH к B → `iperf3 -s` эфемерно +
  UFW allow с IP A → SSH к A → `iperf3 -c B` (+ `ping`/`traceroute` по выбранным метрикам) → распарсить → откат
  iperf-сервера/UFW на B → запись в `speedtest_store` (`kind='pair'`). `POST /api/speedtest/xray` (body:
  источник `{kind,ip,creds}` + `xray_link` + `metrics`): `xray_link_speedtest_script` на источнике. Оба —
  стрим-Task; под `require_account`, роутер в `main.py`.
- создать `frontend/src/components/stats/SpeedTests.tsx` — матрица/форма: выбор стороны A и стороны B из
  доступных ресурсов (ноды из `deploy_jobs_<id>`, тест-серверы из `/api/testservers`, панели из `panel_jobs_<id>`),
  чекбоксы метрик 1/2/3, режим «пара» либо «xray-ссылка» (поле ссылки + выбор источника), запуск со стримом +
  таблица истории (из `speedtest_store`). Смонтировать в раздел «Тесты скорости» группы «Статистика».
- изменить `frontend/src/components/Sidebar.tsx` — добавить Tab `stats-speedtests` в группу «Статистика»
  (создать группу, если её ещё нет — план dashboard-monitoring мог не отработать); `frontend/src/App.tsx` —
  роутинг.
- изменить `frontend/src/components/Settings.tsx` — дефолтный набор метрик (1/2/3) в вкладке тест-настроек
  (используется как префилл в форме запуска).

**Шаги:** эндпоинты pair/xray (оркестрация двух SSH, эфемерный iperf-сервер, откат) → раздел SpeedTests (выбор
пары/источника, метрики, стрим, история) → Tab в группе «Статистика» + роутинг.

**Edge-cases:** одна из сторон недоступна (частичный результат + ошибка стороны); A==B (запретить); iperf-порт
занят на B (выбрать свободный/откатить); UFW-allow не откатился (idempotent cleanup в finally); ресурс без
инструментов (`install_test_tools=false` → предложить доустановку); битая xray-ссылка (валидация, не логировать);
пустой список ресурсов (плейсхолдер); группа «Статистика» отсутствует (создать).

**Verify-гейт (исполняемый смоук):** два локальных docker-контейнера с iperf3 (или мок-SSH с фикстурными
JSON-выводами) → `curl POST /api/speedtest/pair` → throughput распарсен, эфемерный сервер и UFW откатились
(проверить cleanup), запись в `speedtest_store`; `POST /api/speedtest/xray` с фикстурной ссылкой → парс без
логирования ссылки. Frontend headless — раздел рендерит выбор пары + метрики, запуск стримит. `python -m
py_compile` + `tsc --noEmit`.

**Контракт:** `POST /api/speedtest/{pair,xray}` + раздел «Тесты скорости». следующий шаг: none (домен speed-тестов
Волны 1 завершён).

## Фаза 3 — Сайдбар-группа «Remnawave» + скелет разделов
<!-- circle: status=done order=30 deps=[] autonomy=auto obstacle="" -->

**Подход:** новая группа + 6 Tab'ов; «Миграция»/«Профили» — заглушки-плейсхолдеры Волны 2 (отвергнуто:
не создавать их сейчас — тогда Волна 2 переверстает сайдбар; дешевле заложить заглушки).

**Файловый манифест:**
- изменить `frontend/src/components/Sidebar.tsx` — группа «Remnawave» с Tab'ами `rw-install`, `rw-migration`
  (заглушка), `rw-variables`, `rw-subpages`, `rw-backup`, `rw-profiles` (заглушка); добавить в Tab-union.
- изменить `frontend/src/App.tsx` — роутинг новых Tab'ов; заглушки — компонент «Раздел появится в Волне 2».
- создать `frontend/src/components/rw/Placeholder.tsx` — переиспользуемая заглушка раздела.

**Шаги:** Tab-union + группа в сайдбаре → роутинг в App → заглушки для migration/profiles.

**Edge-cases:** активный Tab сохраняется (существующий `ni_tab_<id>`); неизвестный Tab → дефолт; мобильный
сайдбар/drawer; session-gate (группа под `require_account` как остальные).

**Verify-гейт:** headless — открыть каждый из 6 Tab'ов → рендерится без падения SPA (реальные — свой контент,
заглушки — плейсхолдер); скриншот сайдбара. `tsc --noEmit`.

**Контракт:** Tab'ы `rw-*` + группа. следующий шаг: Ф4-Ф9 монтируют контент в эти Tab'ы.

## Фаза 4 — Установка панели/подписки: бэкенд-пайплайн
<!-- circle: status=pending order=40 deps=[] autonomy=auto obstacle="" -->

**Подход:** новый пайплайн установки Remnawave (панель И/ИЛИ страница подписок) по образцу `run_pipeline`,
переиспользуя `SSHSession`/`Task`/`build_ssl_script` (отвергнуто: расширять деплой-ноды пайплайн — другой
домен, другой набор шагов; чище отдельный пайплайн). Панель и подписку можно ставить на РАЗНЫЕ серверы
(officially separate-server mode).

**Файловый манифест:**
- создать `backend/app/models/panel_deploy.py` — `PanelDeployRequest` `{target:'panel'|'subpage'|'both', ip,
  ssh creds, panel_domain, sub_domain, reverse_proxy:'caddy'|'nginx'|'traefik'|'angie', enable_webhooks,
  webhook_url, extra_env:dict, sub_server?(отдельный ip/creds для separate-mode), subpage_html_id?(из каталога
  Ф5), install_test_tools:bool=True}` + валидаторы доменов (FQDN-allowlist, как `DeployRequest`).
- создать `backend/app/services/panel_pipeline.py` — шаги: connect → docker install → сгенерировать `.env`
  (`openssl rand -hex 64/24`, домены, webhooks HMAC secret, `extra_env`) → написать `/opt/remnawave/
  docker-compose.yml` (backend:2 + postgres:18.4 + valkey) → reverse-proxy (ветка по `reverse_proxy`) + SSL
  (`build_ssl_script`) → `docker compose up -d` → verify `remnawave` healthy. Для подписки: контейнер
  `remnawave/subscription-page` со своим `.env` (bundled или separate), volume-mount выбранного html (Ф5),
  `SUB_PUBLIC_DOMAIN` на панели. Гейтом `install_test_tools` дёрнуть `test_tools.test_tools_install_script`
  (общий инсталлер из Ф1). Метки шагов через `_begin_step`; `PANEL_STEP_LABELS` в `task_store.py`.
- создать `backend/app/api/panel_deploy.py` — `POST /api/panel/deploy` (стрим-Task), `POST /api/panel/detect`
  (что уже стоит), `POST /api/panel/step` (reinstall/uninstall компонента — для Ф7); под `require_account`,
  роутер в `main.py`.

**Шаги:** модель `PanelDeployRequest` → генератор `.env` + compose → ветки reverse-proxy → SSL → up+verify →
ветка подписки (bundled/separate + html-mount) → API + метки шагов.

**Edge-cases:** reverse-proxy не выбран (валидация — обязателен); порт 80/443 занят (`fuser -k`); домен не
резолвится (для acme HTTP-01 — залогировать); панель и подписка на одном сервере vs разных; повторный деплой
(идемпотентность compose/`.env`); webhook secret < 32 симв (валидация); TZ БД должен остаться UTC (не трогать);
SSH-таргет недоступен → FAILED.

**Verify-гейт:** unit-смоук генераторов — `panel_pipeline` собирает валидный `docker-compose.yml` + `.env`
(проверить наличие обязательных ключей: `DATABASE_URL`, `JWT_AUTH_SECRET`, домены, webhook-заголовки) на
фикстурном запросе; ветки reverse-proxy рендерят непустой конфиг. `python -m py_compile`.

**Контракт:** `PanelDeployRequest` + `POST /api/panel/{deploy,detect,step}` + `PANEL_STEP_LABELS`. следующий
шаг: Ф6 строит дэшборд/формы поверх, Ф7 — управление компонентами через `/api/panel/step`.

## Фаза 5 — Каталог страниц подписок (Orion): загрузка + предпросмотр
<!-- circle: status=pending order=50 deps=[3] autonomy=auto obstacle="" -->

**Подход:** per-account стор HTML-страниц (Orion = один `index.html`), каталог слева + iframe-предпросмотр
справа (отвергнуто: хранить как файлы на диске вне account-изоляции — нарушает per-account; храним в
`accounts/<id>/`).

**Файловый манифест:**
- создать `backend/app/services/subpage_store.py` — per-account стор `accounts/<id>/subpages/` (файлы html +
  `index.json` метаданных `{id,name,created_at}`); CRUD; лимит размера html.
- создать `backend/app/api/subpages.py` — `GET/POST /api/subpages` (POST = загрузка html, multipart/base64),
  `GET /api/subpages/{id}/raw` (для предпросмотра/деплоя), `DELETE`; под `require_account`, роутер в `main.py`.
- создать `frontend/src/components/rw/SubPages.tsx` — каталог (список слева) + предпросмотр (iframe `srcdoc`
  справа) + загрузка своего html; смонтировать в Tab `rw-subpages` (Ф3).

**Шаги:** стор html → API загрузки/чтения → каталог + iframe-предпросмотр + загрузка.

**Edge-cases:** пустой каталог; невалидный/огромный html (лимит, отклонить); XSS в предпросмотре (iframe
`sandbox`, `srcdoc` — не выполнять в контексте панели); дубликат имени; удаление страницы, выбранной в
деплой-форме (Ф6 деградирует).

**Verify-гейт:** `curl POST /api/subpages` (загрузка фикстурного html) → сохранён; `GET …/raw` отдаёт его;
headless — каталог рендерит запись, предпросмотр показывает iframe. `python -m py_compile` + `tsc --noEmit`.

**Контракт:** `GET /api/subpages` + id. следующий шаг: Ф6 деплой-форма подписки выбирает html из каталога.

## Фаза 6 — Установка (frontend): дэшборд + виджет-рамка + деплой-формы
<!-- circle: status=pending order=60 deps=[3,4,5] autonomy=auto obstacle="" -->

**Подход:** `PanelDashboard` по образцу `DeployDashboard` (jobs в localStorage `panel_jobs_<id>`, creds
per-request), но виджет = рамка с 2 подрамками (панель + страница подписок) (отвергнуто: форк
`DeployDashboard` целиком — переиспользуем инфру, но виджет-верстка своя).

**Файловый манифест:**
- создать `frontend/src/components/rw/PanelDashboard.tsx` — список виджетов в localStorage `panel_jobs_<id>`
  (`auth/store.ts` — добавить `panelJobsKey`), `addJob`/`removeJob` функциональным `setState`; смонтировать в
  Tab `rw-install`.
- создать `frontend/src/components/rw/PanelWidget.tsx` — рамка с 2 подрамками: подрамка «Панель» (статус
  онлайн/оффлайн, ip, домен, установлено ли резервное копирование, — поллинг доступности) + подрамка «Подписка»
  (статус, ip, домен); клик по подрамке → открыть модалку (Ф7).
- создать `frontend/src/components/rw/PanelDeployForm.tsx` — форма деплоя (target панель/подписка/оба, домены,
  селектор reverse-proxy, webhooks-тумблер+URL, доп. `.env` key-value редактор, отдельный сервер для подписки,
  выбор html из каталога Ф5 ИЛИ загрузка, тумблер `install_test_tools` дефолт вкл); `validatePanelForm` (экспорт
  для тестов); шлёт `PanelDeployRequest`.
- изменить `frontend/src/auth/store.ts` — `panelJobsKey(accountId)`.

**Шаги:** localStorage-модель jobs → `PanelDashboard` → `PanelDeployForm` (2 таргета, reverse-proxy, webhooks,
extra-env, каталог) → `PanelWidget` (2 подрамки + статус-поллинг) → стрим деплоя (`useTaskStream`).

**Edge-cases:** пустой дэшборд; деплой упал (FAILED + retry, как у нод); панель недоступна (виджет «оффлайн»);
подписка не деплоилась (подрамка «не установлено»); каталог пуст (в форме — «загрузите html»); F5 (карточка
появляется сразу — функциональный `setState`); мобильная верстка (подрамки в колонку); creds только в
localStorage.

**Verify-гейт:** headless — открыть `rw-install`: пустое состояние → добавить (мок `/api/panel/deploy` стрим)
→ виджет-рамка с 2 подрамками появляется сразу, показывает поля статуса; `validatePanelForm` юнит (reverse-proxy
обязателен, webhook URL при включённых webhooks). `tsc --noEmit`.

**Контракт:** `panel_jobs_<id>` схема + `PanelWidget` клик-хук. следующий шаг: Ф7 открывает модалку по клику;
Ф8/Ф9 читают список панелей из `panel_jobs_<id>`.

## Фаза 7 — Управление панелью/подпиской: модалка Компоненты + Статистика
<!-- circle: status=pending order=70 deps=[6] autonomy=auto obstacle="" -->

**Подход:** модалка с 2 вкладками по образцу `DeployCard` `ManageBlock`/`OpStreamModal` (Компоненты) +
`/api/stats/node` (Статистика) (отвергнуто: отдельные страницы — модалка ближе к ТЗ «нажатие на подрамку»).

**Файловый манифест:**
- создать `frontend/src/components/rw/PanelManageModal.tsx` — 2 вкладки. **Компоненты**: список шагов установки
  с переустановить/удалить (через `POST /api/panel/step`, стрим `OpStreamModal`), редактирование домена
  установки и данных сервера (ip/домены/ssh-порт/ssh-пароль → правка записи в `panel_jobs_<id>`).
  **Статистика**: трафик (vnstat) + баны fail2ban — переиспользовать `POST /api/stats/node` (creds из записи).
- изменить `backend/app/api/panel_deploy.py` — `POST /api/panel/step` уже введён в Ф4; здесь довести
  reinstall/uninstall компонентов панели/подписки (compose down/up, замена `.env`/домена).
- изменить `frontend/src/components/rw/PanelWidget.tsx` — открытие `PanelManageModal` по клику на подрамку.

**Шаги:** модалка-скелет 2 вкладки → Компоненты (шаги + правка сервера/доменов + стрим-операции) → Статистика
(трафик+fail2ban через `/api/stats/node`) → связать с виджетом.

**Edge-cases:** сервер недоступен (операции падают понятно); правка ssh-пароля (обновляет только localStorage);
смена домена панели (требует пере-выпуск SSL — предупредить); удаление компонента (двойной confirm, как
`ManageBlock`); vnstat/fail2ban не стоят (блок «нет данных»); параллельные операции.

**Verify-гейт:** headless — клик по подрамке открывает модалку; вкладка Компоненты рендерит шаги + кнопки
(мок `/api/panel/step` стрим); вкладка Статистика показывает трафик/баны (мок `/api/stats/node`). `tsc --noEmit`
+ `python -m py_compile` (правки `panel_deploy.py`).

**Контракт:** лист домена. следующий шаг: none.

## Фаза 8 — Переменные: редактор .env панели
<!-- circle: status=pending order=80 deps=[6] autonomy=auto obstacle="" -->

**Подход:** чтение/запись `/opt/remnawave/.env` на сервере панели по SSH (creds per-request из `panel_jobs`),
редактор key-value (отвергнуто: хранить .env у нас — секреты панели остаются на её сервере).

**Файловый манифест:**
- изменить `backend/app/api/panel_deploy.py` — `GET /api/panel/env` (SSH-чтение `.env`, маскировать секреты в
  ответе), `POST /api/panel/env` (запись + `docker compose up -d` для применения); `domain`/значения
  валидировать; секреты не логировать.
- создать `frontend/src/components/rw/PanelVariables.tsx` — выбор панели (из `panel_jobs_<id>`) + key-value
  редактор `.env` + «Применить»; смонтировать в Tab `rw-variables` (Ф3).

**Шаги:** SSH read/write `.env` (маскирование+не-логирование секретов) → редактор + применение (compose up).

**Edge-cases:** панель не выбрана/не деплоилась; `.env` отсутствует (создать?); нет прав; невалидная пара
(валидация); секретные ключи (`*SECRET*`/`*PASSWORD*`/`*TOKEN*`) — маскировать в UI, не логировать; применение
уронило контейнер (показать логи); SSH недоступен.

**Verify-гейт:** `curl GET /api/panel/env` против бокса с фикстурным `.env` (мок SSH) → пары вернулись,
секреты замаскированы; `POST` пишет и не логирует значения. Frontend headless — редактор рендерит пары.
`python -m py_compile` + `tsc --noEmit`.

**Контракт:** лист домена. следующий шаг: none.

## Фаза 9 — Резервное копирование (distillium): дэшборд + интерактивная настройка
<!-- circle: status=pending order=90 deps=[6] autonomy=auto obstacle="" -->

**Подход:** ставить/конфигурировать distillium `backup-restore.sh` НА сервере панели по SSH (его секреты в
`config.env` chmod 600 на целевом сервере), дэшборд-обёртка над его функционалом (отвергнуто: реализовывать
pg_dump/tar самим — distillium уже покрывает + аплоады; переиспользуем).

**Файловый манифест:**
- создать `backend/app/services/backup_service.py` — SSH-скрипты: установка `backup-restore.sh` в
  `/opt/rw-backup-restore`, запись `config.env` (UPLOAD_METHOD, BOT_TOKEN/CHAT_ID | S3_* | GD_*, CRON_TIMES,
  RETAIN_BACKUPS_DAYS, DB_CONNECTION_TYPE), `setup_auto_send` (cron), ручной `create_backup`, `restore_backup`
  (деструктив → требует явного флага confirm).
- создать `backend/app/api/backup.py` — `POST /api/backup/setup` (установка+config, стрим-Task),
  `POST /api/backup/run`, `POST /api/backup/restore` (confirm-флаг), `GET /api/backup/status` (стоит ли, cron,
  список бэкапов); под `require_account`, роутер в `main.py`.
- создать `frontend/src/components/rw/Backup.tsx` — дэшборд: выбор панели/подписки (из `panel_jobs_<id>`),
  форма настройки (метод аплоуда + расписание + retention), кнопки «Забэкапить сейчас»/«Восстановить» (двойной
  confirm), статус; смонтировать в Tab `rw-backup` (Ф3). Статус backup также питает подрамку виджета (Ф6).

**Шаги:** SSH-обёртка distillium (install+config+cron) → API setup/run/restore/status → дэшборд + интерактивная
форма + confirm на restore → пробросить backup-статус в виджет Ф6.

**Edge-cases:** панель не деплоилась (нельзя настроить); restore ДЕСТРУКТИВЕН (двойной confirm + предупреждение
про чистку тома); секреты аплоуда (в `config.env` на сервере, не у нас, не логировать); cron уже стоит
(перезаписать свою запись); аплоуд-метод не выбран; SSH недоступен; TLS-сертификаты distillium НЕ бэкапит
(предупредить в UI).

**Verify-гейт:** unit-смоук `backup_service` — генерирует валидный `config.env` + cron-строку на фикстурных
настройках (проверить обязательные ключи по методу аплоуда); `curl POST /api/backup/setup` (мок SSH) → 200,
секреты не в логах. Frontend headless — форма + confirm-модалка restore. `python -m py_compile` + `tsc --noEmit`.

**Контракт:** `GET /api/backup/status` (питает виджет Ф6). следующий шаг: none (Волна 1 завершена).

## Журнал

### Ф3 — done (2026-07-07)
Сайдбар-группа «Remnawave» (плоская секция, как «Статистика»/«Инфра-биллинг») + Tab-union `rw-install/
subpages/variables/backup/migration/profiles`; `rw/Placeholder.tsx` (переиспользуемая заглушка). App:
роутинг + CRUMB — install/subpages/variables/backup → Placeholder «Появится в следующих фазах Волны 1»
(Ф4-Ф9 заменят), migration/profiles → «Волна 2». Мобильный drawer подхватывает автоматически. Чисто
аддитивная UI-структура без логики/security — self-review не запускал (skipped:trivial-ui), diff просмотрен
вручную. Verify: tsc clean, frontend **106 passed** (+2 Sidebar-теста), render-смоук 6 rw-табов pageerrors=0.

### Ф2 — done (2026-07-07)
Пайплайн **13→14 шагов**: `step_test_tools` на позиции 5 (внутри «Оптимизация ОС», гейт `install_test_tools`
+ `skip_components`, не-фатален, оба режима до mode-branch); сдвиг ВСЕХ индексов `_begin_step`/`_skip_component`/
haproxy-слот 9→10/skip 11–14; `STEP_LABELS`↔`DEPLOY_STEPS` 14:1, `STEP_GROUPS` 3-5/6-9/11-14. `node_ops`:
компонент `test_tools` (reinstall/uninstall/detect). `models/deploy.install_test_tools=True`, `DeployForm`
тумблер. `speedtest_store.py` (per-account SQLite, explicit account_id, retention 90д). `POST /api/stats/
node-speedtest` (одна SSH-сессия: lazy-install→характеристики→speedtest Ookla/python→iperf3 к тест-серверу
+ping/traceroute по метрикам→xray-туннель; 422 битая ссылка без утечки, 404 чужой testserver, 502 SSH,
warnings не 500) + `GET …/history`. `DeployCard` блок «Характеристики и скорость» (селектор тест-сервера/
xray-ссылка/метрики 1-3/запуск). TDD: парсеры первыми. Ревью (code+security): security чисто по инъекциям/
изоляции; применены — **LOW-1/MED-2 креды xray-ссылки в argv → `SSHSession.get_script_output` (stdin,
явное закрытие канала при таймауте)**, LOW-2 mktemp вместо `/tmp/xray-$$-$RANDOM`, LOW-3/apt-lock per-node
`_INFLIGHT`-lock (409), MED-3 alive-ref против stale-setState, LOW устаревшие комментарии шагов (deploy.py/
pipeline.py). Отклонены/defer: MED-1 (долгий синхронный эндпоинт — per-node lock смягчил, полный Task-стрим
непропорционален), haproxy-рантайм-тест (статический индекс-тест мод-агностичен), юнит `SpeedtestBlock`.
Verify: backend **239 passed** (+32), frontend **104 passed**, tsc clean.

### Ф1 — done (2026-07-07)
`test_tools.py` (инсталлер iperf3+Ookla speedtest c python-fallback+xray-core, все опц. сбои → `[warn]`;
`iperf_server_script` systemd-юнит c явным fail при незапуске (порт занят → FAILED, сервер НЕ регистрируется);
`iperf_client_script` JSON+маркеры+ping/traceroute; `parse_xray_link` vless/trojan/vmess/ss → xray-конфиг
c socks-inbound 127.0.0.1:10808, фиксированные ошибки без утечки ссылки; `xray_link_speedtest_script`
heredoc+trap, замеры down/up/ping через speed.cloudflare.com). `testserver_registry.py` (per-account
`testservers.json`, дубликат ip+port → 409, `deploy_script` c UFW-allowlist backend+ноды, shlex.quote).
`/api/testservers` CRUD + `/deploy` (транзитные креды, стрим-Task). `settings/TestServers.tsx` + вкладка
«Сервера для тестирования»; деплой live-отслеживается `useTaskStream` (ошибка/успех → тост; ref против
stale-closure). TDD: тесты парсера первыми. Ревью: security — чисто (heredoc-побег невозможен: json.dumps
ensure_ascii + `<<'XRAYCFG'`); code — применены H1 (стрим в UI), M2 (явный fail iperf-сервиса), L6 (409);
отклонены: гонка стора (унаследованный паттерн checker_registry), IPv6-приём (осознанно), socks5-проба.
Отклонения от спеки: `test_tools_install_script()` без `extras` (все 3 инструмента всегда),
`xray_link_speedtest_script(link)` без `mode` (down+up+ping всегда) — упрощение, потребители Ф2/Ф2b/Ф4
учитывают; fallback-бинарь называется `speedtest-cli` (Ookla — `speedtest`) — раннер Ф2 должен проверять оба.
Попутно: починен `StepProgress.test.tsx`, сломанный Ф5 прошлого плана (иерархическая нумерация).
Verify: backend **207 passed** (+24), frontend **103 passed** (было 102+1 fail), tsc clean.
