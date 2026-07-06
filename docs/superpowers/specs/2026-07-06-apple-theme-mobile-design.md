# Apple-скин + мобильная версия — дизайн

**Дата:** 2026-07-06
**Ветка:** `claude/panel-overhaul` (worktree `.claude/worktrees/panel-overhaul`)
**Источник дизайна:** handoff-бандл Claude Design (`Node Installer - Redesign.html` + `js/*`), приложен пользователем.

## Цель

1. Добавить **Apple-скин** как дизайн-тему, **по умолчанию — apple**, с переключателем `Apple`/`Консоль`.
2. Цветовой режим (light/dark/**system**) остаётся отдельной осью; дефолт `system`, при отсутствии системного light-предпочтения → dark. (Уже реализовано — не меняем.)
3. Добавить **мобильную версию** сайта (адаптив ≤820px / ≤600px): нижний таб-бар, drawer, bottom-sheet-модалки, safe-area, увеличенные тап-таргеты, reflow.
4. Чтобы Apple-**light** выглядела цельно, **перевести все оставшиеся hardcoded-dark компоненты на токены темы**.

Дизайн-прототип использует **ту же систему CSS-классов**, что и наш `index.css` (`.btn/.card/.navitem/.seg/.switch/.tbl/.input/.chip`…) — порт аддитивный. Реальный `:root` уже содержит все themeable-токены, на которые ссылается Apple-скин (`--nav-active-bg/fg`, `--glass-blur`, `--brand-ink`, `--primary-ink`, `--raised`, `--scroll-thumb`, shadows) с комментарием «the Apple theme re-points these» — база была подготовлена заранее.

## Модель темы: две независимые оси

| Ось | Атрибут (на `documentElement`) | Значения | Дефолт | Хранение |
|---|---|---|---|---|
| **skin** (новая) | `data-skin` | `apple` \| `console` | `apple` | per-account `ni_skin_<id>` |
| **mode** (есть) | `data-theme` | `light` \| `dark` (разрешается из light/dark/**system**) | `system`→dark | per-account `ni_thememode_<id>` |
| accent (есть) | inline `--accent*` | 5 hue | `blue` | device-global |
| density (есть) | `body[data-density]` | comfortable/compact | comfortable | device-global |

**Почему отдельная ось `data-skin`, а не прототипное `data-theme="apple-light"`:** прототип кодирует skin+mode в одно значение. Дословный перенос потребовал бы переписать уже работающую console-логику light/dark/**system** (`resolveThemeMode`, live-listener системной темы). Отдельный атрибут `data-skin` чисто аддитивен: console остаётся нетронутым, apple добавляется параллельным набором правил.

Отвергнутые альтернативы: (а) слить обе оси в одно `data-theme` значение — ломает существующий system-режим; (б) выкинуть console-скин целиком — противоречит выбранному «переключатель скинов».

## Компонент 1. CSS-порт (`frontend/src/index.css`)

Селекторы прототипа переписываются механически:

| Прототип | Наш селектор | Содержимое |
|---|---|---|
| `body[data-theme^="apple"]` | `:root[data-skin="apple"]` | структура: SF/системный шрифт (`--font`), радиусы 7/10/14, iOS-тоггл `#34C759`, pill-сегменты, filled-nav-pill (без левой полоски), `--glass-blur:22px`, вибрэнси sidebar/topbar, `letter-spacing:-.006em` |
| `body[data-theme="apple-light"]` | `:root[data-skin="apple"][data-theme="light"]` | Apple-**light** палитра (bg `#ECECEF/#F7F7F9/#FFFFFF`, text `#1D1D1F…`, iOS-цвета состояний, тени, `--term-bg:#1C1C1E`) |
| `body[data-theme="apple-dark"]` | `:root[data-skin="apple"]:not([data-theme="light"])` | Apple-**dark** палитра (bg `#1C1C1E/#232325/#2C2C2E`, iOS-dark состояния) |
| `body[data-theme^="apple"] .ni-tabbar` | `:root[data-skin="apple"] .ni-tabbar` | вибрэнси нижнего таб-бара |

Палитры (light+dark) берутся из прототипа как есть. Console-скин (base `:root` + `:root[data-theme="light"]`) не трогаем.

**Мобильный блок** (скин-независим, переносится дословно):
- `@media (max-width:820px)`: `.ni-sidebar{display:none}`, `.ni-tabbar{display:flex}`, `.ni-main{padding-bottom:calc(58px+safe)}`, ужатие `.ni-topbar`, `.ni-clock{display:none}`, `.ni-pagehead` в колонку, тап-таргеты (`.btn` min-h 40, `.iconbtn` 38×38, `.seg button` min-h 34).
- `@media (max-width:600px)`: reflow `.ni-health`/`.ni-noderow`/`.ni-node-name`/`.ni-node-bars`; модалки → bottom-sheets (`.overlay`→`align-items:flex-end`, `.modal`→полная ширина, `border-radius:16px 16px 0 0`, `sheetUp` анимация); `.ni-drawer aside` safe-top.
- Новые токены в `:root`: `--safe-b/l/r: env(safe-area-inset-*, 0px)`.
- В `index.html`: `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">` (для `env(safe-area-inset-*)`).

## Компонент 2. Skin-состояние (`frontend/src/theme/tweaks.ts`)

Добавить рядом с существующими mode/accent/density (тот же паттерн):
```ts
export type AppSkin = "apple" | "console";
export const SKINS: { key: AppSkin; label: string }[] =
  [{ key: "apple", label: "Apple" }, { key: "console", label: "Консоль" }];
export function applySkin(s: AppSkin): void { document.documentElement.dataset.skin = s; }
const skinKey = (id?) => id ? `ni_skin_${id}` : "ni_skin";
export function loadSkin(id?): AppSkin { /* default "apple" */ }
export function saveSkin(id, s): void { ... }
```
`App.tsx` mount-effect (где уже `applyThemeMode/applyAccent/applyDensity`) добавляет `applySkin(loadSkin(getActiveId()))`.

## Компонент 3. Мобильный shell (`frontend/src/App.tsx`, `Sidebar.tsx`, страницы)

Media-queries требуют `ni-*` класс-хуков (сейчас shell на инлайн-стилях):
- `App.tsx`: `className="ni-sidebar"` на `<aside>`, `ni-topbar` на `<header>`, `ni-main` на `<main>`, `ni-clock` на блок даты.
- **`BottomTabBar`** (новый компонент, порт из `app.jsx`): 4 таба (**Статус/Деплой/SSL/Трафик**) + «Ещё» → открывает мобильный **drawer** с полной навигацией (Хосты, Шаблоны, Настройки, Инфра-группа). Виден только ≤820px (`.ni-tabbar`).
- **Drawer**: выезжающая панель `.ni-drawer` со скримом, внутри переиспользует существующий `<Sidebar>`; открывается по «Ещё».
- Страницы: обёртки заголовков получают `ni-pagehead`/`ni-pagehead-actions`, тело — `ni-pagebody`. У нас нет общего `Page/PageHeader` — добавляем хуки к существующим per-page заголовкам (Dashboard, DeployDashboard, Settings, Hosts, TrafficRules, CertsForm/DomainsPanel, Templates, infra/*).
- `Dashboard.tsx`: `ni-health` на health-баннер, `ni-noderow`/`ni-node-name`/`ni-node-bars` на строки нод.
- Фикс фиксированных grid-колонок под мобилу: `App.tsx` certs `grid-cols-[360px_1fr]` и `DeployCard.tsx` `grid-cols-[260px_1fr]` → стек на ≤820px. Широкие `.tbl` (Traffic/Hosts/Infra) — обёртка `overflow-x:auto`.

## Компонент 4. Селектор скина (`frontend/src/components/Settings.tsx` → `ThemeTab`)

Сверху `ThemeTab`, над выбором режима, добавить **Стиль**: две карточки `Apple` (дефолт) / `Консоль` (тот же паттерн, что mode-карточки). Применение: `setSkin(s); applySkin(s); saveSkin(accountId, s)` — мгновенно.

## Компонент 5. Конвертация hardcoded-dark → токены темы

Чтобы Apple-light (и обычная light) выглядели цельно. Заменить хардкод-hex на var-токены (`--bg*/--line*/--t-*/--ok|warn|err*/--accent*`), сохранив визуал на dark. Цели:
- `components/Settings.tsx` — подформы Remnawave / Deploy-defaults / Optimization.
- `components/Templates.tsx`
- `components/TrafficRules.tsx`
- `components/MultiSelect.tsx` (та самая «тёмный остров»), `components/CountrySelect.tsx` (проверить min-width на мобиле).
- `components/infra/*` — `InfraDashboard`, `InfraProviders`, `InfraProjects`, `InfraServices`, `InfraPayments`, `InfraSettings`, `InfraApiTokens`, а также общие `infra/ui.tsx`, `infra/Toast.tsx`.

Правило: только замена цветов на токены; структуру/логику не трогаем (surgical). Widetables получают `overflow-x` обёртку заодно.

## Верификация (frontend — playwright/скриншоты, НЕ TDD)

Каждая фаза:
- `npx tsc --noEmit` — чисто.
- `npx vitest run` — существующие тесты зелёные (обновить `Sidebar.test.tsx` при изменении навигации; добавить unit на `loadSkin/applySkin` дефолт=apple, per-account, и на `resolveThemeMode` неизменность).
- **Playwright-скриншоты** (харнесс `tests/e2e/theme-shots.mjs` уже есть): матрица `skin×mode` = {apple,console}×{light,dark} на десктопе + мобильном вьюпорте (375×812, `viewport-fit`), ключевые экраны (Dashboard, Deploy-форма, Settings→Тема, одна infra-страница, модалка как bottom-sheet). Console-скриншоты — регресс (не должны измениться).
- Мобильный смоук: bottom-tab-bar виден ≤820px, sidebar скрыт, «Ещё» открывает drawer, модалка = bottom-sheet ≤600px.

## Известные ограничения / решения

- Терминал (`--term-bg`) намеренно тёмный даже в light (и apple-light) — соответствует прототипу.
- Apple-appearance в прототипе только light/dark; мы сохраняем **system** как третий режим — Apple работает поверх разрешённого значения, цвет следует системе.
- Существующие per-account ключи (`ni_thememode_<id>`, `deploy_jobs_<id>`, `ni_tab_<id>`) дополняются `ni_skin_<id>`; при переключении аккаунта App перемонтируется (keyed by activeId) и перечитывает скин.

## Объём

Многодоменно (theme-система + мобильный shell + навигация + конвертация ~12 компонентов + тесты/скриншоты). Кандидат на **пофазное** исполнение. Предлагаемая нарезка фаз:

1. **CSS-порт + skin-состояние + селектор** (index.css apple+mobile блоки, tweaks.ts, ThemeTab, viewport meta).
2. **Мобильный shell + навигация** (ni-* хуки, BottomTabBar, drawer, grid/tbl фиксы).
3. **Конвертация hardcoded-dark**, часть A (Settings-подформы, Templates, TrafficRules, MultiSelect, CountrySelect).
4. **Конвертация hardcoded-dark**, часть B (все infra/* + infra/ui/Toast).
5. **Скриншот-матрица + регресс + полировка**.
