# Волна 5 · План G — Редактор виджетов статистики

> Конструктор дашборда на странице «Статистика → Пользователи»: добавить/удалить/переставить (drag)/
> изменить размер виджета, выбрать тип и метрику, персист раскладки и per-widget настроек **per-account**.
> Сейчас `frontend/src/components/stats/UsersStats.tsx` — 6 захардкоженных inline-SVG виджетов в статичной
> CSS-grid без layout-state, а `WidgetSettings.tsx` — презентационный gear-поповер без персиста.
> Затрагивает: **новый** backend-стор + API (`services/stat_widgets_store.py` **или** секция в `settings.json`,
> `api/user_stats.py` — расширить, роут `/api/stats/users/widgets`), рефактор `UsersStats.tsx` в реестр виджетов,
> **новый** `stats/WidgetGrid.tsx` (своя CSS-grid + нативный HTML5-DnD), расширение `WidgetSettings.tsx`.
> Переиспользует: паттерн Zustand+Immer+per-account-localStorage из `profiles/store/configStore.ts`, ключевые
> хелперы `auth/store.ts`, атомы `Card/State/Bar/WindowSelect/CheckerSelect` из `UsersStats.tsx`.

## Контекст (как есть)

- **`stats/UsersStats.tsx`** (390 строк) — 6 виджетов = 6 локальных функ-компонентов (`WNodeLoad`,
  `WAvgPerNode`, `WTopUsers`, `WMigrations`, `WStableNodes`, `WFastNodes`), каждый самодостаточный: свой
  `useState` окна/чекера + `useFetch`. Раскладка — **жёстко зашитая** сетка (строки 379–386):
  `<div className="grid grid-cols-1 lg:grid-cols-2" style={{display:"grid",gap:16}}>` с шестью компонентами,
  перечисленными вручную. **Нет массива-конфига, id, размеров, порядка в state** — всё статично.
- Виджеты a–d берут окно через `WindowSelect` (24ч/7д/30д → `hours` 24/168/720), эндпоинты
  `/api/stats/users/{node-load,top-users,migrations}?hours=`. Виджеты e–f берут `CheckerSelect` (список из
  `GET /api/checker/instances` + опция `server-monitor`), эндпоинт `_statusUrl(cid)` → `/api/checker/statuspage`
  или `/api/server-monitor/statuspage`.
- **`stats/WidgetSettings.tsx`** (37 строк) — чисто презентационный gear-поповер (`Settings2` тогглит `open`,
  fixed-overlay для закрытия по клику вне, абсолютный поповер). **Ничего не хранит** — рендерит `children`;
  само значение (окно/чекер) живёт в `useState` конкретного виджета. **Настройки НЕ персистятся** — каждый
  маунт стартует с дефолта (`useState(168)`/`useState("local")`).
- **`profiles/store/configStore.ts`** — эталон нужной инфраструктуры: Zustand+Immer store с **ручным
  per-account localStorage** (не zustand-persist middleware): `storageKey(id=getActiveId())` →
  `xray_profile_${id ?? 'none'}`, `persist(config)` в try/catch (quota/private mode), `hydrate()` на маунте,
  App keyed by `activeId` → смена аккаунта = ремаунт = перечитывание своего черновика.
- **`auth/store.ts`** — per-account key-хелперы (строки 100–109): `deployJobsKey`/`panelJobsKey`/`tabKey` +
  `getActiveId()`. Место для `widgetsKey(id)`.
- **Backend-стор** (`services/storage.py`) — единый паттерн `load_*/save_*(account_id=None)` над
  `DATA_DIR/accounts/<id>/<name>.json` (`_dir` резолвит `current_account` ContextVar; фон-вызовы передают
  явный `account_id`). Существуют пары для settings/templates/traffic_rules/subscriptions/domains/hosts/
  checkers/rules/testservers/certwarden/netbird.
- **Роутинг**: `api/user_stats.py` (`stats.router`, роуты `/api/stats/users/{node-load,top-users,migrations}`,
  clamp hours 1–720) уже под `require_account` в `main.py` (строка 101, `app.include_router(stats.router,
  dependencies=_auth)`). Таб `stats-users` → `<UsersStats/>` в `App.tsx` (строки 62, 209).
- **Зависимости** (`package.json`): есть `zustand ^5`, `immer ^11`, `motion`, `lucide-react`. **НЕТ**
  grid-drag-либы (`react-grid-layout`/`gridstack` не установлены).

## Развилки (закреплены)

- **Своя CSS-grid + нативный HTML5 DnD, БЕЗ новой либы.** `react-grid-layout` тянет свой CSS (ломает
  var-token-тему skin×mode + mobile bottom-sheet вёрстку `@media max-width:820px`) и плохо дружит с
  CSP-self-contained. Проект уже практикует ручной splice-реордер (`configStore.moveItem/reorderRules`), есть
  `motion` для плавности. Ресайз = тоггл ширины `w` 1↔2 (`grid-column: span N`), перестановка = `order`.
- **Персист — И на backend (per-account JSON), И зеркало в localStorage.** Сквозная идея 5 (расширять backend
  API) требует серверного стора → раскладка переживает смену устройства. localStorage — мгновенный кэш/оффлайн
  дефолт (как `deploy_jobs`), синхронизируется с сервером. Источник истины при конфликте — **сервер**
  (последняя запись выигрывает; без CRDT — в фоне не переспрашивать).
- **Хранилище — новый `stat_widgets.json` через `storage.load/save_stat_widgets`** (НЕ поле в `settings.json`:
  раскладка меняется часто и независимо от настроек; отдельный файл проще и изолированнее). Никаких секретов —
  Fernet-волт НЕ нужен.
- **Реестр виджетов — источник правды в коде** (`WIDGETS: Record<WidgetKind, WidgetDef>`); в сторе хранится
  только layout (ссылки на `kind` + позиция/размер + per-widget `settings`). Неизвестный `kind` из стора
  (после отката версии) — тихо отбрасывается при гидратации.
- **Миграция: пустой/отсутствующий layout → дефолтная раскладка из 6 текущих виджетов** в текущем порядке и
  ширинах (`WNodeLoad` — full-width `w:2`, остальные `w:1`). Не ломать существующих пользователей: первый заход
  = ровно нынешний вид.
- **Палитра виджетов = существующие 6 + новые из УЖЕ имеющихся данных** (без новых бэкенд-эндпоинтов данных):
  node-load, avg-per-node, top-users, migrations, stable-nodes (uptime), fast-nodes (latency) + производные
  (например «uptime-сводка» из statuspage, «speedtest-история» из `/api/speedtest/history` если тривиально).
  Дубли одного `kind` с разными настройками разрешены (у каждого экземпляра свой `instanceId`).

## Стратегия

Ф1 (backend: стор `stat_widgets` + API GET/PUT) → Ф2 (frontend: реестр виджетов + layout-стор + гидратация/
персист + миграция дефолтов) → Ф3 (frontend: сетка-редактор — DnD/ресайз/добавить/удалить + persist настроек).

---

### Ф1 — Backend: per-account стор раскладки + API → verify: pytest + py_compile

- **`services/storage.py`** — добавить пару `load_stat_widgets(account_id=None) -> dict` /
  `save_stat_widgets(data, account_id=None)` над `DATA_DIR/accounts/<id>/stat_widgets.json` (по образцу
  `load_settings`/`save_settings`; дефолт при отсутствии файла — `{}` → фронт подставит дефолтную раскладку).
- **`api/user_stats.py`** (тот же `stats.router`, уже под `require_account`):
  - `GET /api/stats/users/widgets` → `{layout: WidgetInstance[]}` (пусто → `{layout: []}`, фронт мигрирует).
  - `PUT /api/stats/users/widgets` (тело `{layout: WidgetInstance[]}`) → сохранить, вернуть сохранённое.
  - Pydantic-модель `WidgetInstance {instance_id: str, kind: str, w: int (Field ge=1 le=2), order: int,
    settings: dict}` — **валидировать `kind` по closed-enum** известных типов (неизвестный → 422; фронт-реестр
    и бэкенд-enum держать синхронными), `settings` — свободный dict (окно/чекер), но **без секретов** (в
    статистике их нет). Лимит длины `layout` (например ≤ 40) от разрастания.
- Аккаунт-изоляция бесплатна: `current_account` ContextVar через `require_account` (роут уже под `_auth`).
- verify: `backend/tests/test_user_stats.py` дополнить — GET-дефолт (пусто), PUT→GET round-trip, изоляция
  между аккаунтами, отказ на неизвестный `kind`/`w` вне диапазона. `python -m py_compile` изменённых файлов;
  `python -m pytest backend/`.

---

### Ф2 — Frontend: реестр виджетов + layout-стор + миграция → verify: tsc

- **Рефактор `stats/UsersStats.tsx` в реестр.** Вынести 6 виджет-компонентов так, чтобы каждый принимал
  `settings` (окно/чекер) **как props** (сегодня они держат своё `useState` — поднять в layout-стор). Создать
  `WIDGETS: Record<WidgetKind, {title, Icon, defaultW: 1|2, defaultSettings, SettingsControl, Component}>`.
  `WidgetKind = "node-load"|"avg-per-node"|"top-users"|"migrations"|"stable-nodes"|"fast-nodes"|…` (+ новые).
  Общие атомы `Card/State/Bar/WindowSelect/CheckerSelect/NodeLoadChart` остаются (переиспользуются реестром).
- **`stats/statWidgetsStore.ts`** (новый) — Zustand+Immer по образцу `configStore.ts`:
  - `WidgetInstance {instanceId, kind, w, order, settings}`. State `{layout: WidgetInstance[], dirty}`.
  - Методы: `hydrate()` (сервер → при ошибке localStorage → при пусто `DEFAULT_LAYOUT`), `add(kind)`,
    `remove(instanceId)`, `resize(instanceId, w)`, `move(fromOrder, toOrder)` (splice-реордер как
    `moveItem`), `setSettings(instanceId, patch)`. Каждый мутатор → `persist()`.
  - `persist()` — **дебаунс** записи: сразу `localStorage.setItem(widgetsKey(), …)` + отложенный
    `PUT /api/stats/users/widgets` (детач, ошибку глотаем — localStorage уже спас). Ключ
    `widgetsKey(id=getActiveId())` → `stat_widgets_${id ?? 'none'}` (добавить хелпер в `auth/store.ts` рядом с
    `deployJobsKey`).
  - `DEFAULT_LAYOUT` — 6 текущих виджетов в нынешнем порядке/ширинах (миграция: пустой сервер+localStorage).
  - Неизвестный `kind` из стора (после отката) — отфильтровать при гидратации (не падать).
- **App keyed by `activeId`** уже даёт per-account ремаунт → `hydrate()` в `useEffect` на маунте `UsersStats`
  перечитает раскладку активного аккаунта (как `Profiles.tsx`).
- verify: `npx --no-install tsc --noEmit`; юнит `UsersStats`/стора (миграция дефолтов, add/remove/resize/move,
  фильтр неизвестного kind) в мини-раннере фронта (как `Dashboard.test.mjs`) либо `*.test.tsx`.

---

### Ф3 — Frontend: сетка-редактор (DnD/ресайз/добавить/удалить) → verify: tsc + preview

- **`stats/WidgetGrid.tsx`** (новый) — рендерит `layout` (сортировка по `order`) в CSS-grid
  (`lg:grid-cols-2`; `w:2` → `gridColumn:"span 2"` на ≥lg, на mobile всё в 1 колонку — уже так). Каждый
  экземпляр = `WIDGETS[kind].Component` в обёртке с оверлеем-«режим редактирования».
- **Тоггл «Редактировать» в шапке страницы** (`ni-pagehead-actions`). В режиме редактирования на каждом
  виджете: drag-handle (нативный HTML5 `draggable` + `onDragStart/onDragOver/onDrop` → `move`), кнопки
  «ширина 1/2» (→ `resize`), «удалить» (двухкликовый confirm, как в проекте). Плавность — `motion` (layout-
  анимация перестановки), но **без внешних ассетов** (CSP).
- **Палитра «+ Добавить виджет»** — поповер/модалка со списком `WIDGETS` (title+Icon) → `add(kind)` (даёт
  `instanceId`, ставит в конец). Дубли разрешены.
- **Настройки экземпляра** — расширить `WidgetSettings.tsx`: помимо `children` (окно/чекер-контрол) принимать
  колбэк на изменение, писать в `store.setSettings(instanceId, …)` (персист). Значение окна/чекера читается из
  `instance.settings`, а не из локального `useState` виджета.
- Вне режима редактирования — вид идентичен нынешнему (виджеты + их gear-настройки), драг/ресайз/удаление
  скрыты.
- verify: `tsc`; preview — добавить/удалить/переставить/сменить размер, сменить окно у экземпляра, F5
  (localStorage), смена аккаунта (ремаунт → своя раскладка), пусто → дефолтные 6. Проверить mobile
  (≤820px) — 1 колонка, редактор-контролы доступны, bottom-sheet не ломается.

## Критерии готовности плана G

- `GET/PUT /api/stats/users/widgets` под `require_account`, per-account `stat_widgets.json`, closed-enum `kind`,
  round-trip и изоляция — зелёные в `test_user_stats.py`; `py_compile`/`pytest` чисто.
- Реестр виджетов + layout-стор: добавить/удалить/переставить(drag)/размер, per-widget настройки (окно/чекер)
  персистятся per-account (сервер + localStorage-кэш), пустой стор мигрирует к текущим 6 виджетам без регресса.
- Своя CSS-grid + нативный DnD (без новых npm-зависимостей), тема через var-токены, CSP-self-contained,
  mobile не ломается. `tsc` + preview + ручной smoke (в т.ч. смена аккаунта и F5).
- CLAUDE.md обновлён (§4d/§9 — новый стор, роут, реестр виджетов, миграция дефолтов) при реализации.
