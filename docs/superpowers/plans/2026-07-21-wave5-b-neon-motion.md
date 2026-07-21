# Волна 5 · План B — Оформление: неон-скин, больше цветов, motion-анимации везде

> Идеи 2 и 7 Волны 5. (2) motion-анимации ВЕЗДЕ; (7) статичное оформление — больше цветов,
> неон, свечения (glow), насыщенные градиенты, анимированные акценты. Проектируем три слоя:
> **(a)** неон как ТРЕТИЙ `data-skin` (параллельная палитра, dark-committed) — НЕ ломая
> `apple`/`console` × `light`/`dark`; **(b)** переиспользуемый модуль motion-примитивов
> (`motion/react` уже в проекте) — переходы вкладок, stagger-появление списков/карточек,
> hover-glow, анимированные числа/бары, скелетоны; **(c)** раскатка на ключевые статичные
> поверхности (Dashboard/статус, карточки, модалки-bottom-sheets), с учётом `prefers-reduced-motion`
> и нового тумблера «анимации/неон» в Настройках→«Тема».
> Затрагивает: `frontend/src/theme/tweaks.ts` (оси/акцент/storage), `frontend/src/index.css`
> (token-слои + keyframes + reduced-motion), НОВЫЙ `frontend/src/theme/motion.ts` (варианты + хуки +
> UI-примитивы), `frontend/src/components/Settings.tsx` (`ThemeTab` селектор), `frontend/src/App.tsx`
> (apply-on-mount + вставка перехода вкладок), `frontend/src/components/Dashboard.tsx` (числа/бары/
> скелетоны), + backend `models/settings.py`/`api/settings.py` (per-account персист appearance —
> сквозная идея 5). Переиспользует `motion` (`package.json` `motion@^11.11.17`, уже бандлится —
> ЕДИНСТВЕННЫЙ текущий импорт в `components/hostings/HostingsMap.tsx`).

## Контекст (как есть)

- **Две независимых оси на `:root` (`theme/tweaks.ts`):** SKIN (`data-skin`, `AppSkin="apple"|"console"`,
  `SKINS`, дефолт apple, `applySkin` tweaks.ts:50, storage `ni_skin_<accountId>`) и MODE (`data-theme`,
  `ThemeMode="light"|"dark"|"system"`, дефолт system→dark, `applyThemeMode` tweaks.ts:71, storage
  `ni_thememode_<accountId>`). ОБА — per-account. ACCENT (`AccentKey="blue"|"green"|"violet"|"amber"|
  "cyan"`, `ACCENTS` tweaks.ts:23, `applyAccent` tweaks.ts:36, storage `ni_accent`) и DENSITY
  (`ni_density`) — **device-global**.
- **`applyAccent` пишет `--accent*` ИНЛАЙН-стилями на `documentElement`** (tweaks.ts:38-44:
  `--accent`/`--accent-hi`/`--accent-ink`/`--accent-dim`=`hexA(base,.13)`/`--accent-line`=`hexA(base,.4)`).
  Инлайн ВЫИГРЫВАЕТ каскад → правило `[data-skin="neon"]` в CSS **не может** переопределить `--accent`.
  Неон-скин обязан НАСЛАИВАТЬ glow/surface и ВЫВОДИТЬ цвет свечения из `--accent`, а не бороться с ним.
- **Токены — чистое CSS-var переуказание** (`index.css`): базовый `:root` (index.css:6-35) = console-dark
  (все токены: `--bg0..3`, `--line`/`--line-soft`, `--t-hi/mid/low/faint`, `--ok/warn/err` +`-dim`/`-line`,
  `--accent*`, радиусы `--r-sm/md/lg`, поверхности `--raised/row-hover/overlay/topbar-bg/sidebar-bg/
  term-bg/scroll-thumb`, `--nav-active-*`, `--glass-blur:8px`, `--shadow-pop/modal`, `--mono`/`--font`).
  Далее каскад: `:root[data-theme="light"]` (222-237, console-light) → `:root[data-skin="apple"]`
  (243-270, СТРУКТУРНОЕ: SF-шрифт, радиусы 7/9/8, `--glass-blur:22px`, iOS-тумблер `#34C759`,
  pill-контролы) → `[data-skin="apple"][data-theme="light"]` (273-289) → `[data-skin="apple"]:not(
  [data-theme="light"])` (292-304). Console не трогается apple-ветками.
- **`loadSkin` (tweaks.ts:112) валидирует ТОЛЬКО 2 значения:** `=== "console" ? "console" : "apple"` —
  любой третий скин сейчас деградирует в apple.
- **Инвентарь анимаций сейчас (`index.css`):** keyframes `ni-pulse` (118, `.dot.pulse`), `ni-fadeIn`
  (173, `.overlay`), `ni-riseIn` (174, `.modal`+drawer), `ni-toastIn` (182, `.toast`), `ni-spin` (199,
  `.spin`), `ni-sheetUp` (335, mobile bottom-sheet). Transitions кнопок/инпутов `.12s`; `StepProgress`
  бар width `duration-500`. Tailwind `animate-spin`/`animate-pulse` разбросаны по ~40 файлам. **`motion`
  (Framer Motion v11) — УЖЕ зависимость** (`package.json:27`), но импортируется РОВНО в одном файле:
  `components/hostings/HostingsMap.tsx` (spring-in маркеров, `AnimatePresence` попапа) — рантайм уже
  бандлится и проверен, новая зависимость НЕ нужна.
- **Пробел:** `prefers-reduced-motion` НЕ обрабатывается НИГДЕ (ни CSS-медиазапрос, ни `useReducedMotion`).
  Это надо добавить ПЕРВЫМ — база безопасности для всего motion-слоя.
- **Apply-on-mount (`App.tsx:121-126`):** `applySkin(loadSkin(getActiveId()))` → `applyThemeMode` →
  `applyAccent` → `applyDensity`, один раз; `App` keyed by `activeId` (AuthGate) → смена аккаунта
  ремаунтит и перечитывает per-account skin+mode.
- **`ThemeTab` (`Settings.tsx:558-647`):** 2-кол сетка скинов (574), 3-кол сетка режимов, свотчи акцента
  (5 цветов), seg плотности — каждый пикер зовёт `apply*`+`save*` императивно.
- **Переключение вкладок (`App.tsx:202-303`):** плоский `{tab === "…" && <Comp/>}` внутри `<main>` —
  без обёртки перехода; ~30 вкладок.
- **Backend appearance-персист:** ОТСУТСТВУЕТ. `AppSettings` (`models/settings.py:86-92`) держит
  `remnawave/deploy_defaults/optimization/xray_checker/mcp/ai`; тема живёт ТОЛЬКО в localStorage
  per-account (не следует за аккаунтом между устройствами). `api/settings.py` — `GET /settings` +
  `POST /settings/{remnawave,optimization,deploy-defaults,xray-checker}` (под `require_account`).

## Развилки (закреплены)

- **Неон = ТРЕТИЙ SKIN** (`AppSkin="apple"|"console"|"neon"`), НЕ отдельный режим-акцент. Причина:
  аддитивный `[data-skin="neon"]` каскад в точности зеркалит apple → нулевой риск для существующих
  веток; `applySkin` уже generic; `ThemeTab` авто-отрисует карточку (сделать сетку 3-кол).
- **Неон dark-committed:** палитра неона одинакова под `[data-theme="light"]` и `:not(...)` — как уже
  делает `--term-bg` (всегда тёмный). Опциональный «day-glow» light НЕ делаем в первой итерации
  (можно позже; MODE ортогонален — добавляется одной парной веткой).
- **Свечение выводится из акцента, не переопределяет его.** `applyAccent` дополнительно эмитит
  `--accent-glow` (`hexA(base,.55)`) на ВСЕХ скинах; РЕНДЕРИТСЯ (`--glow`) только под неоном. Apple/
  console получают `--glow:none` → нулевое визуальное изменение.
- **Больше цветов — БЕЗ ломки семантики состояний.** `--ok/warn/err` (смысл статуса) НЕ трогаем; неон
  поднимает их хрому в СВОЕЙ ветке. Добавляем data-ink токены `--viz-1..--viz-8` (паттерн уже есть —
  donut/line-палитры фикс-хьюы, CLAUDE.md §2a) + неон-boosted набор под `[data-skin="neon"]`. Расширяем
  `ACCENTS` двумя неон-хьюами (`magenta #FF4D9D`, `lime #B4FF3A`; `cyan` уже неон-adjacent) — работают на
  всех скинах, «стреляют» под неоном.
- **Тумблер «Анимации» + «Неон-glow» — device-global** (как accent/density), но зеркалятся в backend
  per-account appearance-конфиг (идея 5, ниже). Дефолт: анимации ON, но `prefers-reduced-motion:reduce`
  системно перекрывает (CSS-медиазапрос + `useReducedMotion()` гейт) независимо от тумблера.
- **В фоне не переспрашивать:** имена неон-акцентов, точные хекс-значения палитры, длительности
  (140-220мс) — берём дефолты ниже; менять только по явному запросу.
- **Backend appearance (идея 5):** НОВЫЙ `AppearanceConfig` на `AppSettings` + `POST /settings/appearance`
  — per-account зеркало префов (skin/mode/accent/density/animations/neon_glow), чтобы оформление
  следовало за аккаунтом между устройствами. Секретов нет → обычный JSON-стор (`storage.py`), Fernet
  НЕ нужен. localStorage остаётся быстрым локальным кэшем; backend — источник при первом входе аккаунта
  на новом устройстве.

## Стратегия

Ф1 (тема/неон: 3-й скин + токены glow/viz + reduced-motion CSS) → Ф2 (motion-модуль `theme/motion.ts`:
варианты+хуки+примитивы) → Ф3 (backend appearance-персист, идея 5) → Ф4 (frontend раскатка: переход
вкладок, stagger/числа/бары/скелетоны на Dashboard+карточках, `ThemeTab` тумблеры).

---
### Ф1 — Тема: неон-скин + токены свечения/цветов + reduced-motion → verify: tsc + preview (матрица скинов)

- **`theme/tweaks.ts`:** `AppSkin` += `"neon"`; `SKINS` += `{key:"neon", label:"Неон"}`; ослабить
  `loadSkin` (валидировать 3 значения: `neon|console|apple`, дефолт apple). `applySkin` уже generic —
  не трогать. `ACCENTS` += `magenta {base:"#FF4D9D",hi:"#FF80BC",ink:"#1A0510"}`,
  `lime {base:"#B4FF3A",hi:"#CDFF77",ink:"#0C1400"}` (форма `{base,hi,ink}` сохраняется). `applyAccent`
  дополнительно `r.setProperty("--accent-glow", hexA(a.base, 0.55))`. Добавить в модель `AccentKey`
  union новые ключи.
- **`index.css`:** (1) на базовый `:root` добавить ИНЕРТНЫЕ дефолты `--glow:none;
  --accent-glow:transparent;` + data-ink `--viz-1..--viz-8` (нейтральный набор). (2) НОВЫЙ каскад-блок
  ПОСЛЕ apple-веток (ничего в `:root`/apple НЕ править): `:root[data-skin="neon"]{…}` (СТРУКТУРНОЕ:
  `--mono`-шрифт (техно), радиусы, `--glass-blur`, `--glow:0 0 16px -2px var(--accent-glow)`,
  boosted `--viz-*`, boosted-хрома `--ok/warn/err`) + `:root[data-skin="neon"]:not([data-theme="light"])`
  и `:root[data-skin="neon"][data-theme="light"]` С ОДИНАКОВОЙ dark-палитрой (`--bg0:#05060A`,
  `--bg1:#090B12`, `--bg2:#0D1018`, `--bg3` slightly-lifted, `--line:#1B2740`, `--t-hi:#EAF6FF`,
  тёмные surface-токены). (3) утилиты: `.hover-glow{transition:box-shadow .18s} .hover-glow:hover,
  .card.glow,.btn-primary{box-shadow:var(--glow)}` (инертны вне неона), `.neon-text{text-shadow:0 0 8px
  var(--accent-glow)}` (применять к `.h1`/активному nav только под неоном). (4) **reduced-motion блок
  ПЕРВЫМ приоритетом:** `@media (prefers-reduced-motion: reduce){ *,*::before,*::after{
  animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms
  !important}}`. Любые бесконечные неон-pulse/scanline keyframes — ВНУТРИ `@media (prefers-reduced-motion:
  no-preference)`.
- verify: `tsc --noEmit`; preview — матрица `data-skin`×`data-theme` (apple/console/neon × light/dark):
  apple/console визуально НЕ изменились, неон рендерит glow под всеми акцентами; `prefers-reduced-motion:
  reduce` в devtools отключает анимации. (Опционально расширить `tests/e2e/theme-shots.mjs` матрицу
  третьим скином.)
---
### Ф2 — Motion-модуль `theme/motion.ts` (примитивы + reduced-motion гейт) → verify: tsc + preview

НОВЫЙ `frontend/src/theme/motion.ts` — framer-варианты + хуки + мелкие UI-компоненты, ВСЕ skin-agnostic
(рендерят на любом скине; неон лишь добавляет glow). КАЖДЫЙ вход/цикл гейтится `useReducedMotion()`
(из `motion/react`) → варианты схлопываются в opacity-only/no-op:
- **Переход вкладок:** экспортировать вариант `tabFade` (opacity 0→1 + y 8→0, 140-180мс) для обёртки
  `<AnimatePresence mode="wait">` над свитчем `<main>` (вставка в Ф4).
- **`<Stagger>`/`<StaggerItem>`** — `motion.div` с `staggerChildren:0.04`; item = opacity 0→1 + y 6→0.
- **`<AnimatedNumber value>`** — `useSpring`+`useTransform` (или мелкий rAF-твин); под reduced-motion
  снапает мгновенно. Для `tabular-nums`/`.num` значений.
- **`<Skeleton>`** — блок с `@keyframes ni-shimmer` (градиент-свип `--bg2`→`--bg3`; неон-вариант =
  accent-tinted свип). keyframe добавить в `index.css` (внутри no-preference-медиазапроса).
- **`useMotionEnabled()`** — читает device-global тумблер (localStorage `ni_motion`, дефолт on) И
  `useReducedMotion()`; оба гейтят. Экспортировать для условного рендера обёрток.
- verify: `tsc --noEmit`; preview — примитивы монтируются, при `prefers-reduced-motion` не анимируют.
  Юнит: `theme/motion.test.ts` (гейт-логика `useMotionEnabled`, снап `AnimatedNumber` под reduced).
---
### Ф3 — Backend: per-account appearance-персист (идея 5) → verify: pytest

- **`models/settings.py`:** НОВЫЙ `AppearanceConfig(BaseModel)` — `skin:str="apple"`, `mode:str="system"`,
  `accent:str="blue"`, `density:str="comfortable"`, `animations:bool=True`, `neon_glow:bool=True`
  (валидаторы допустимых значений enum-строк). Добавить поле `appearance: AppearanceConfig =
  AppearanceConfig()` на `AppSettings` (рядом с `mcp`/`ai`).
- **`api/settings.py`:** `POST /settings/appearance` (под `require_account`, паттерн
  `POST /settings/optimization`) — сохраняет `AppearanceConfig` в per-account `settings.json` через
  `storage.py` (`current_account` ContextVar). Секретов нет → обычный JSON, Fernet НЕ нужен. `GET /settings`
  уже отдаёт весь `AppSettings` → фронт читает `appearance` при первом входе.
- **Изоляция:** данные под `accounts/<id>/settings.json` (существующий стор), роут под `require_account`.
- verify: `python -m py_compile`; `backend/tests/test_settings_appearance.py` (сохранение/чтение per-account,
  дефолты, отклонение невалидного skin/mode/accent → 422, изоляция между аккаунтами).
---
### Ф4 — Frontend: раскатка motion+неон на статичные поверхности + `ThemeTab` тумблеры → verify: tsc + preview

- **Переход вкладок (`App.tsx:202-303`):** обернуть свитч `<main>` в `<AnimatePresence mode="wait">` +
  `<motion.div key={tab} variants={tabFade}>` (одно изменение покрывает все ~30 вкладок; гейт
  `useMotionEnabled`). Apply-on-mount (`App.tsx:121-126`) дополнить чтением backend `appearance` (Ф3) как
  fallback при отсутствии localStorage (первый вход на устройстве).
- **`ThemeTab` (`Settings.tsx:558-647`):** сетку скинов сделать 3-кол (авто-отрисует «Неон» из `SKINS`,
  добавить подпись `s.key==="neon"?"Неон, свечения и градиенты":…`); свотчи акцента авто-покажут 2 новых
  (`ACCENTS` итерируется). Добавить два тумблера: «Анимации интерфейса» (`ni_motion`) и «Неон-свечение»
  (device-global `ni_neon_glow`, влияет на `--glow` рендер) — каждый `apply*`+`save*` + `POST
  /settings/appearance` (best-effort зеркало).
- **Раскатка примитивов (ключевые статичные поверхности):**
  - `Dashboard.tsx` (статус): `<Stagger>` на country/sub-группах и `NodeRow`; `<AnimatedNumber>` на
    `Stat` (uptime %, кол-во протоколов) и живом ping; uptime-бар-грид `NodeRow` — stagger `scaleY 0→1`
    (transform-origin bottom); неон-glow на `up`-барах; loading-состояния («Загрузка…») → `<Skeleton>`.
  - Карточки: `DeployDashboard` карты и `DeployCard` числа (traffic/security counters) → `<AnimatedNumber>`;
    infra card-grids (`InfraDashboard` balances/burn) → stagger + числа; `HostingsCatalog` → stagger.
  - `.hover-glow` на `.card`/активном `.navitem`/`.btn-primary` (CSS из Ф1 — инертен вне неона).
  - Модалки/bottom-sheets: `ni-riseIn`/`ni-sheetUp` уже есть; добавить неон-glow бордер на `.modal` под
    неоном (в no-preference-медиазапросе).
- verify: `tsc --noEmit`; preview — переход вкладок плавный, списки stagger-появляются, числа тикают,
  скелетоны при загрузке; неон включает glow; `prefers-reduced-motion:reduce` всё гасит; apple/console
  без регрессий (визуальная матрица). Юнит: обновить `theme/tweaks.test.ts` (3-й скин + новые акценты +
  `--accent-glow`), `components/Settings.test.tsx` (тумблеры).

## РАЗВЕДКА (факты)

Внешних источников/URL у плана нет — фича полностью на нашем frontend + локальном backend-персисте.
Проверенные факты кода (сверено с файлами):
- `motion@^11.11.17` уже в `frontend/package.json:27`; единственный текущий импорт `motion/react` —
  `components/hostings/HostingsMap.tsx` (паттерн для зеркалирования: `motion.*`, `AnimatePresence`,
  spring). Новая npm-зависимость НЕ требуется — рантайм бандлится.
- `applyAccent` (tweaks.ts:36-44) пишет `--accent*` ИНЛАЙН на `documentElement.style` → инлайн выигрывает
  каскад; неон-скин НЕ может переопределить `--accent` через CSS-правило (отсюда развилка «glow из акцента»).
- `prefers-reduced-motion` в кодовой базе НЕ обрабатывается нигде (grep пусто) — Ф1 закрывает пробел.
- `loadSkin` (tweaks.ts:112) сейчас бинарный (`console`|`apple`) — требует ослабления под 3 значения.
- CSP-инвариант: без внешних CDN/шрифтов — неон использует существующие `--mono`/system-стек, свечения
  через `box-shadow`/`text-shadow`, без внешних ассетов.

## Критерии готовности плана B

- `AppSkin` расширен `neon` (3-я карточка в `ThemeTab`), `loadSkin` валидирует 3 значения; `ACCENTS` +2
  неон-хьюа; `applyAccent` эмитит `--accent-glow`.
- `index.css`: инертные `--glow`/`--accent-glow`/`--viz-*` дефолты + аддитивная `[data-skin="neon"]`
  ветка (dark-committed) + reduced-motion блок; apple/console × light/dark БЕЗ регрессий (визуальная матрица).
- `theme/motion.ts`: `tabFade`/`<Stagger>`/`<AnimatedNumber>`/`<Skeleton>`/`useMotionEnabled` — все под
  `useReducedMotion()`-гейтом; раскатаны на переход вкладок + Dashboard/карточки/модалки.
- Тумблеры «Анимации»/«Неон-свечение» в `ThemeTab` (device-global) + backend-зеркало.
- Backend: `AppearanceConfig` на `AppSettings` + `POST /settings/appearance` под `require_account`,
  per-account изоляция (`accounts/<id>/settings.json`), без секретов.
- Verify: `pytest` (`test_settings_appearance`) + `tsc --noEmit` + preview (матрица скин×режим +
  reduced-motion) + ручной smoke (смена аккаунта подтягивает appearance; неон+motion работают, apple/
  console чисты). Обновить CLAUDE.md §2a (skin×mode ось + motion-модуль).
