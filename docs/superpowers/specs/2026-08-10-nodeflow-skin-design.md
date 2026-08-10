# Скин «NodeFlow» для node-assistant — дизайн

Дата: 2026-08-10. Статус: утверждён пользователем (полный облик, обе темы).

## Цель

Четвёртый дизайн-скин `data-skin="nodeflow"` (рядом с apple/console/neon),
переносящий визуальный язык панели NodeFlow (вендоренная `nodeflow/frontend`,
Mantine-тема + `styles/global.css`): «лесная» зеленовато-тёмная палитра, Inter,
глубоко-зелёная primary-кнопка, 2px кромка активного пункта навигации,
`translateY(1px)` на `:active`, тайл иконки в заголовке страницы.

## Решения по скоупу (подтверждены)

- **Полный облик**: зеленоватые поверхности + Inter + зелёный акцент-дефолт
  скина. Акцент-пикер продолжает работать (как на neon).
- **Обе темы**: тёмная и светлая палитры (у NodeFlow определены обе).
- НЕ входит (YAGNI): Mantine-компоненты, z-index-система NodeFlow, 40px высоты
  контролов (у нас своя density-ось), traffic.css-специфика.

## Архитектура

Чисто аддитивно, по образцу neon-скина. Существующие скины не меняются.

1. `frontend/src/index.css` — три новых блока:
   - `:root[data-skin="nodeflow"]` — структура (шрифт, радиусы, nav-токены,
     primary-кнопка, `:active`-отдача, тайл `ni-pageicon`);
   - `:root[data-skin="nodeflow"]:not([data-theme="light"])` — тёмная палитра;
   - `:root[data-skin="nodeflow"][data-theme="light"]` — светлая палитра.
2. `frontend/src/theme/tweaks.ts`:
   - `AppSkin` += `"nodeflow"`; `SKINS` += `{ key: "nodeflow", label: "NodeFlow" }`;
     `loadSkin` принимает `"nodeflow"`;
   - `ACCENTS` += восьмой вариант `nodeflow` (с `light`-вариантом);
   - `applyAccent` становится тема-зависимым: у записи `ACCENTS` может быть
     `light: { base, hi, ink }` — берётся при `dataset.theme === "light"`;
     текущий акцент сохраняется в module-scope, `applyThemeMode` и системный
     matchMedia-листенер переприменяют его после смены темы;
   - новый `resolveAccentForSkin(skin)`: сохранённый акцент, иначе
     `skin === "nodeflow" ? "nodeflow" : "blue"` — зелёный дефолт применяется,
     только если пользователь никогда не выбирал акцент явно.
3. `frontend/src/App.tsx` — `applyAccent(resolveAccentForSkin(skin))`
   (skin уже читается в том же эффекте).
4. `frontend/index.html` — Inter добавляется в существующий Google Fonts link.
5. `frontend/src/theme/ui.tsx` — иконке `PageHeader` добавляется класс
   `ni-pageicon` (инертен на остальных скинах).

## Палитры (oklch NodeFlow → hex; тёмная)

Поверхности и производные:

| Токен | Значение | Источник NodeFlow |
|---|---|---|
| `--bg0` | `#060E0D` | canvas / sidebar |
| `--bg1` | `#0A1512` | surface |
| `--bg2` | `#0F1D18` | surface-raised |
| `--bg3` | `#13271D` | surface-active |
| `--line` | `rgba(158,177,161,.21)` | border (oklch 74% .03 151 / .21) |
| `--line-soft` | `rgba(158,177,161,.13)` | border-soft |
| `--t-hi` | `#E2EAE4` | text (oklch 93%) |
| `--t-mid` | `#9DACA0` | secondary (oklch 73%) |
| `--t-low` | `#8F9D91` | tertiary, поднят с oklch 59% → 68% |
| `--t-faint` | `#808D82` | поднят до AA (см. ниже) |
| `--raised` | `rgba(10,21,18,.6)` | surface-soft полупрозрачный |
| `--row-hover` | `rgba(19,39,29,.45)` | surface-active |
| `--overlay` | `rgba(4,10,8,.72)` | overlay |
| `--topbar-bg` | `rgba(10,21,18,.7)` | |
| `--sidebar-bg` | `#060E0D` | sidebar |
| `--term-bg` | `#050B09` | терминал остаётся тёмным |
| `--scroll-thumb` | `#1A2E24` | |

Текстовая иерархия на `--bg3` (худшая поверхность): 4.53 / 5.54 / 6.62 / 12.82 —
все ≥ AA 4.5. **Отступление от NodeFlow (принцип > токен)**: его tertiary
(oklch 59% ≈ `#748176`, 3.85 на bg3) и disabled (2.44) не проходят наш
AA-гейт; значения подняты до прохождения. Зафиксировано как token gap.

Акцент и статусы (тёмная):

| Токен | Значение |
|---|---|
| `ACCENTS.nodeflow` (tweaks.ts) | `base: #48BD54, hi: #6FDA75, ink: #062110` (ink→base 7.04) + **light-вариант** (см. ниже) |
| `--ok` | `#47BE8B` (oklch 72% .13 162) |
| `--warn` | `#E0AF3B` (oklch 78% .14 85) |
| `--err` | `#F05F5A` (oklch 67% .18 25) |
| dim/line варианты | те же альфы, что в базовой палитре (.11/.32–.42) |
| `--viz-1` | `#48BD54` (линия графиков = акцент скина) |

**Тема-зависимый акцент (механика tweaks.ts).** `applyAccent()` ставит
`--accent*` инлайн на `:root` и перебивает любые stylesheet-правила, поэтому
«светлая версия акцента» не может быть CSS-блоком — она живёт в самом
`ACCENTS`-записе: необязательное поле `light: { base, hi, ink }`. Оно есть
только у `nodeflow`: `light: { base: #157A2B, hi: #157A2B, ink: #FFFFFF }`
(из Mantine-рампы NodeFlow; на белом 5.45 — `#48BD54` даёт 2.42, `#6FDA75`
1.76, текстом недопустимы). `applyAccent` читает `dataset.theme` и берёт
`light`-вариант на светлой теме; `applyThemeMode` (и системный
matchMedia-листенер) переприменяет текущий акцент после смены темы. Текущий
акцент хранится в module-scope переменной tweaks.ts.

Primary-кнопка — отдельно от акцента (фирменный ход NodeFlow):
`.btn-primary` под скином — фон `#187A36`, белый текст (контраст 5.42),
hover `#14672E`. Переопределяет `--accent`-стили только для `.btn-primary`.

## Палитра (светлая)

| Токен | Значение | Источник |
|---|---|---|
| `--bg0` | `#F2F6F3` | canvas |
| `--bg1` | `#FFFFFF` | surface |
| `--bg2` | `#EDF4EF` | surface-raised |
| `--bg3` | `#E3EEE6` | surface-active |
| `--line` | `rgba(48,78,61,.22)` | border |
| `--line-soft` | `rgba(48,78,61,.13)` | border-soft |
| `--t-hi` | `#14231A` | text |
| `--t-mid` | `#2E3E34` | между text и secondary (9.51 на bg3) |
| `--t-low` | `#52665A` | secondary |
| `--t-faint` | `#5A6C60` | tertiary, поднят до AA (token gap) |
| `--raised` | `#F5F8F6` | surface-soft |
| `--row-hover` | `rgba(20,35,26,.04)` | |
| `--overlay` | `rgba(20,35,26,.3)` | |
| `--topbar-bg` | `rgba(255,255,255,.78)` | |
| `--sidebar-bg` | `#EAF1EC` | sidebar |
| `--term-bg` | `#0F1D18` | тёмный и на светлой |
| `--scroll-thumb` | `rgba(20,35,26,.22)` | |
| `--ok` / `--warn` / `--err` | `#12703A` / `#7A5C00` / `#C03430` | затемнённые версии статусов |
| `--viz-1` | `#157A2B` | светлый вариант акцента (читаемая линия на белом) |

Иерархия на `--bg3`: 4.71 / 5.18 / 9.51 / 13.73 — все ≥ AA 4.5.
Акцент на светлой — через `ACCENTS.nodeflow.light` (см. «Тема-зависимый
акцент» выше): `#157A2B` везде, где на тёмной был `#48BD54`; белые чернила
на нём проходят (5.45).

## Компонентный контракт скина

- **Шрифт**: `--font: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`;
  `--mono` без изменений (терминал/код). `index.html`: `family=Inter:wght@400;500;600;700`
  в существующий link с `display=swap`.
- **Радиусы**: `--r-sm:8px; --r-md:8px; --r-lg:10px`.
- **Nav**: `--nav-active-bg: var(--bg2); --nav-active-fg: var(--t-hi)`;
  `::before` — `left:0; width:2px; top:11px; bottom:11px` (кромка NodeFlow).
- **Press-отдача**: `.btn:active:not(:disabled), .iconbtn:active` →
  `transform: translateY(1px)` (NodeFlow signature; ease скина
  `cubic-bezier(.25,1,.5,1)` для этих микродвижений).
- **PageHeader**: `.ni-pageicon` под скином — тайл 34×34,
  `background: var(--accent-dim); border: 1px solid var(--accent-line);
  border-radius: var(--r-sm)`. На остальных скинах класс без стилей.
- **Фокус**: остаётся общий (`outline: 2px var(--accent-line)`) — совпадает
  с NodeFlow (`outline: 2px var(--nf-accent)`).

## Тесты

1. `frontend/src/theme/contrast.test.ts` — `PALETTES` += `nodeflow-dark`,
   `nodeflow-light` (блоки читаются как текст, поэтому все измеряемые токены —
   hex, не oklch). Скин сразу под AA-гейтом; `KNOWN_FAILURES` остаётся пустым.
2. `frontend/src/theme/tweaks.test.ts` — тест «exposes exactly the two skin
   options» давно падает (ждёт 2, есть 3): обновить до 4 с `nodeflow`.
   Заодно закрывает существующий долг.
3. Прогон: `tsc --noEmit` + полный `vitest run`.

## Риски / открытые вопросы

- Настройки → Тема: пикеры скинов/акцентов рендерятся из `SKINS`/`ACCENTS`
  автоматически — проверить, что четвёртый скин и восьмой акцент встают без
  правок разметки (и как подписан акцент `nodeflow` — при необходимости
  добавить человекочитаемый label).
- `applyThemeMode` после рефактора переприменяет акцент — проверить, что
  системный matchMedia-листенер идёт тем же путём (иначе на «Системной» теме
  акцент не переключится при смене OS-темы).
