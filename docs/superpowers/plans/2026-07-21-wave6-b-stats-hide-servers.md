# Волна 6 · План B — Скрытие серверов в виджетах статистики

> Дать оператору возможность убрать конкретный сервер из виджетов «Статистика → Пользователи» (и вернуть обратно).
> Мотив реальный, а не косметический: `user_stats.db` хранит 35 дней истории и отдаёт КАЖДУЮ ноду, у которой есть
> хоть один сэмпл в окне, — удалённая из Remnawave нода висит в виджетах до 35 дней, механизма «надгробия» нет.
> **Блокирующий факт:** идентичность сервера НЕ едина между виджетами — три непересекающихся пространства id
> (`node_uuid` Remnawave · `stableId` xray-checker · row-id `server_monitor`), моста между ними в коде нет.
> Поэтому один плоский набор скрытых id невозможен; план вводит ДВЕ оси и отдельно решает третью.
> Затрагивает: `backend/app/api/user_stats.py` (+`backend/tests/test_stat_widgets.py`),
> `frontend/src/components/stats/{statWidgetsStore.ts,UsersStats.tsx}` + новый `stats/HiddenServers.tsx`,
> и отдельной фазой — `backend/app/{services/server_monitor_store.py,api/server_monitor.py}` +
> `frontend/src/components/Dashboard.tsx`.

## Контекст (как есть)

- **Стор раскладки уже есть и почти готов принять набор.** `statWidgetsStore.ts:18-24` —
  `WidgetInstance {instanceId, kind, w, order, settings}`; персист двойной: синхронный
  `localStorage.setItem('stat_widgets_<accountId>')` (`:50`) + дебаунс 600 мс `PUT /api/stats/users/widgets`
  (`:52-59`), ошибки PUT глотаются. Гидратация: сервер → localStorage → `defaultLayout()` (`:78-89`).
- **`WidgetInstance.settings` — МЁРТВОЕ поле.** Нормализуется на входе (`statWidgetsStore.ts:43`) и сериализуется
  на выходе (`:56`), но НИ ОДИН виджет его не читает: все per-widget настройки — локальный `useState`
  (`UsersStats.tsx:228,242,262,283,319,340`). Бэкенд валидирует его как свободный `dict` **без ограничения
  размера** (`api/user_stats.py:63`), контракт-тест гоняет `settings:{"hours":168}` (`tests/test_stat_widgets.py:20`).
- **Три пространства id (проверено чтением):**
  - `node_uuid` (Remnawave) — `WNodeLoad`/`WAvgPerNode` ключуются на `n.node_uuid`
    (`UsersStats.tsx:180,190,204,216,252`), `WMigrations` оперирует `from_node`/`to_node`, которые резолвятся
    через страничный `nameMap`, собираемый из `/api/stats/users/node-load?hours=720` (`UsersStats.tsx:380-384`).
  - `stableId` — `WStableNodes`/`WFastNodes` (`UsersStats.tsx:330,351`), но **namespace переключается в рантайме**
    собственным селектором виджета: `_statusUrl(cid)` (`UsersStats.tsx:312-316`) ведёт либо на
    `/api/checker/statuspage?checker_id=<cid>` (proxy-id xray-checker), либо на
    `/api/server-monitor/statuspage` (row-id реестра, `api/server_monitor.py:129` — `"stableId": s["id"]`).
  - `username` — `WTopUsers` (`UsersStats.tsx:273`); измерение ноды схлопнуто ещё в SQL:
    `SELECT username, MAX(total_bytes) … GROUP BY username` (`services/user_stats_store.py:153-157`).
- **`stableId` сам по себе НЕ ключ** — независимое подтверждение из кода: ring-буфер метрик ключуется
  `(checker_id, stable_id)` с явным комментарием «тот же stable_id может существовать на разных инстансах»
  (`services/metrics_store.py:38-41`), плюс задокументированный caveat на `_cid_clause`
  (`services/metrics_store.py:164-176`).
- **Фильтровать можно целиком на клиенте.** `/node-load`, `/top-users`, `/migrations` принимают ТОЛЬКО `hours`
  (clamp 1..720) — `api/user_stats.py:34-46`; никаких exclude-параметров нет. Виджеты и так получают полный набор
  и режут его локально: `slice(0,6)` в графике (`UsersStats.tsx:128`), `slice(0,8)` в
  `WAvgPerNode`/`WStableNodes`/`WFastNodes` (`:244,323,343`). ⇒ скрытие не требует backend-работы **по данным** и
  попутно даёт контроль над тем, кто попадает в этот top-N (сегодня это чистый ранг по avg/uptime/latency).
- **Контракт виджет-документа.** `GET /widgets` → `{layout: […]}` (пусто → фронт сеет дефолтные 6),
  `PUT /widgets` — **полная замена** документа (`api/user_stats.py:77-87`); модель `WidgetLayout{layout}`
  (`:73-74`), `WidgetInstance` валидирует `instance_id`(1..64)/`kind`(closed enum)/`w`(1..2) (`:58-70`).
  Хранилище — `storage.load_stat_widgets/save_stat_widgets` → `accounts/<id>/stat_widgets.json`
  (`services/storage.py:142-149`) ⇒ per-account изоляция бесплатна, ключ localStorage тоже per-account
  (`statWidgetsStore.ts:26`).
- **Мотив (почему это не «хотелка»):** `_node_load` делает `GROUP BY node_uuid` по окну без проверки живости
  (`services/user_stats_store.py:116-146`), ретенция 35 дней (`:25`). Нода, удалённая из панели, остаётся в
  трёх виджетах до 35 дней (и все 30 дней при окне «30 дней»).
- **Дэшборд → «Доступность серверов» — отдельная проблема того же класса.** На каждом маунте вкладка
  ре-постит браузерные `deploy_jobs` в `/api/server-monitor/servers/sync-deployed`
  (`Dashboard.tsx:377-396`), а кнопки редактирования/удаления отрисовываются ТОЛЬКО для `source === 'manual'`
  (`Dashboard.tsx:471-484`; у deployed — статичная подпись «авто»). Даже если открыть удаление, `_sync_deployed`
  вставит строку обратно (`services/server_monitor_store.py:182-187`), а `_delete_server` вдобавок вычищает
  `server_samples` (`:153-157`) — то есть удаление разрушает историю. Зато апсерт существующей deployed-строки
  трогает только `name, country, port` (`:178-181`) ⇒ **колонка `hidden` переживёт ре-синк нетронутой.**
- **Расхождения CLAUDE.md ↔ код (прав КОД, чинить CLAUDE.md §4d, «Widget editor (Wave-5 Plan G)»):**
  1. Сказано «Backend `GET/PUT /api/stats/users/widgets` (same `stats.router`)» — на деле роуты на
     `user_stats.router` (`api/user_stats.py:24`, регистрация `main.py:134`); `stats.router` — другой роутер
     (`main.py:125`). Косметика, но сбивает при grep-е «куда добавить роут».
  2. Не упомянуто, что backend-энум `_WIDGET_KINDS` содержит **восемь** типов — шесть реализованных плюс
     `uptime-summary` и `speedtest-history` (`api/user_stats.py:51-54`), которых во фронте нет вообще.
     `normalize` их отфильтрует (`statWidgetsStore.ts:36-37`), и следующий `add/remove/resize/move` сотрёт их
     с сервера. Латентно (создать такой инстанс из UI нельзя).

## Развилки (закреплены)

- **Две оси, а не один плоский набор.** `hidden.nodes` — `node_uuid` Remnawave (применяется к node-load /
  avg-per-node / migrations); `hidden.checker` — карта `{checker_id: {stableId: …}}` (применяется к
  stable-nodes / fast-nodes). Причина — три disjoint-пространства id без кросс-мапа в коде (см. Контекст), а
  `stableId` без `checker_id` не уникален (`metrics_store.py:38-41`). Плоский набор физически не может работать.
- **Набор — страничный per-account, НЕ per-instance.** Хранится **соседним ключом `hidden` на `WidgetLayout`**,
  а НЕ внутри `WidgetInstance.settings`. Мёртвое `settings` выглядит готовым местом, но это ловушка: палитра
  разрешает дубликаты одного `kind` (`UsersStats.tsx:401-405`), и наборы у инстансов немедленно разъедутся.
  Скрытие ноды — утверждение о ноде, а не о карточке. Один источник истины, PUT остаётся атомарным.
- **`PUT` — полная замена документа, значит фронт ВСЕГДА шлёт `layout` + `hidden` вместе.** Сегодняшний
  `persist(layout)` (`statWidgetsStore.ts:49`) обязан стать `persist()` от целого состояния стора — иначе любой
  add/remove/resize сотрёт набор скрытых. Это главный риск фазы Ф2.
- **Фильтрация — 100% на клиенте.** Эндпоинты и так отдают всё (`api/user_stats.py:34-46`), новых query-параметров
  не вводим. Никаких изменений в SQL `user_stats_store`.
- **«Топ пользователей» — ВНЕ объёма v1.** Он ключуется на `username`, а нода схлопнута в SQL
  (`user_stats_store.py:153-157`) — «скрыть сервер ⇒ скрыть его юзеров» требует НОВОГО backend-параметра и
  отдельного продуктового решения. DEFAULT: виджет не участвует в скрытии; в пикере под ним подпись-пояснение.
- **`server-monitor` в статистике НЕ дублируется отдельным набором.** Виджет, переключённый на
  `cid === 'server-monitor'`, читает `/api/server-monitor/statuspage`, а Ф4 заставляет этот эндпоинт исключать
  скрытые строки на бэкенде ⇒ подавление приходит «бесплатно» и из ОДНОГО места (SQLite-колонка `hidden`).
  Поэтому селектор инстансов в пикере статистики показывает только xray-чекеры (без пункта «Server uptime»),
  а рядом — подсказка «серверы из Server uptime скрываются на вкладке Дэшборд → Доступность серверов».
  Так мы избегаем двух конкурирующих механизмов для одних и тех же id.
- **Скрытие = только отображение, работа продолжается.** `monitor_loop` продолжает пробить скрытые серверы
  (`api/server_monitor.py:208-219`), коллектор — снимать скрытые ноды. Бары/аптайм не рвутся, отмена скрытия
  возвращает непрерывную историю. Пересматривать — только если стоимость проб реально станет жалобой.
- **Хранить рядом с id последнее известное имя** (`{id: "имя"}`, а не голый список). Причина: `stableId`
  генерируется апстримом xray-checker из записи подписки (`services/xray_checker.py:16`), мы его нигде не
  пиним; id, пропавший из выдачи, иначе отрисуется в пикере голым хексом. Пропавшие записи — отдельная группа
  «не найдено» с пер-строчным «забыть».
- **Расхождения CLAUDE.md — чиним ДОКУМЕНТ, код не трогаем.** Энум из 8 типов оставляем как есть (из UI такой
  инстанс не создать, вреда сегодня нет); §3 CLAUDE.md запрещает «улучшать» соседний код. Если фаза всё равно
  редактирует `_WIDGET_KINDS` — можно заодно сузить до шести, но это НЕ обязательное условие готовности.
- **DEFAULT для мелочей, чтобы исполнитель не останавливался:** миграция скрывается, если скрыт ЛЮБОЙ из её
  концов (`from_node` или `to_node`); при полностью пустом после фильтра виджете показываем не «Данных пока нет»,
  а «Все серверы скрыты» (когда набор непуст); лимиты — ≤200 `node_uuid`, ≤20 checker_id × ≤200 `stableId`,
  имя обрезается до 64 символов; старый документ без `hidden` → пустой набор.

## Стратегия

Ф1 (backend: ключ `hidden` в документе виджетов) → Ф2 (стор: состояние + действия + персист целого документа +
чистые селекторы) → Ф3 (UI: пикер «Серверы» и применение фильтра в 5 виджетах) → Ф4 (независимо: скрытие на
Дэшборд → Доступность серверов) → Ф5 (CLAUDE.md + починка двух расхождений в документации).

---

### Ф1 — Backend: соседний ключ `hidden` на документе виджетов → verify: pytest

- **`backend/app/api/user_stats.py`**:
  - Новая модель рядом с `WidgetInstance` (`:58`):
    ```python
    class HiddenSet(BaseModel):
        nodes: dict[str, str] = Field(default_factory=dict)              # node_uuid -> last-known name
        checker: dict[str, dict[str, str]] = Field(default_factory=dict) # checker_id -> {stableId -> name}
    ```
    + `field_validator`-ы: `len(nodes) <= 200`, `len(checker) <= 20`, в каждой вложенной карте ≤200 записей,
    значения-имена усечь до 64 символов (не 422 на длинное имя — просто обрезать; 422 только на превышение
    количественных лимитов). Это заодно закрывает существующую дыру «свободный `dict` без cap-а» — но ТОЛЬКО
    для нового поля, `settings` не трогаем.
  - `WidgetLayout` (`:73`) += `hidden: HiddenSet = Field(default_factory=HiddenSet)`.
  - `GET /widgets` (`:77-81`) → `{"layout": …, "hidden": doc.get("hidden") or {"nodes": {}, "checker": {}}}`.
  - `PUT /widgets` (`:83-87`) → сохранять `{"layout": [...], "hidden": body.hidden.model_dump()}` и возвращать
    сохранённое. **Помнить: PUT — full-replace, тело без `hidden` обнулит набор** (это и есть контракт;
    задача фронта — слать целиком).
- Изоляция/хранилище — без изменений: `storage.load_stat_widgets/save_stat_widgets` (`services/storage.py:142-149`),
  роутер уже под `require_account` (`main.py:134`).
- **`backend/tests/test_stat_widgets.py`** — дописать: (a) `GET` на свежем аккаунте отдаёт
  `{"layout": [], "hidden": {"nodes": {}, "checker": {}}}`; (b) round-trip обеих осей; (c) документ без `hidden`
  (записанный старой версией) читается без ошибки; (d) 422 на превышение лимитов; (e) имя >64 обрезается;
  (f) изоляция между аккаунтами для `hidden`.
- → verify: `cd backend && python -m pytest tests/test_stat_widgets.py -q`, затем полный
  `cd backend && python -m pytest`.

---

### Ф2 — Frontend: состояние `hidden` в сторе + чистые селекторы → verify: tsc + vitest

- **`frontend/src/components/stats/statWidgetsStore.ts`**:
  - Тип `export interface Hidden { nodes: Record<string,string>; checker: Record<string, Record<string,string>> }`,
    поле `hidden: Hidden` в `State` (`:62-72`), пустой дефолт.
  - **`persist()` переписать на целый документ**: сегодня `persist(layout)` (`:49-60`) сериализует только layout —
    сделать `persist(layout, hidden)` (или `persist(get())`) и слать `{layout: […], hidden}` в PUT, а в
    localStorage класть `{layout, hidden}`. **Обратная совместимость localStorage:** старый ключ содержит ГОЛЫЙ
    массив — в `hydrate` распознавать оба формата (`Array.isArray(parsed) ? {layout: parsed, hidden: empty} : parsed`).
  - `hydrate()` (`:78-89`) читает `hidden` с сервера, при неудаче — из localStorage, иначе пусто. Нормализовать:
    выбросить не-строковые значения, обрезать имена, применить те же лимиты, что и на бэкенде (защита от
    подпорченного localStorage).
  - Действия: `hideNode(uuid, name)`, `showNode(uuid)`, `hideCheckerNode(cid, stableId, name)`,
    `showCheckerNode(cid, stableId)` — каждое зовёт `persist()`. Отдельного «forget» не нужно: «показать» и
    «забыть пропавший» — одно и то же действие (удаление ключа).
  - **Чистые экспортируемые селекторы** (их и тестируем, без рендера):
    `isNodeHidden(h, uuid)`, `filterNodeLoad(h, nodes)`, `filterMigrations(h, migs)`,
    `filterCheckerNodes(h, cid, nodes)`. Для `cid === 'server-monitor'` `filterCheckerNodes` — **passthrough**
    (подавление приходит с бэкенда, Ф4).
- **`frontend/src/components/stats/statWidgetsStore.test.ts`** (новый, vitest подхватывает
  `src/**/*.{test,spec}.{ts,tsx}` — `frontend/vitest.config.ts`): миграция старого localStorage-формата
  (голый массив); `persist` шлёт layout+hidden вместе (мок `fetch`, проверка тела); скрытие/показ по обеим осям;
  `filterMigrations` режет по любому из концов; `filterCheckerNodes` изолирует по `checker_id` (один и тот же
  `stableId` под двумя cid скрывается независимо); passthrough для `server-monitor`; нормализация мусора и лимитов.
- → verify: `cd frontend && npx --no-install tsc --noEmit` и `cd frontend && npm test`.

---

### Ф3 — Frontend: пикер «Серверы» + применение фильтра в виджетах → verify: tsc + vitest + preview

- **`frontend/src/components/stats/HiddenServers.tsx`** (новый) — модалка/поповер из шапки страницы, две секции:
  - **«Ноды Remnawave»** — источник списка уже есть на странице: `nameMap` строится из
    `/api/stats/users/node-load?hours=720` (`UsersStats.tsx:380-384`) — прокинуть его в пикер пропом, НОВЫХ
    запросов не добавлять. Строка = имя + переключатель «глаз»; пишет в `hideNode/showNode`.
  - **«Мониторинг»** — селектор инстанса (переиспользовать `CheckerSelect`, `UsersStats.tsx:100-111`, но
    **без пункта `server-monitor`** — см. развилку; вместо него подсказка со ссылкой-текстом на Дэшборд) +
    ленивый `fetch(_statusUrl(cid))` для списка узлов этого инстанса; пишет в `hideCheckerNode/showCheckerNode`.
  - **Группа «не найдено»** в каждой секции: записи из `hidden`, отсутствующие в текущей выдаче, — с сохранённым
    именем и кнопкой «забыть» (тот же `show*`). Это и есть страховка от смены `stableId` апстримом.
  - Под секциями — строка-пояснение: «“Топ пользователей” считается по пользователям и не фильтруется по серверам».
- **`frontend/src/components/stats/UsersStats.tsx`** — точки применения (фильтровать ДО срезов top-N):
  - `WNodeLoad` (`:230`) — `filterNodeLoad` перед передачей в `NodeLoadChart` (внутренний `slice(0,6)` на `:128`
    тогда увидит только видимые).
  - `WAvgPerNode` (`:244`) — фильтр до `.slice(0, 8)`.
  - `WMigrations` (`:286`) — `filterMigrations`.
  - `WStableNodes` (`:321-323`) / `WFastNodes` (`:342-343`) — `filterCheckerNodes(h, cid, …)` до сортировки/среза.
  - `WTopUsers` (`:261-280`) — **не трогать**.
  - Пустое состояние: если после фильтра пусто И набор скрытых непуст — вместо «Данных пока нет» показать
    «Все серверы скрыты» (одна ветка в `State`/в месте вызова, без рефактора атома).
  - Кнопка «Серверы» (иконка `EyeOff`) в `ni-pagehead-actions` (`:395-413`) рядом с «Редактировать»; бейдж с
    числом скрытых, когда набор непуст. Доступна ВНЕ режима редактирования (скрытие — повседневное действие,
    а не редактирование раскладки).
- Тема — только var-токены, никаких внешних ассетов (CSP-self-contained), мобильная вёрстка ≤820px не ломается
  (пикер как обычная `.modal`/bottom-sheet — паттерн уже в проекте).
- → verify: `cd frontend && npx --no-install tsc --noEmit`; `npm test`; `docker compose build frontend`;
  ручной preview: скрыть ноду → она исчезает из графика/среднего/миграций и её место в top-N занимает следующая;
  F5 (пережило localStorage) → смена аккаунта (свой набор) → «показать» возвращает историю целиком.

---

### Ф4 — Дэшборд → «Доступность серверов»: скрытие вместо невозможного удаления → verify: pytest + preview

Независимая фаза (не требует Ф1–Ф3), закрывающая реальный тупик: deployed-строку сегодня нельзя убрать вообще.

- **`backend/app/services/server_monitor_store.py`**:
  - Схема `servers` (`:64-74`) += `hidden INTEGER NOT NULL DEFAULT 0`; для существующих БД — идемпотентный
    `ALTER TABLE servers ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0` в try/except (паттерн уже применялся в
    `metrics_store` для `checker_id`, см. CLAUDE.md §4b).
  - `_row_to_server` (`:98-103`) += `"hidden": bool(r["hidden"])`.
  - **Новая функция `_set_hidden(sid, hidden, account_id)`** — отдельно от `_update_server`, потому что тот
    намеренно ограничен `AND source = 'manual'` (`:143`), а скрывать нужно в первую очередь **deployed**-строки.
  - `_sync_deployed` НЕ менять: апсерт трогает только `name, country, port` (`:178-181`) ⇒ флаг переживает
    ре-синк. Удаление строк, ушедших из `deploy_jobs` (`:188-192`), оставляем как есть.
- **`backend/app/api/server_monitor.py`**:
  - `ServerUpdate` (`:56-70`) += `hidden: Optional[bool]`; в `patch_server` (`:92-97`) — если пришёл `hidden`,
    звать `store.set_hidden(...)` (для ЛЮБОГО `source`), остальные поля — прежним путём.
  - `statuspage` (`:117-157`): в узлы добавить `"hidden": s["hidden"]`, а `total/online/gstate/uptime30d`
    (`:144-153`) считать **только по нескрытым**. Так вкладка «Статистика» с `cid='server-monitor'` получает
    подавление автоматически (см. развилку), а баннер здоровья не портится скрытыми.
  - `monitor_loop`/`_monitor_account` (`:208-240`) — **не трогать**: скрытые продолжают пробиться.
- **`frontend/src/components/Dashboard.tsx`** (вкладка «Доступность серверов»): в `trailing` строки
  (`:471-484`) добавить кнопку «глаз» — для **обоих** `source` (для deployed это единственный способ убрать
  сервер с глаз); скрытые не попадают в группы по странам, а собираются в свёрнутый блок «Скрытые (N)» с
  кнопкой возврата. Удаление (`removeServer`, `:407-410`) оставить только у manual и оставить `confirm` —
  оно по-прежнему разрушает `server_samples` (`server_monitor_store.py:153-157`), скрытие — нет.
- **`backend/tests/test_server_monitor.py`** — дописать: PATCH `hidden` работает для `source='deployed'`;
  флаг переживает повторный `POST /servers/sync-deployed`; `statuspage` не отдаёт скрытые в счётчиках и метит их
  флагом; `delete` по-прежнему 404/204 как раньше.
- → verify: `cd backend && python -m pytest tests/test_server_monitor.py -q`; preview: скрыть авто-сервер →
  перезагрузить страницу (ре-синк не вернул его в список) → вернуть → бары и 30-дневный аптайм на месте.

---

### Ф5 — Документация: CLAUDE.md + два расхождения → verify: чтение

- **CLAUDE.md §4d** («Widget editor (Wave-5 Plan G)»): (a) исправить «same `stats.router`» → роуты на
  `user_stats.router` (`api/user_stats.py:24`, `main.py:134`); (b) отметить, что backend-энум содержит 8 типов
  против 6 во фронте и что `normalize` молча отбрасывает лишние; (c) описать новый ключ `hidden` (две оси,
  page-global, клиентская фильтрация, «Топ пользователей» не участвует).
- **CLAUDE.md §9b** (server_monitor): описать колонку `hidden`, `PATCH …/servers/{id}` с `hidden` для любого
  `source`, исключение скрытых из счётчиков `statuspage` и то, что пробы продолжаются.
- → verify: перечитать изменённые секции; `grep -n "stats.router" CLAUDE.md` больше не указывает на виджеты.

## РАЗВЕДКА (факты)

- **Мост между `node_uuid` и `stableId` в репозитории ОТСУТСТВУЕТ** — проверено чтением всех точек, где
  фигурируют оба: `UsersStats.tsx` (два независимых набора виджетов), `api/xray_checker.py:202-215`
  (узлы статус-страницы собираются из proxy-полей апстрима, `node_uuid` там нет),
  `api/server_monitor.py:129` (`stableId = s['id']`, row-id реестра). Не пытаться «сматчить по имени»:
  имя xray-прокси приходит из подписки и чистится от account-тега (`api/xray_checker.py:184`), совпадение с
  именем ноды Remnawave — совпадение, а не контракт.
- **Именно поэтому нужны две оси**, а «server-monitor» — третья, решаемая на бэкенде (Ф4), а не третьим набором.
- **`GET /api/checker/instances`** уже возвращает `{instances:[{id,name,kind}]}` (`api/xray_checker.py:306-309`)
  и уже фетчится страницей (`UsersStats.tsx:378-379`) — пикеру не нужен новый эндпоинт.
- **НЕ ПРОВЕРЕНО (утверждение об апстриме, не о нашем коде):** что `stableId` меняется при регенерации подписки.
  В нашем коде задокументирована только форма ответа `GET /api/v1/proxies -> [{stableId, …}]`
  (`services/xray_checker.py:16`); гарантий стабильности id мы нигде не проверяли и не пиним.
  Апстрим: https://github.com/kutovoys/xray-checker. Отсюда — страховка «хранить имя рядом с id» и группа
  «не найдено», а не попытка мигрировать id автоматически.
- **НЕ ПРОВЕРЕНО в рантайме:** что `ALTER TABLE servers ADD COLUMN hidden` корректно отрабатывает на уже
  существующей БД аккаунта (у нас нет живой инсталляции под рукой) — обязательный ручной шаг при внедрении Ф4:
  прогнать на копии `accounts/<id>/server_monitor.db`. Схема создаётся лениво, per-path, под локом
  (`server_monitor_store.py:54-87`) — миграцию класть внутрь того же `_ensure_schema`.
- **Уровень «сколько это кода»:** backend Ф1 — одна модель + два поля в роутах; Ф4 — колонка + одна функция
  стора + одно поле в PATCH + счётчики. Меньшего пути нет: альтернатива «спрятать набор в
  `WidgetInstance.settings`, чтобы вообще не трогать бэкенд» технически работает (поле свободное,
  `tests/test_stat_widgets.py:20`), но дублирует набор по инстансам и делает «источник истины» неопределённым —
  сознательно отвергнуто.
- **Порядок применения фильтра важен:** все срезы top-N (`UsersStats.tsx:128,244,323,343`) идут ПОСЛЕ фильтра,
  иначе скрытая нода продолжит «съедать» место в восьмёрке.
- **Изоляция бесплатна в обеих осях:** `stat_widgets.json` резолвится через `current_account`
  (`services/storage.py:142-149`), `server_monitor.db` — per-account (`server_monitor_store.py:38-46`).
  Единственный вариант, который сломал бы это свойство, — device-global localStorage без сервера; он отвергнут.

## Критерии готовности

- [ ] `GET/PUT /api/stats/users/widgets` отдают и принимают `hidden` (две оси, лимиты, обрезка имён);
      документ без `hidden` читается; изоляция между аккаунтами зелёная —
      `cd backend && python -m pytest` чисто.
- [ ] Стор шлёт `layout` + `hidden` **одним** PUT; ни один add/remove/resize/move не стирает набор скрытых
      (покрыто юнит-тестом на тело запроса); старый localStorage-формат (голый массив) мигрирует.
- [ ] Скрытие применяется в 5 виджетах (node-load, avg-per-node, migrations, stable-nodes, fast-nodes) ДО срезов
      top-N; «Топ пользователей» осознанно не участвует и это подписано в UI.
- [ ] `hidden.checker` изолирован по `checker_id`: один и тот же `stableId` на двух инстансах скрывается
      независимо; `cid === 'server-monitor'` — passthrough (подавление с бэкенда).
- [ ] Пикер показывает группу «не найдено» с сохранённым именем и «забыть»; набор переживает F5 и следует за
      аккаунтом (сервер + localStorage-зеркало).
- [ ] Дэшборд → «Доступность серверов»: deployed-сервер можно скрыть, он не возвращается после ре-синка
      `deploy_jobs`, не входит в счётчики/аптайм баннера, продолжает пробиться, возврат восстанавливает бары;
      `python -m pytest tests/test_server_monitor.py` зелёный.
- [ ] `npx --no-install tsc --noEmit`, `npm test`, `docker compose build frontend` — зелёные; тема через
      var-токены, CSP-self-contained, mobile ≤820px не сломан.
- [ ] CLAUDE.md §4d/§9b обновлены, в т.ч. исправлены два зафиксированных расхождения (`stats.router` →
      `user_stats.router`; энум из 8 типов против 6 во фронте).
