# Волна 3 · План A — Дэшборд: Xray/Server uptime, фиксы, server-uptime монитор

> Пункты пользователя: 1a (мониторинг по умолчанию), 1b (группировка по подписке→странам), 1c (баг появления
> подписки), 1d (вкладка «Server uptime» + ручные сервера), 6b (тип чекера в виджетах статистики).
> Зависимости: subs-aggregator (Волна 2 §4b), xray-checker (§4b), user-stats виджеты (§4d Ф4).

## Контекст (как есть сейчас)

- `frontend/src/components/Dashboard.tsx` — единственный status-page на данных xray-checker: баннер здоровья,
  30-дн аптайм, группы по `groupName` ([Dashboard.tsx:145-152](../../frontend/src/components/Dashboard.tsx)),
  строки нод с бар-грид, инциденты. Полит `/api/checker/statuspage` + `/incidents` каждые 10с.
- Группировка идёт по `n.groupName || "Прочее"`. **Баг:** `groupName` приходит из вывода чекера
  (`/api/v1/proxies`), а `subs-aggregator` тегирует только remark/`name` (`<account>:<sub>|orig`), страну НЕ
  проставляет → у всех нод `groupName` пустой → всё падает в «Прочее».
- `api/xray_checker.py`: `_parse_tag` извлекает **только** `account_id` из тега (sub_id отбрасывается);
  `_filter_by_account` фильтрует по аккаунту и срезает тег; `/statuspage` строит `nodes[]` с `groupName`,
  но без `subId`. `poller_loop` семплит чекер по наименьшему `poll_interval`.
- `subs-aggregator/app.py`: `_tag_config` пишет тег `account:sub|orig` в remark; чекер перечитывает
  `SUBSCRIPTION_URL` только по своему `PROXY_CHECK_INTERVAL` (дефолт из `check_interval`, 300с).
- `models/settings.py::XrayCheckerConfig.enabled = False` (по умолчанию выключен).
- `services/xray_checker.py`: `start`/`stop`/`restart`/`container_state`/`update` контейнера `xray-checker`.
- Виджеты «Самые стабильные/быстрые ноды» — `components/stats/UsersStats.tsx` (e/f), селектор `checker_id`
  из `stats/WidgetSettings.tsx` (список из `/api/checker/instances`).

## Развилки (закреплены)

- Мониторинг по умолчанию **включён**; автостарт в lifespan при наличии `subscription_url` + Docker; иначе тихо.
- Группировка «Xray uptime»: **подписка → страна** (страна из имени ноды; фолбэк «Прочее»).
- Фикс появления подписки — **debounced reload чекера** при CRUD подписок + `/refresh` агрегатора.
- «Server uptime» — TCP(443→22)+ICMP, статус up/slow/down; ручные сервера (CRUD) + автоподтягивание нод.

## Стратегия (порядок ведёт зависимость)

Ф1 (фиксы xray-мониторинга) → Ф2 (backend server-uptime монитор) → Ф3 (frontend: 2 вкладки + server-uptime UI)
→ Ф4 (6b — server-uptime в селекторе виджетов статистики).

---

### Ф1 — Фиксы xray-мониторинга (backend + агрегатор) → verify: юнит + ручной сценарий

1. **Мониторинг по умолчанию** (`models/settings.py`): `XrayCheckerConfig.enabled` → `True`.
   - `main.py` lifespan: после старта — для каждого аккаунта, у кого `enabled` + непустой `subscription_url`
     + Docker доступен, вызвать `xray_checker.start()` (обёрнуть в try/except → тихий лог, НЕ падать; если
     `_NO_DOCKER` — пропустить). Идемпотентно (если контейнер уже поднят — no-op).
   - verify: свежий аккаунт с заданным `subscription_url` → после рестарта backend контейнер `xray-checker`
     сам поднимается; без Docker — `container_state()=="no-docker"`, дэшборд показывает «не настроен», без 500.

2. **Группировка по подписке → странам** (`api/xray_checker.py`):
   - `_parse_tag` → вернуть **`(account_id, sub_id, orig_name)`** (сейчас sub_id отбрасывается). Обновить всех
     вызывающих (`_filter_by_account`).
   - `/statuspage` `nodes[]`: добавить поле **`subId`** (из тега; untagged → `""`). Пробросить также
     человекочитаемую метку подписки: backend отдаёт карту `subId → sub_label` (из
     `storage.load_subscriptions(account)`; метка = усечённый url или заданное имя). Вернуть в ответе
     `subscriptions: [{id,label}]` рядом с `nodes`.
   - Страну оставляем как есть в `groupName` (может быть пусто) — **страну парсит фронт** из имени ноды
     (переиспользовать логику `flagFor`); backend не меняет способ добычи страны.
   - verify: `backend/tests/test_xray_checker.py` — `_parse_tag` на 3-компонентном теге; `/statuspage`
     возвращает `subId` + `subscriptions`; untagged-фолбэк не ломается.

3. **Фикс «новая подписка видна только после Обновить / не появляется»** — корень: чекер не перечитывает
   подписку до истечения `check_interval`; поллер семплит то, что чекер уже знает.
   - `api/subscriptions.py`: при **add / enable-toggle / URL-change / refresh** после уведомления агрегатора
     (`POST /refresh`) запустить **debounced reload чекера**: `xray_checker.restart()` (перечитает
     `SUBSCRIPTION_URL` при старте). Debounce (например 5–10с, module-scoped asyncio task, отменяемая) — чтобы
     пакет CRUD-операций дал один рестарт. Обернуть в try/except (нет Docker/remote — пропустить).
   - **РАЗВЕДКА (для исполнителя):** проверить, есть ли у `kutovoys/xray-checker` эндпоинт reload подписки
     (тогда вместо `restart` дёрнуть его — мягче). Если нет — `restart` контейнера остаётся решением.
   - Ошибки апстрима: `/api/subscriptions/status` уже мержит `last_error` из агрегатора — убедиться, что
     фронт показывает и «вторая подписка: 0 конфигов» (пустой апстрим без ошибки) отдельной пометкой.
   - verify: добавить 2-ю подписку → в пределах debounce+один цикл поллинга её ноды появляются на дэшборде без
     ручного refresh; подписка с 0 конфигов помечена «0 конфигов», с ошибкой — красной строкой.

---

### Ф2 — Backend server-uptime монитор → verify: юнит-тесты стора + endpoints

Новый монитор доступности сервера по IP, независимый от xray-checker. Зеркалит паттерны `metrics_store` +
`checker_registry`, per-account.

1. **`services/server_monitor_store.py`** — per-account SQLite `accounts/<id>/server_monitor.db` (explicit
   `account_id`, lazy-schema под `threading.Lock`, как `infra_billing_store`/`user_stats_store`):
   - `servers(id, name, country, ip, port, note, source['manual'|'deployed'], created_at)` — реестр серверов.
   - `server_samples(ts, server_id, online, latency_ms)` (индексы на ts, (server_id,ts)); ретенция 35 дней.
   - CRUD: `add_server`/`update_server`/`delete_server`/`list_servers`; `record_samples`; аналитика зеркалит
     `metrics_store`: `get_bars(n)`, `get_uptime_30d()`, `get_incidents(days)`, `get_node_uptime(hours)`.
   - `source='deployed'` записи не хранятся в БД как ручные — **виртуально** подмешиваются из `deploy_jobs`?
     НЕТ: `deploy_jobs` — это localStorage браузера, backend их не видит. **Решение:** фронт при загрузке
     «Server uptime» отправляет backend список задеплоенных {name,country,ip,port} (из `deploy_jobs_<id>`),
     backend **upsert-ит** их как `source='deployed'` (обновляет по ip). Ручные (`source='manual'`) — чистый CRUD.
   - Онлайн-проба: `probe(ip, port)` → TCP-connect (asyncio.open_connection, порт → фолбэк 22) + ICMP-ping
     (через `asyncio.create_subprocess_exec('ping', ...)` кроссплатформенно, или raw-socket недоступен без
     root → используем системный `ping`). Статус: `up` (connect ok), `slow` (RTT ≥ порог, дефолт 800мс), `down`.
     RTT берём из TCP-connect времени; ICMP как фолбэк, если TCP-порт закрыт, но хост пингуется (тогда `up`/`slow`
     по ICMP RTT). Порог RTT — константа `SLOW_MS` (как в `metrics_store`).

2. **`api/server_monitor.py`** (`/api/server-monitor`, под `require_account`):
   - `GET/POST /servers`, `PATCH/DELETE /servers/{id}` — ручные сервера (CRUD; `source='manual'`).
   - `POST /servers/sync-deployed` — тело `[{name,country,ip,port}]` из `deploy_jobs`; upsert `source='deployed'`.
     (Удалённые из `deploy_jobs` ноды — чистим по отсутствию в списке.)
   - `GET /statuspage?ticks=N` + `GET /incidents?days=N` — тот же формат ответа, что у
     `/api/checker/statuspage`/`incidents` (баннер/ноды/бары/аптайм/инциденты), чтобы фронт переиспользовал
     компоненты. Группировка на фронте — по стране (у server-monitor `country` есть напрямую).
   - IP-валидация: только публичные адреса? Ручные сервера могут быть в приватной сети оператора — **НЕ**
     применять SSRF-guard жёстко (это не user-supplied URL, а IP серверов оператора); валидировать формат IP.

3. **Поллер** `api/server_monitor.py::monitor_loop` — lifespan background task (зеркалит `poller_loop`):
   каждые N сек (константа, дефолт 60с) по всем аккаунтам пробит их серверы (concurrent, `asyncio.gather`
   с cap), пишет `record_samples`. Резистентный (per-account try/except, не умирает).
   - Запуск в `main.py` lifespan рядом с `poller_loop`/`collector_loop`.
   - verify: `backend/tests/test_server_monitor.py` — CRUD + isolation + `probe` на локальном порту (up) и
     закрытом (down) + `get_uptime_30d`/incidents на синтетических семплах.

---

### Ф3 — Frontend: 2 вкладки + Server-uptime UI → verify: dev-preview + скриншот

1. **2 горизонтальные вкладки** на дэшборде («Xray uptime» / «Server uptime») — `Dashboard.tsx`:
   - Вынести текущее тело статуса в компонент `XrayUptime` (без изменений логики) — вкладка 1.
   - Добавить `.seg`-переключатель вкладок (как в Settings→«Тема»), состояние в компоненте (можно не
     персистить, либо `ni_dash_tab_<accountId>` по аналогии с `tabKey`).

2. **Группировка Xray по подписке → странам** (`XrayUptime`):
   - Верхний уровень — группы по `subId` (метка из `subscriptions[]`; untagged → «Без подписки»). Вложенный —
     под-группы по стране (`flagFor(country)` из имени ноды, как сейчас). Обе свёртки — как текущий `collapsed`.
   - verify: 2 подписки → 2 верхних группы, внутри страны; ноды больше НЕ сваливаются все в «Прочее».

3. **Вкладка «Server uptime»** — новый компонент `components/ServerUptime.tsx`:
   - Тот же статус-лейаут (баннер/группы-по-странам/строки/инциденты), данные из
     `/api/server-monitor/statuspage` + `/incidents`. Переиспользовать `NodeRow`/`BANNER`/бар-грид (вынести
     общие куски из `Dashboard.tsx` в `components/statuspage/` или экспортировать).
   - На маунте POST `/server-monitor/servers/sync-deployed` c текущими `deploy_jobs_<id>` (успешные remnanode
     ноды: name/country/ip/port по `change_ssh_port`-логике не нужен — это мониторинг доступности сервера, порт
     = 443/фолбэк 22 по умолчанию).
   - **Ручные сервера**: кнопка «Добавить сервер» → модалка (Название / Страна — `CountrySelect` / IP / Порт
     (опц., дефолт 443) / Примечание). Строки `source='manual'` — с кнопками **редактировать** и **удалить**
     (два клика). Строки `source='deployed'` — read-only, с пометкой «из деплоя».
   - verify: dev-preview — добавить ручной сервер, увидеть его в статусе; отредактировать; удалить;
     задеплоенные ноды подтянулись автоматически.

---

### Ф4 — 6b: server-uptime как тип чекера в виджетах статистики → verify: preview

- `stats/WidgetSettings.tsx`: селектор `checker_id` для виджетов «Самые стабильные»/«Самые быстрые» дополнить
  опцией **«Server uptime»** (виртуальный источник, напр. `checker_id='server-monitor'`).
- `components/stats/UsersStats.tsx` (виджеты e/f): когда выбран `server-monitor`, тянуть аптайм/латентность из
  `/api/server-monitor/statuspage` (uptime30d / latencyMs) вместо `/api/checker/*`.
- backend: убедиться, что server-monitor отдаёт per-node uptime30d + latency (уже в Ф2).
- verify: переключить источник в шестерёнке виджета → данные меняются на server-uptime.

## Критерии готовности плана A

- Свежий backend с заданным `subscription_url` → xray-checker стартует сам; дэшборд не в «Прочее», а сгруппирован
  по подписке→странам; 2-я подписка появляется без ручного refresh.
- Вкладка «Server uptime» работает: ручной CRUD серверов + автоподтягивание нод + бары/аптайм/инциденты.
- Виджеты статистики умеют показывать server-uptime.
- `py_compile` backend, `pytest` (новые test_server_monitor + обновлённый test_xray_checker), `tsc --noEmit`,
  dev-preview скриншот обеих вкладок.
