# Волна 6 · План F — Производительность и контраст (точечно, по измерениям)

> Два независимых дефекта, оба локализованы разведкой и оба чинятся без редизайна.
> (1) **Лаги/прокрутка** — не цепочка overflow (она здорова), а объём рендера: реплей логов деплоя приходит
> ОДНОЙ WS-рамкой на строку (до 2000 на карточку) и каждая строка вызывает `setLogs(l => [...l, line])`;
> плюс дэшборд каждые 10 с переписывает `style`+`title` у всех uptime-баров (индексные ключи, ноль мемоизации).
> (2) **Светлый текст на светлом фоне** — два системных источника: токен `--t-faint` (2.4–3.5:1 в 4 из 5
> палитр, ≈249 упоминаний) и захардкоженный `#fff` поверх пользовательского акцента в apple-скине (белое на
> `lime` = **1.21:1**). Затрагивает: `backend/app/api/ws.py`, `frontend/src/hooks/useTaskStream.ts`,
> `components/{DeployCard,Dashboard,Sidebar,DeployDashboard}.tsx`, `settings/AiChat.tsx`, `stats/UsersStats.tsx`,
> `auth/{AuthScreen,AccountMenu}.tsx`, `infra/ui.tsx`, `index.css`, `theme/tweaks.ts`, `App.tsx`, `vite.config.ts`.
> **Рамка: сначала измеряем, потом чиним.** Ф1 ставит два харнеса, каждая следующая фаза обязана показать
> «было/стало» числом — «стало плавнее» не принимается.

## Контекст (как есть)

**Перф — цепочка overflow здорова, проблема в объёме рендера:**
- `body{overflow:hidden}` (`index.css:47`), каждая вкладка держит свой `flex-1 overflow-y-auto`
  (`DeployDashboard.tsx:126`, `infra/ui.tsx:24`, `Dashboard.tsx:105`, `UsersStats.tsx:388`, `Settings.tsx:809`).
  **Единственное исключение** — `settings/AiChat.tsx:130`: корень `<div className="card card-p flex flex-col gap-4">`
  смонтирован прямо в `<Screen>` (`App.tsx:240`, стиль `flex:1;min-height:0`), скроллера нет ни на одном
  предке → конфиг-форма и `PromptPresets` над чатом обрезаются и недостижимы.
- **Реплей логов (доминанта).** `Task.logs = deque(maxlen=2000)` (`task_store.py:37`), `subscribe()` кладёт в
  очередь КАЖДУЮ буферизованную строку (`task_store.py:64-74`; `SharedTaskStore` — тот же контракт,
  `shared_task_store.py:232-263`, и `_tail` отдаёт до 500 строк за тик, `:275`). `ws.py:47-50` шлёт по
  **одной WS-рамке на строку**. Клиент: `DeployCard.tsx:97` `setLogs(l => [...l, line])`, поток открывается
  **безусловно** (`DeployCard.tsx:105`), в том числе для карточек с уже проставленным `job.finalStatus`.
  Терминал при этом рендерится только в раскрытой карточке (`DeployCard.tsx:361`, `:680`) — то есть 2000
  ререндеров происходят ради данных, которых на экране нет.
- **Дэшборд.** Бары держат **индексные ключи по скользящему окну** (`Dashboard.tsx:731-734`): при каждом
  тике элемент `i` — уже другой сэмпл, поэтому переписываются и `style.background`, и `title`. Ни `NodeRow`
  (`Dashboard.tsx:707`), ни `CountryGroup` (`:172`) не мемоизированы, `data` — новый объект на каждый поллинг.
  Три независимых таймера: `setInterval(…, 10_000)` в XrayUptime (`:230`) и в ServerUptime (`:400`),
  `setInterval(load, 15_000)` в SubscriptionSelector (`:602`); каждый `load()` — два фетча (`:218-221`).
  Проверки `document.visibilityState` нет нигде.
- **Sidebar.** `NavBtn` объявлен ВНУТРИ тела `Sidebar` (`Sidebar.tsx:81`) и используется как `<NavBtn/>`
  (`:123,127,131,135,139,145`) → новая идентичность компонента на каждый рендер → React пересоздаёт все ~30
  кнопок с lucide-иконками. `Sidebar` не мемоизирован и рендерится прямо из `App` (`App.tsx:198`), а `App`
  перерисовывается на каждую строку SSL-лога (`App.tsx:162` `setCertLogs(l => [...l, line])`).
- **`NodeLoadChart`.** `allTs = Array.from(new Set(shown.flatMap(...))).sort(...)` считается на КАЖДЫЙ рендер
  без `useMemo` (`UsersStats.tsx:142`), `valAt` — линейный проход по точкам (`:144-148`), а `onMouseMove`
  (`:157-165`) после `getBoundingClientRect()` линейно сканирует `allTs`.
- **`AiChat`.** `useEffect(… scrollRef.current.scrollHeight …, [msgs])` (`AiChat.tsx:47`) — форс-лейаут на
  каждый стрим-дельта-токен.
- **`.overlay`** — полноэкранный `backdrop-filter:blur(3px)` (`index.css:180`), используется 9 модалками;
  под ним продолжают тикать поллы дэшборда и секундомер `StepProgress` (`StepProgress.tsx:55`).
- **Бандл.** `App.tsx` статически импортирует все 30+ вкладок, `React.lazy`/динамического `import()` в `src/`
  нет, `vite.config.ts` (13 строк) не объявляет `manualChunks`. Измерено на закоммиченном `dist` (**от 11 июля,
  до Волны 6 — перемерить**): `dist/assets/index-BDAs6WmJ.js` = **1 465 182 Б**,
  `index-9vPxm6zk.css` = **468 743 Б**, 142 SVG-флага, каталог 5.7 МБ. `main.tsx:5` безусловно тянет
  `flag-icons/css/flag-icons.min.css`.
- `grep -rn "overscroll" frontend/src` — **пусто**: ни один вложенный список (дропдаун `CountrySelect.tsx:180`
  внутри модалки `DeployDashboard.tsx:233` внутри страничного скроллера) не имеет `overscroll-behavior:contain`.

**Контраст (числа посчитаны по WCAG 2.1, мной, из значений в `index.css`):**
- `--t-faint`: light/apple-light `#A6A6AE` (`index.css:230`, `:281`) на `--bg2:#FFF` = **2.42:1**, на
  `--bg3:#E7E7EC` ≈ **1.98:1**; apple-dark `#6A6A70` (`:300`) на `--bg2:#2C2C2E` = **2.59:1**; console-dark
  `#5A6780` (`:16`) на `--bg2:#121826` ≈ **3.11:1**; neon `#586A88` (`:352`) на `--bg2:#0D1018` ≈ **3.47:1**.
  То есть провал в 4 палитрах из 5 (только neon/console-dark дотягивают до AA-large 3:1). Используется
  ≈**249 раз в 49 файлах**, в т.ч. `.input::placeholder` (`index.css:100`), `.faint` (`:65`), ВЕСЬ список
  ещё-не-начатых шагов деплоя (`StepProgress.tsx:80`), подписи осей графика (`UsersStats.tsx:170,176`).
- **apple-скин — дефолтный** (`tweaks.ts:121`) и хардкодит `--accent-ink:#fff; --primary-ink:#fff;
  --brand-ink:#fff; --nav-active-fg:#fff` (`index.css:251-252`), тогда как `--accent` выбирает пользователь
  из `ACCENTS` (`tweaks.ts:24-33`). Белым по акценту: `lime #B4FF3A` = **1.21:1**, `amber #F0B054` = **1.90:1**,
  `green #3ECF8E` ≈ 2.0:1, `cyan` ≈ 2.1:1, `blue #4C8DFF` = **3.20:1** (только AA-large). Бьёт по
  `.btn-primary` (`index.css:75`), `.navitem.active` (`:148`) и 27 местам `bg-[var(--accent)]
  text-[var(--primary-ink)]`.
- **Расхождение код↔CLAUDE.md:** `applyAccent` пишет `--accent-ink` ИНЛАЙНОМ на `:root`
  (`tweaks.ts:45`) — инлайн бьёт любой авторский стиль, поэтому `--accent-ink:#fff` из `index.css:251`
  **никогда не применяется**. Прав КОД; §2a CLAUDE.md («apple re-points these tokens») нужно поправить.
  Иронично: именно эта случайная перебивка спасает `.switch.on::after`/`.ck.on`/`.seg.accent button.on`, а
  ломаются ровно те два токена, которые `applyAccent` НЕ пишет (`--primary-ink`, `--nav-active-fg`).
- **11 самодельных тумблеров** с белым бегунком на треке `var(--bg3)` (в светлой теме белое на `#E7E7EC` =
  **1.22:1**, выключенное состояние не видно): `DeployForm.tsx:270`, `Settings.tsx:707,714`, `CertsForm.tsx:183`,
  `Templates.tsx:133`, `RuleBuilder.tsx:674`, `rw/Migration.tsx:194`, `rw/PanelDeployForm.tsx:272`,
  `settings/AiChat.tsx:189`, `settings/McpTab.tsx:156,165`. Все обходят готовый `.switch` (`index.css:164-170`),
  у которого бегунок `var(--t-low)`. (Разведка говорила «12 мест» — по grep их **11**.)
- **Тёмный остров `AuthScreen`**: захардкоженные `bg-gray-900/80 … text-gray-100 placeholder:text-gray-700`
  (`AuthScreen.tsx:9-11`), `bg-blue-600 … text-white` (`:13-15`). На отдельном экране логина это безвредно, но
  `AccountMenu.tsx:83` рендерит тот же компонент как оверлей «Добавить аккаунт» ПОВЕРХ живой светлой темы, а
  подложка оверлея — `var(--bg0)` (`AuthScreen.tsx:86`) = `#ECECEF`.
- `DeployDashboard.tsx:132` — литеральный `text-white` заголовок на светлом фоне (≈1.05:1) + `text-gray-500`
  (`:133`) + `bg-blue-600 … text-white` кнопка (`:146`), мимо акцент-токена.
- Мелочи: инфра-модалка затемняет фон `bg-black/75` (`infra/ui.tsx:60`) вместо `var(--overlay)`
  (`rgba(0,0,0,.30)`/`.26`, `index.css:234,285`) — в ~2.5 раза темнее всех прочих модалок;
  `MihomoEditor.tsx:20` красит iframe в `var(--bg)` — **такого токена в `index.css` нет** (есть `--bg0…--bg3`),
  декларация невалидна.

**Инструменты, которые уже есть:** `frontend/tests/e2e/theme-shots.mjs` (Playwright-матрица skin×mode +
экспортируемый `apiStub`), `phase-render-smoke.mjs` (сидит device-аккаунт в `localStorage`, стабит `**/api/**`,
ловит `pageerror`), `mobile-smoke.mjs`, `npm test` = vitest, `npx --no-install tsc --noEmit`,
`cd backend && python -m pytest`. Playwright — `@playwright/test ^1.61.1`.

## Развилки (закреплены)

- **Только по измерениям, без широкого рефакторинга.** Оболочку (`App`/`Screen`/сайдбар-раскладку) и палитру
  целиком НЕ переделываем. Каждая фаза = точечная правка + число «было/стало».
- **Лог-шторм чиним на ОБОИХ концах, но дёшево:** (а) `ws.py` склеивает то, что уже лежит в очереди, в одну
  рамку `{"type":"logs","lines":[…]}`; (б) `DeployCard` открывает сокет только для незавершённой карточки —
  либо когда пользователь раскрыл детали. Склейка живёт в `ws.py`, а не в сторах, — тогда она бесплатно
  работает и для `TaskStore`, и для `SharedTaskStore` (у них одинаковый контракт очереди).
  **Совместимость:** одиночные живые строки продолжают ехать как `{"type":"log"}`; `logs` появляется только
  когда в очереди реально >1 элемент → закэшированный старый SPA потеряет максимум реплей, а не поток.
- **Дэшборд: сначала мемоизация, не виртуализация.** `React.memo` на `NodeRow`/`CountryGroup` + ключ бара по
  `b.ts` вместо индекса. Виртуализация не нужна до ~150 узлов (и тянет либу — запрещено без нужды).
  Поллы паузятся по `document.visibilityState` (одно условие); паузу «когда открыта модалка» НЕ делаем —
  это лишняя проводка состояния.
- **Контраст акцента: `applyAccent` считает чернила по светимости.** Пишем инлайном `--primary-ink`,
  `--nav-active-fg`, `--brand-ink` рядом с уже пишущимся `--accent-ink` (порог: относительная светимость
  акцента ≥ ~0.35 → тёмные чернила `a.ink`, иначе `#fff`), а литералы из `index.css:251-252` удаляем.
  Так apple-вид сохраняется для blue/violet/magenta и автоматически переворачивается для lime/amber/green/cyan.
  Альтернативу «выкинуть светлые акценты из `ACCENTS`» отклоняем — теряет фичу, а не чинит причину.
- **`--t-faint` перетюниваем ПО ТОКЕНУ, а не по 249 вызовам.** Цель: **≥4.5:1 на `--bg2`** (основная
  поверхность карточек) и **≥3:1 на `--bg3`**. Кандидаты (посчитаны, финал подтверждает харнес):
  light/apple-light `#A6A6AE`→`#74747E` (4.63:1 на белом), apple-dark `#6A6A70`→`#949499` (≈4.6:1 на `#2C2C2E`),
  console-dark `#5A6780`→`#7C8AA4` (≈5.1:1), neon `#586A88`→ проверить (нужно ≈`#7E92B4`).
  Отдельно правим ОДНО структурное злоупотребление токеном: список шагов в `StepProgress.tsx:80` — это
  основной контент, а не плейсхолдер → `var(--t-low)`.
- **Прокрутка: `overscroll-behavior:contain` на вложенные скроллеры** (дропдаун `CountrySelect`, тела модалок)
  — 1 строка CSS на класс, без JS.
- **Код-сплит — узко.** `React.lazy` ровно на 4 тяжёлых листа (Profiles/CodeMirror, HostingsMap/world-atlas,
  MihomoEditor, TerminalOutput/xterm). **Флаги НЕ сабсетим вручную** (в «Хостингах» код страны вводит
  пользователь — ручной список сломает их); если после lazy CSS всё ещё доминирует — переносим импорт
  `flag-icons` в динамический `ensureFlagCss()` внутри трёх потребителей (`CountrySelect`, `HostingsCatalog`,
  `HostingsMap`). Реструктуризацию роутинга `App.tsx` НЕ трогаем.
- **`.overlay` blur — по измерению.** Сначала паузим поллы (Ф3): если кадр с открытой модалкой всё ещё дорогой
  по харнесу — просто убираем `backdrop-filter` из `.overlay` (косметика дешевле кадров). Глассморфизм
  топбара/сайдбара НЕ трогаем: это flex-сиблинги, под ними ничего не скроллится.
- **Порог «готово» по контрасту — AA 4.5:1 для текста <18px** (3:1 для крупного/иконок). Композитные
  полупрозрачные поверхности (`--raised`, `bg-gray-900/40`) токен-тестом не считаем — они закрываются
  скриншот-матрицей и удалением хардкода.
- **DEFAULT на случай сомнений:** если измерение не показывает выигрыша ≥20% по метрике фазы — правку
  откатываем и пишем это в отчёт фазы; не тащим «оптимизацию ради оптимизации».
- **Вне объёма (в бэклог «later», по решению пользователя):** удаление мёртвого theme/motion-кода
  (`.ni-skeleton`/`Skeleton`/`Stagger`/`StaggerItem`/`AnimatedNumber`/`.toast`/`.term`/`.bar-cell`) и мёртвого
  rail-режима сайдбара (`Sidebar.tsx:75-76` — пропсы `collapsed`/`onToggle` не используются). Ни то, ни другое
  не является источником текущих лагов.

## Стратегия

Ф1 (измерить и зафиксировать базу) → Ф2 (лог-шторм: WS-склейка + клиент + Sidebar) → Ф3 (дэшборд: мемо/ключи/
пауза) → Ф4 (контраст системный: `--t-faint` + чернила акцента) → Ф5 (контраст локальный: тумблеры, тёмные
острова, мелкие баги) → Ф6 (прокрутка и точечные остатки) → Ф7 (вес бандла).

---

### Ф1 — Измерительная база: два харнеса + зафиксированные числа → verify: `node tests/e2e/perf-probe.mjs` + `npm test`

- **`frontend/tests/e2e/perf-probe.mjs`** (новый, коммитим) — Playwright/chromium, переиспользует `apiStub` из
  `theme-shots.mjs` и сид-аккаунт из `phase-render-smoke.mjs:49-57`. В `addInitScript`: `PerformanceObserver`
  на `longtask` → `window.__lt`, счётчик `window.__wsCount`, и **подмена `window.WebSocket` фейком**, который
  после `open` шлёт 1 рамку `status` + N=2000 рамок `log` отдельными задачами (`setTimeout(...,0)`), как
  сегодня делает сервер. (Playwright ≥1.48 умеет `routeWebSocket`, но **НЕ ПРОВЕРЕНО** на нашей версии —
  фейк в init-скрипте детерминированнее и от версии не зависит.)
  Сценарии и метрики:
  - **A «деплой-лог»**: сидим `deploy_jobs_<acc>` тремя карточками (форма — как в `DeployDashboard`), открываем
    вкладку `deploy`. Метрики: `wsInstances`, `framesDelivered`, `longTaskTotalMs`, `longTaskMaxMs`,
    `msToSettled` (от первой рамки до отсутствия long-task 500 мс).
  - **B «дэшборд»**: стаб `/api/checker/statuspage` на 40 узлов × 90 бар; `MutationObserver`
    (`attributes:true, subtree:true`) считает атрибутные мутации за 25 с (≥2 поллинг-тика) → `attrWrites`;
    затем цикл `page.mouse.wheel` с семплированием rAF → `jankFrames` (кадры >32 мс).
  - **C «бандл»** (флаг `--bundle`): размеры `dist/assets/index-*.js|css` и число ассетов.
  Вывод — таблица в stdout + JSON в `tests/e2e/shots/perf/<label>.json`; режим `--compare a.json b.json`
  печатает две колонки и дельту. **База коммитится как `shots/perf/baseline.json`.**
- **`frontend/src/theme/contrast.test.ts`** (новый, vitest) — читает `index.css` через `node:fs`, парсит
  палитровые блоки (`:root`, `[data-theme="light"]`, apple-light, apple-dark, neon) и импортирует `ACCENTS`
  из `./tweaks`; считает WCAG-коэффициенты для пар `--t-{faint,low,mid,hi}` × `--bg{1,2,3}` и
  «белое/`a.ink` на каждом акценте». Печатает таблицу и **ассертит порог 4.5:1, кроме перечисленных в
  коммитимом `KNOWN_FAILURES`** (текущие провалы с их измеренными числами). Тест зелёный сегодня (ловит НОВЫЕ
  регрессии), а Ф4/Ф5 вычёркивают строки из списка; «список пуст» = критерий готовности.
- Никаких новых npm-зависимостей (Playwright и vitest уже есть).
- → verify: `cd frontend && npx vite --host 127.0.0.1` + `node tests/e2e/perf-probe.mjs http://127.0.0.1:5173`
  печатает непустые метрики и пишет `baseline.json`; `npm test` зелёный; числа базы выписаны в коммит-сообщение.

---

### Ф2 — Лог-шторм: склейка на транспорте + батч на клиенте + Sidebar → verify: perf-probe A (было/стало) + pytest

- **`backend/app/api/ws.py`** — после `await queue.get()` жадно дочерпывать `queue.get_nowait()` (кап,
  напр. 500 элементов), накапливая ПОДРЯД идущие `("log", …)`; на границе (`step`/`done`/пусто) сначала
  флашить накопленное — одной рамкой `{"type":"logs","lines":[…]}` при len>1 либо привычной `{"type":"log"}`
  при len==1, затем слать саму границу. Порядок событий сохраняется побайтово; хартбит и `break` на `done`
  не трогаем. Сторы (`task_store.py`, `shared_task_store.py`) **не меняются**.
- **`frontend/src/hooks/useTaskStream.ts`** — новый опциональный `onLogs?: (lines: string[]) => void`;
  `case "logs": onLogs ? onLogs(msg.lines) : (msg.lines as string[]).forEach(onLog)` (фолбэк оставляет
  остальные 9 потребителей нетронутыми).
- **`frontend/src/components/DeployCard.tsx`** — `addLogs = useCallback((ls: string[]) => setLogs(l =>
  l.concat(ls)), [])` (одно копирование вместо N), и подписка только когда она нужна:
  `useTaskStream({ taskId: (!job.finalStatus || showDetail) ? job.taskId : null, … })` — хук уже корректно
  обрабатывает `taskId: null` (`useTaskStream.ts:27`). Завершённая свёрнутая карточка сокет не открывает;
  при раскрытии деталей поток подключается и отдаёт реплей уже склеенным.
- **`frontend/src/App.tsx`** — то же `onLogs` для `certLogs` (`App.tsx:162`).
- **`frontend/src/components/Sidebar.tsx`** — поднять `NavBtn` из тела `Sidebar` на модуль (пропсы
  `item/active/onClick`), сам `Sidebar` завернуть в `React.memo` (пропсы уже стабильны: `goTab` —
  `useCallback` в `App`; если нет — обернуть).
- → verify: `python -m pytest` + **новый `backend/tests/test_ws_logs.py`** (`TestClient.websocket_connect`:
  задача с 50 предзаписанными строками → приходит ОДНА рамка `logs` со всеми 50 в исходном порядке; живая
  строка после подписки приходит как `log`; порядок `logs`→`status`→`done` сохранён; те же ассерты при
  `TASK_STORE=shared`). Фронт: `perf-probe` сценарий A — ожидаем `framesDelivered` 2000→«десятки»,
  `longTaskTotalMs` в разы меньше, `wsInstances` 3→0 для завершённых карточек; `npm test`; `tsc --noEmit`.

---

### Ф3 — Дэшборд: мемоизация, стабильные ключи, пауза фоновых поллов → verify: perf-probe B (attrWrites/jank)

- **`frontend/src/components/Dashboard.tsx`** — `NodeRow` (`:707`) и `CountryGroup` (`:172`) в `React.memo`;
  ключ бара `key={b.ts}` вместо `key={i}` (`:731`); паддинг-слоты (`:728-730`) оставить с их `p${i}` (они
  однородны). Функции-помощники, попадающие в пропсы, — через `useCallback`.
- Поллы: в трёх местах (`:230`, `:400`, `:602`) не запускать тик, когда `document.visibilityState === "hidden"`
  (проверка внутри колбэка + `visibilitychange`-слушатель, который дёргает `load` один раз при возврате).
  Общий крошечный хелпер вместо трёх копий, если получится без новых абстракций.
- → verify: `perf-probe` сценарий B: `attrWrites` за 25 с должны упасть с «тысяч» до близких к числу реально
  изменившихся баров; `jankFrames` при прокрутке — меньше базы; фон-вкладка (`page.bringToFront()` на другую
  вкладку) даёт 0 сетевых запросов к `/statuspage`. Плюс `npm test`, `tsc --noEmit`, и глазами — что бары
  по-прежнему сдвигаются и тултип-время корректно.

---

### Ф4 — Контраст, системный слой: `--t-faint` + чернила акцента → verify: contrast.test.ts (KNOWN_FAILURES ↓)

- **`frontend/src/index.css`** — перетюнить `--t-faint` в четырёх палитрах (`:16`, `:230`, `:281`, `:300`,
  `:352`) до порога «≥4.5:1 на `--bg2`, ≥3:1 на `--bg3`»; точные хексы подтверждает `contrast.test.ts`
  (кандидаты — в «Развилках»). Удалить `--primary-ink:#fff; --brand-ink:#fff` и `--nav-active-fg:#fff`
  из `:root[data-skin="apple"]` (`index.css:251-252`); `--accent-ink:#fff` там же тоже удалить как мёртвый
  (его всё равно перебивает инлайн `applyAccent`).
- **`frontend/src/theme/tweaks.ts`** — в `applyAccent` (`:40-51`) посчитать относительную светимость
  `a.base` (WCAG-формула, ~8 строк) и записать инлайном `--primary-ink`, `--nav-active-fg`, `--brand-ink`:
  светимость ≥ ~0.35 → `a.ink` (тёмные чернила), иначе `#fff`. Порог подобрать так, чтобы blue/violet/magenta
  остались с белыми чернилами, а lime/amber/green/cyan получили тёмные (проверяется тестом).
- **`frontend/src/components/StepProgress.tsx:80`** — `var(--t-faint)` → `var(--t-low)` для ещё не начатых
  шагов (это основной контент карточки деплоя, а не плейсхолдер).
- → verify: `npm test` — `contrast.test.ts` без соответствующих строк в `KNOWN_FAILURES` (было: t-faint 2.42/
  2.59/3.11/1.98; белое на lime 1.21, amber 1.90, blue 3.20 → стало ≥4.5 для текста); скриншот-матрица
  `node tests/e2e/theme-shots.mjs` — apple/console × light/dark без визуальных регрессий; глазами: primary-кнопка
  и активный пункт сайдбара читаемы на КАЖДОМ из 7 акцентов (по одному скриншоту на акцент достаточно).

---

### Ф5 — Контраст, локальные очаги: тумблеры, тёмные острова, мелкие баги → verify: grep-счётчики + матрица

- **11 самодельных тумблеров → компонент `.switch`** (`index.css:164-170`): `DeployForm.tsx:269-270`,
  `Settings.tsx:707,714`, `CertsForm.tsx:183`, `Templates.tsx:133`, `RuleBuilder.tsx:674`, `rw/Migration.tsx:194`,
  `rw/PanelDeployForm.tsx:272`, `settings/AiChat.tsx:189`, `settings/McpTab.tsx:156,165`. Если разметка в них
  расходится — завести один локальный `Switch`-атом (`components/common/Switch.tsx`) и переиспользовать; новых
  абстракций сверх этого не вводить.
- **`auth/AuthScreen.tsx`** — перевести 46 палитровых Tailwind-классов на токены (`inputCls` → `.input`,
  `btnPrimary` → `.btn .btn-primary`, карточки → `.card`, заголовки → `text-[var(--t-hi)]`, подписи →
  `text-[var(--t-low)]`). Экран логина при этом остаётся тёмным по умолчанию (на нём `:root` без `data-theme`),
  а оверлей «Добавить аккаунт» (`AccountMenu.tsx:83`) наконец совпадает с активной темой.
- **`components/DeployDashboard.tsx`** — `text-white` (`:132`) → `text-[var(--t-hi)]`, `text-gray-500` (`:133`)
  → `text-[var(--t-low)]`, кнопка `bg-blue-600 … text-white` (`:146`) → `.btn .btn-primary`.
- **Мелочи:** `infra/ui.tsx:60` `bg-black/75` → `background: var(--overlay)`; `infra/Toast.tsx:46`
  `animate-[fadeIn_.15s]` → существующий `ni-fadeIn` (`index.css:178`) либо убрать; `MihomoEditor.tsx:20`
  `var(--bg)` → `var(--bg0)`.
- → verify: `grep -rn "bg-white rounded-full" frontend/src` → **0** (было 11);
  `grep -rnE "text-white|bg-gray-|text-gray-|bg-blue-600" frontend/src/auth frontend/src/components/DeployDashboard.tsx`
  → 0 (было 46+3); `npm test` (+ ассерт этих счётчиков можно добавить в `contrast.test.ts` как дешёвый лint);
  `node tests/e2e/theme-shots.mjs` + отдельный скриншот оверлея «Добавить аккаунт» в apple-light.

---

### Ф6 — Прокрутка и точечные остатки → verify: наблюдаемые проверки в perf-probe

- **`settings/AiChat.tsx`** — обернуть корень (`:130`) в `<div className="flex-1 overflow-y-auto"><div
  className="ni-pagebody max-w-3xl mx-auto px-6 py-6">…`, как во всех остальных вкладках. Прокрутку лога
  (`:47`) перевести на `requestAnimationFrame` и выполнять только если пользователь у нижнего края
  (`scrollTop + clientHeight >= scrollHeight - 40`), чтобы не дёргать лейаут на каждый токен.
- **`index.css`** — `overscroll-behavior:contain` на вложенные скроллеры: `.modal`-тело и дропдаун
  `CountrySelect` (добавить классу дропдауна имя, если его нет). Глобально на все `overflow-y-auto` НЕ вешаем.
- **`stats/UsersStats.tsx`** — `allTs` (`:142`) и `vMax/tMin/tMax` (`:132-137`) в `useMemo` по `shown`;
  `valAt` — заменить линейный проход бинарным поиском по уже отсортированному `allTs` **или** мемоизировать
  индекс; `onMouseMove` (`:157`) — троттлинг через rAF (одно вычисление на кадр).
- **`.overlay`** (`index.css:180`) — **по измерению**: если после Ф3 кадр с открытой модалкой всё ещё дороже
  базы, убрать `backdrop-filter:blur(3px)` (оставив `background: var(--overlay)`).
- → verify: в `perf-probe` добавить проверки-наблюдения: (а) на вкладке «Ассистент» `scrollHeight >
  clientHeight` у страничного скроллера и прокрутка доезжает до формы конфига (было: контент недостижим);
  (б) колесо в конце списка `CountrySelect` не меняет `scrollTop` родителя (было: меняет); (в) 50 синтетических
  `mousemove` над графиком нагрузки — `longTaskTotalMs` меньше базы. `npm test`, `tsc --noEmit`.

---

### Ф7 — Вес бандла: 4 ленивых листа (+ флаги по измерению) → verify: размеры `dist/assets` до/после

- **`frontend/src/App.tsx`** — `React.lazy(() => import(...))` для `Profiles`, `HostingsMap` (или страницы
  `hostings-map` целиком), `MihomoEditor`, `TerminalOutput` (последний импортируется из `DeployCard`/
  `PanelWidget` — лениво грузить там же), каждый в `<Suspense fallback={…}>` (простой текст/спиннер, без
  новых компонентов).
- Если после этого CSS всё ещё доминирует — `ensureFlagCss()` (динамический `import("flag-icons/css/…")`
  внутри `CountrySelect`/`HostingsCatalog`/`HostingsMap`) вместо глобального импорта в `main.tsx:5`.
  Ручной сабсет флагов — **не делаем** (код страны в «Хостингах» вводит пользователь).
- `vite.config.ts` не трогаем, пока измерение не покажет, что `manualChunks` даёт что-то сверх lazy.
- → verify: `npm run build` до и после, сравнить `ls -l dist/assets/index-*.js index-*.css` (база:
  **1 465 182 Б JS / 468 743 Б CSS**, но **перемерить** — закоммиченный dist от 11 июля);
  `node tests/e2e/phase-render-smoke.mjs` — все экраны рендерятся без `pageerror` (ленивые чанки грузятся);
  `docker compose build frontend` проходит.

## РАЗВЕДКА (факты)

- **Транспорт логов:** `task_store.py:37` (`deque(maxlen=2000)`), `task_store.py:64-74` (`subscribe` кладёт
  каждую строку), `shared_task_store.py:232-263` + `:270-308` (тот же контракт + tail до 500 строк/тик),
  `ws.py:36-71` (одна рамка на элемент очереди, хартбит 25 с). ⇒ склейка в `ws.py` покрывает обе реализации.
- **Клиент логов:** `DeployCard.tsx:87-105` (`setLogs(l => [...l, line])`, безусловная подписка),
  `useTaskStream.ts:26-73` (эффект по `[taskId]`, ранний выход на `null` — `:27`), `App.tsx:162,170`
  (тот же паттерн для SSL-лога), терминал в карточке — только под `showDetail` (`DeployCard.tsx:361,680`).
  Потребителей `useTaskStream` всего 11 (`grep`), поэтому опциональный `onLogs` ничего не ломает.
- **Дэшборд:** `Dashboard.tsx:216-232` (`load` = 2 фетча, `setInterval` 10 с), `:400`, `:602`;
  `:707-751` `NodeRow` без `memo`; `:731-734` индексные ключи + `style`+`title` на каждом баре;
  `:172` `CountryGroup` без `memo`.
- **Sidebar:** `Sidebar.tsx:80-93` (`NavBtn` внутри рендера), использование `:123-145`, монтаж из
  `App.tsx:198`; пропсы `collapsed`/`onToggle` объявлены (`Sidebar.tsx:75-76`), но в теле не используются —
  мёртвый rail-режим (в бэклог).
- **Скролл:** `index.css:44-48` (`body{overflow:hidden}`), скроллеры вкладок — `DeployDashboard.tsx:126`,
  `infra/ui.tsx:24`, `Dashboard.tsx:105`, `UsersStats.tsx:388`, `Settings.tsx:809`; исключение — `AiChat.tsx:130`
  внутри `App.tsx:240`. `overscroll-behavior` не встречается в `frontend/src` ни разу.
- **Бандл (измерено на закоммиченном `dist`, дата 11 июля — НЕ актуальная сборка):**
  `index-BDAs6WmJ.js` 1 465 182 Б, `index-9vPxm6zk.css` 468 743 Б, 142 SVG, каталог 5.7 МБ;
  `vite.config.ts` — 13 строк, ни `build`, ни `manualChunks`; `main.tsx:5` — глобальный `flag-icons`.
- **Контраст (посчитано мной по WCAG 2.1 из значений `index.css`):** `#A6A6AE`/`#FFFFFF` = 2.42:1;
  `#A6A6AE`/`#E7E7EC` ≈ 1.98:1; `#6A6A70`/`#2C2C2E` = 2.59:1; `#5A6780`/`#121826` ≈ 3.11:1;
  `#586A88`/`#0D1018` ≈ 3.47:1 (**neon разведкой не разбирался — посчитано здесь**);
  `#fff` на `#B4FF3A` = 1.21:1, на `#F0B054` = 1.90:1, на `#4C8DFF` = 3.20:1; `#6E6E77`(`--t-low`) на
  `#FFFFFF` ≈ 5.05:1, на `#E7E7EC` ≈ 4.09:1.
- **Расхождения с CLAUDE.md (правит код, CLAUDE.md подлежит правке при реализации):** (а) §2a утверждает, что
  apple-скин перенаправляет `--accent-ink` — фактически инлайн `applyAccent` (`tweaks.ts:45`) всегда сильнее,
  и работают только `--primary-ink`/`--brand-ink`/`--nav-active-fg`; (б) §2a описывает `Skeleton`/`Stagger`/
  `AnimatedNumber`/`.ni-skeleton` как живые примитивы — по grep они не используются нигде в `src/`
  (удаление — в бэклоге «later», но документацию поправить стоит).
- **Расхождение с разведкой:** самодельных тумблеров **11**, а не 12 (`grep -rn "bg-white rounded-full"`).
- **НЕ ПРОВЕРЕНО** (честно, чтобы никто не выдал за факт): реальные FPS/задержки в браузере пользователя —
  их не измеряли, разведка структурная; поэтому Ф1 = обязательная предпосылка. Также не проверено:
  доступность `page.routeWebSocket` в `@playwright/test 1.61` (дефолт — фейковый `window.WebSocket`),
  актуальность размеров `dist` (сборка от 11 июля) и то, что все существующие фронт-тесты сейчас зелёные.
- **Что НЕ является причиной лагов** (проверено разведкой, не тратить бюджет): `.ni-skeleton`, `Stagger`,
  `AnimatedNumber`, `.toast`, `.term`, `.bar-cell` — мёртвый код; `motion` живёт только в 160-мс фейде
  вкладок (`App.tsx:58-67`) и карте хостингов; glass-blur топбара/сайдбара (`index.css:258-263`,
  `App.tsx:204`) — статичная подложка, стоит промоушена слоя, а не перерисовки кадра.

## Критерии готовности плана F

- **Ф1:** `tests/e2e/perf-probe.mjs` и `src/theme/contrast.test.ts` закоммичены, `shots/perf/baseline.json`
  зафиксирован, числа базы выписаны; `npm test` зелёный.
- **Ф2:** `test_ws_logs.py` зелёный (склейка + порядок + `TASK_STORE=shared`); perf-probe A показывает
  падение `framesDelivered` и `longTaskTotalMs` **в разы**, `wsInstances`=0 для завершённых свёрнутых карточек;
  живой стрим деплоя визуально не изменился.
- **Ф3:** perf-probe B — `attrWrites` за 25 с упали до порядка реально изменившихся баров, `jankFrames`
  уменьшились; при скрытой вкладке поллов нет; бары/тултипы работают как раньше.
- **Ф4:** `KNOWN_FAILURES` в `contrast.test.ts` **пуст** по системным парам (t-faint и чернила акцента);
  все 7 акцентов дают читаемые primary-кнопку и активный пункт сайдбара; матрица скриншотов без регрессий.
- **Ф5:** `grep` даёт 0 самодельных тумблеров и 0 палитровых хардкодов в `auth/` и `DeployDashboard.tsx`;
  оверлей «Добавить аккаунт» соответствует активной теме.
- **Ф6:** вкладка «Ассистент» прокручивается и конфиг-форма достижима; колесо во вложенном списке не
  прокручивает родителя; `mousemove` над графиком нагрузки дешевле базы.
- **Ф7:** размеры `dist/assets/index-*.{js,css}` измеримо меньше свежей базы; `phase-render-smoke.mjs` без
  `pageerror`; `docker compose build frontend` проходит.
- Сквозное: `cd backend && python -m pytest`, `npm test`, `npx --no-install tsc --noEmit` — зелёные;
  ни одной новой npm/pip-зависимости; CLAUDE.md §2a поправлен (инлайн-перебивка `--accent-ink`, статус
  мёртвых motion-примитивов) и дополнен разделом про измерительные харнесы.
- Явно НЕ входит и остаётся в бэклоге «later»: удаление мёртвого theme/motion-кода и мёртвого rail-режима
  сайдбара.
