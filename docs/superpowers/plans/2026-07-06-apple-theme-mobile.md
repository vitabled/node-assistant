# Apple-скин + мобильная версия — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить Apple-скин (дефолт) с переключателем Apple/Консоль и полноценную мобильную версию, переведя оставшиеся hardcoded-dark компоненты на токены темы.

**Architecture:** Две независимые оси на `documentElement`: новая `data-skin` (`apple`|`console`, дефолт apple, per-account) поверх существующей `data-theme` (light/dark, разрешается из light/dark/**system**). Apple-скин и мобильный адаптив портируются **аддитивно** из handoff-прототипа (`scratchpad/design-system/.../Node Installer - Redesign.html`), который использует ту же систему CSS-классов, что и наш `index.css`. Console-скин остаётся нетронутым.

**Tech Stack:** React 18 + TS + Vite + Tailwind; CSS-переменные + рукописные классы в `index.css`; Playwright для скриншот-верификации; vitest для unit.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-07-06-apple-theme-mobile-design.md`.
- Дефолт скина — **apple**; дефолт режима — **system** (→dark при отсутствии light-предпочтения). Режим НЕ трогаем (`theme/tweaks.ts:resolveThemeMode` без изменений).
- `data-skin`/`data-theme` — на `documentElement` (не на `body`).
- Хранение per-account: ключ `ni_skin_<accountId>` (паттерн как у `ni_thememode_<id>`).
- Surgical: при конвертации меняем только цвета (Tailwind `*-gray-*`/`rgba`/hex → токены `var(--…)`), структуру/логику/отступы не трогаем.
- Терминал (`--term-bg`) намеренно остаётся тёмным во всех темах.
- Console-скриншоты — регресс: не должны меняться после порта.
- Верификация фронта — playwright/скриншоты + `tsc --noEmit` + `vitest run` (НЕ TDD для UI; unit-тесты только для чистой логики `tweaks.ts`).
- Коммит после каждой задачи. Прогон: `cd frontend && npx tsc --noEmit && npx vitest run`.
- Prettier не установлен в репо — хук advisory, форматирование не блокирует; не ставить prettier ради этого.

---

### Task 1: CSS-порт Apple-скина + мобильного блока

**Files:**
- Modify: `frontend/src/index.css` (добавить в конец, после light-блока строки ~237)
- Modify: `frontend/index.html` (viewport meta)

**Interfaces:**
- Produces: CSS-правила `:root[data-skin="apple"]`, `:root[data-skin="apple"][data-theme="light"]`, `:root[data-skin="apple"]:not([data-theme="light"])`; мобильные media-queries на классы `ni-sidebar/ni-topbar/ni-main/ni-tabbar/ni-burger/ni-pagebody/ni-pagehead/ni-pagehead-actions/ni-clock/ni-health/ni-noderow/ni-node-name/ni-node-bars/ni-drawer`; токены `--safe-b/l/r`. Task 3–5 добавляют эти классы в разметку; Task 2 выставляет `data-skin`.

- [ ] **Step 1: Добавить safe-area токены в `:root`**

В `frontend/src/index.css`, в блок `:root{…}` (после строки с `--glass-blur:8px;`, ~строка 30) добавить:
```css
  --safe-b:env(safe-area-inset-bottom,0px); --safe-l:env(safe-area-inset-left,0px); --safe-r:env(safe-area-inset-right,0px);
```

- [ ] **Step 2: Дописать Apple-скин в конец `index.css`**

В конец файла добавить (селекторы адаптированы с `body[data-theme^="apple"]` → `:root[data-skin="apple"]`):
```css
/* ════════ Apple skin (System Settings) — накладывается поверх любого data-theme ════════ */
:root[data-skin="apple"]{
  --font:-apple-system,BlinkMacSystemFont,"SF Pro Text","SF Pro Display","Helvetica Neue",system-ui,sans-serif;
  --r-sm:7px; --r-md:10px; --r-lg:14px;
  --accent-ink:#fff; --primary-ink:#fff; --brand-ink:#fff;
  --nav-active-bg:var(--accent); --nav-active-fg:#fff;
  --glass-blur:22px; letter-spacing:-.006em;
}
:root[data-skin="apple"] .navitem{font-weight:450;border-radius:8px}
:root[data-skin="apple"] .navitem.active{font-weight:600}
:root[data-skin="apple"] .navitem.active::before{display:none}
:root[data-skin="apple"] .ni-sidebar,
:root[data-skin="apple"] .ni-topbar,
:root[data-skin="apple"] .ni-tabbar{
  -webkit-backdrop-filter:blur(var(--glass-blur)) saturate(180%);
  backdrop-filter:blur(var(--glass-blur)) saturate(180%);
}
:root[data-skin="apple"] .btn{font-weight:590;border-radius:8px}
:root[data-skin="apple"] .btn-primary{box-shadow:0 1px 2px rgba(0,0,0,.14)}
:root[data-skin="apple"] .iconbtn{border-radius:7px}
:root[data-skin="apple"] .switch{width:38px;height:22px}
:root[data-skin="apple"] .switch::after{width:16px;height:16px}
:root[data-skin="apple"] .switch.on{background:#34C759;border-color:#34C759}
:root[data-skin="apple"] .switch.on::after{transform:translateX(16px);background:#fff}
:root[data-skin="apple"] .seg{border-radius:9px}
:root[data-skin="apple"] .seg button{border-radius:7px;font-weight:530}
:root[data-skin="apple"] .seg button.on{box-shadow:0 1px 3px rgba(0,0,0,.14)}
:root[data-skin="apple"] .tag{border-radius:6px;text-transform:none;letter-spacing:.01em}
:root[data-skin="apple"] .term .lt{color:#6B6B72}

/* Apple · Light (accent-независимая палитра поверх console-light) */
:root[data-skin="apple"][data-theme="light"]{
  --bg0:#ECECEF; --bg1:#F7F7F9; --bg2:#FFFFFF; --bg3:#E7E7EC;
  --line:#D7D7DD; --line-soft:#E7E7EC;
  --t-hi:#1D1D1F; --t-mid:#3E3E46; --t-low:#6E6E77; --t-faint:#A6A6AE;
  --ok:#28A745;   --ok-dim:rgba(52,199,89,.13);   --ok-line:rgba(40,167,69,.28);
  --warn:#B25E00; --warn-dim:rgba(255,149,0,.14);  --warn-line:rgba(178,94,0,.26);
  --err:#D70015;  --err-dim:rgba(255,59,48,.10);   --err-line:rgba(215,0,21,.26);
  --raised:#F1F1F4; --row-hover:rgba(0,0,0,.035); --overlay:rgba(0,0,0,.26);
  --topbar-bg:rgba(247,247,249,.72); --sidebar-bg:rgba(240,240,243,.72);
  --term-bg:#1C1C1E;
  --shadow-pop:0 12px 34px rgba(0,0,0,.14); --shadow-modal:0 20px 60px rgba(0,0,0,.22);
  --scroll-thumb:rgba(0,0,0,.22);
}
:root[data-skin="apple"][data-theme="light"] .card{box-shadow:0 1px 2px rgba(0,0,0,.05)}
:root[data-skin="apple"][data-theme="light"] .toast.ok{background:rgba(240,250,243,.94);color:#1B7A34}
:root[data-skin="apple"][data-theme="light"] .toast.err{background:rgba(253,240,240,.94);color:#C11}
:root[data-skin="apple"][data-theme="light"] .toast.info{background:rgba(248,248,250,.96);color:var(--t-mid)}

/* Apple · Dark (когда data-theme ≠ light) */
:root[data-skin="apple"]:not([data-theme="light"]){
  --bg0:#1C1C1E; --bg1:#232325; --bg2:#2C2C2E; --bg3:#3A3A3C;
  --line:#3A3A3C; --line-soft:#2E2E30;
  --t-hi:#F5F5F7; --t-mid:#D0D0D6; --t-low:#98989F; --t-faint:#6A6A70;
  --ok:#30D158;   --ok-dim:rgba(48,209,88,.15);   --ok-line:rgba(48,209,88,.32);
  --warn:#FF9F0A; --warn-dim:rgba(255,159,10,.15); --warn-line:rgba(255,159,10,.32);
  --err:#FF453B;  --err-dim:rgba(255,69,58,.14);   --err-line:rgba(255,69,58,.34);
  --raised:rgba(255,255,255,.045); --row-hover:rgba(255,255,255,.05); --overlay:rgba(0,0,0,.55);
  --topbar-bg:rgba(30,30,32,.72); --sidebar-bg:rgba(35,35,37,.72);
  --term-bg:#151517;
  --shadow-pop:0 14px 40px rgba(0,0,0,.5); --shadow-modal:0 24px 64px rgba(0,0,0,.6);
  --scroll-thumb:rgba(255,255,255,.18);
}
```

- [ ] **Step 3: Дописать мобильный блок в конец `index.css`**

```css
/* ════════ Mobile / touch adaptation (скин-независимо) ════════ */
.ni-tabbar{display:none}
@media (max-width:820px){
  .ni-sidebar{display:none !important}
  .ni-burger{display:none !important}
  .ni-tabbar{display:flex !important}
  .ni-main{padding-bottom:calc(58px + var(--safe-b)) !important}
  .ni-topbar{padding-left:max(16px,var(--safe-l)) !important; padding-right:max(16px,var(--safe-r)) !important; gap:8px !important}
  .ni-clock{display:none !important}
  .ni-pagebody{padding:16px max(16px,var(--safe-r)) 28px max(16px,var(--safe-l)) !important}
  .ni-pagehead{flex-direction:column; align-items:stretch !important; gap:12px !important}
  .ni-pagehead .ni-pagehead-actions{flex-wrap:wrap; width:100%}
  .btn{min-height:40px}
  .iconbtn{width:38px;height:38px}
  .navitem{padding-top:10px;padding-bottom:10px}
  .seg button{min-height:34px}
}
@media (max-width:600px){
  .ni-health{flex-wrap:wrap; gap:12px 16px !important; padding:16px !important}
  .ni-health-stats{width:100%; justify-content:flex-start; gap:28px !important; text-align:left !important}
  .ni-noderow{flex-wrap:wrap; gap:6px 14px !important; padding-top:11px !important; padding-bottom:11px !important}
  .ni-noderow .ni-node-name{width:auto !important; flex:1 1 auto !important}
  .ni-noderow .ni-node-bars{order:5; flex:1 1 100% !important; width:100%}
  .overlay{align-items:flex-end !important; padding:0 !important}
  .modal{max-width:100% !important; width:100% !important; border-radius:16px 16px 0 0 !important;
    border-bottom:none !important; max-height:92vh !important; padding-bottom:var(--safe-b);
    animation:sheetUp .22s cubic-bezier(.22,1,.36,1) !important}
  .ni-drawer aside{padding-top:max(16px,env(safe-area-inset-top)) !important}
}
@keyframes sheetUp{from{transform:translateY(100%)}to{transform:none}}
```

- [ ] **Step 4: viewport meta для safe-area**

В `frontend/index.html` заменить существующий `<meta name="viewport" …>` на:
```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
```

- [ ] **Step 5: Проверка сборки**

Run: `cd frontend && npx tsc --noEmit && npx vite build`
Expected: сборка без ошибок (CSS валиден). Визуально ничего ещё не поменялось (нет `data-skin`, нет `ni-*` классов).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/index.css frontend/index.html
git commit -m "feat(theme): порт Apple-скина + мобильного CSS (data-skin ось, media 820/600)"
```

---

### Task 2: Skin-состояние в `theme/tweaks.ts` + wiring в App

**Files:**
- Modify: `frontend/src/theme/tweaks.ts`
- Modify: `frontend/src/App.tsx:27-29` (импорт), `:82-86` (mount-effect)
- Test: `frontend/src/theme/tweaks.test.ts` (создать)

**Interfaces:**
- Produces: `type AppSkin = "apple" | "console"`; `SKINS: {key:AppSkin;label:string}[]`; `applySkin(s:AppSkin):void` (ставит `documentElement.dataset.skin`); `loadSkin(accountId?:string|null):AppSkin` (дефолт `"apple"`); `saveSkin(accountId:string|null|undefined, s:AppSkin):void`. Task 3 (ThemeTab) их потребляет.

- [ ] **Step 1: Написать падающий тест**

Создать `frontend/src/theme/tweaks.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { loadSkin, saveSkin, applySkin, resolveThemeMode } from "./tweaks";

beforeEach(() => { localStorage.clear(); document.documentElement.removeAttribute("data-skin"); });

describe("skin", () => {
  it("defaults to apple when nothing stored", () => {
    expect(loadSkin("acc1")).toBe("apple");
    expect(loadSkin(null)).toBe("apple");
  });
  it("persists per-account and reads back", () => {
    saveSkin("acc1", "console");
    expect(loadSkin("acc1")).toBe("console");
    expect(loadSkin("acc2")).toBe("apple"); // isolated
  });
  it("applySkin sets data-skin on documentElement", () => {
    applySkin("apple");
    expect(document.documentElement.dataset.skin).toBe("apple");
    applySkin("console");
    expect(document.documentElement.dataset.skin).toBe("console");
  });
  it("ignores garbage stored value → apple", () => {
    localStorage.setItem("ni_skin_acc1", "frutiger");
    expect(loadSkin("acc1")).toBe("apple");
  });
});

describe("resolveThemeMode unchanged", () => {
  it("system resolves to light|dark", () => {
    expect(["light","dark"]).toContain(resolveThemeMode("system"));
    expect(resolveThemeMode("dark")).toBe("dark");
    expect(resolveThemeMode("light")).toBe("light");
  });
});
```

- [ ] **Step 2: Прогнать — падает**

Run: `cd frontend && npx vitest run src/theme/tweaks.test.ts`
Expected: FAIL — `loadSkin`/`saveSkin`/`applySkin` не экспортированы.

- [ ] **Step 3: Реализация в `tweaks.ts`**

Добавить после блока `ThemeMode`/`THEME_MODES` (после строки ~12):
```ts
export type AppSkin = "apple" | "console";
export const SKINS: { key: AppSkin; label: string }[] = [
  { key: "apple",   label: "Apple" },
  { key: "console", label: "Консоль" },
];
```
Добавить после `applyDensity` (~строка 39):
```ts
export function applySkin(s: AppSkin): void {
  document.documentElement.dataset.skin = s;
}
```
Добавить рядом с `themeModeKey` (~строка 77):
```ts
// Skin is per-account (like theme mode). Default apple.
const skinKey = (accountId?: string | null) =>
  accountId ? `ni_skin_${accountId}` : "ni_skin";
export function loadSkin(accountId?: string | null): AppSkin {
  return localStorage.getItem(skinKey(accountId)) === "console" ? "console" : "apple";
}
export function saveSkin(accountId: string | null | undefined, s: AppSkin): void {
  localStorage.setItem(skinKey(accountId), s);
}
```

- [ ] **Step 4: Прогнать — проходит**

Run: `cd frontend && npx vitest run src/theme/tweaks.test.ts`
Expected: PASS (все кейсы).

- [ ] **Step 5: Wiring в App.tsx**

В `frontend/src/App.tsx` импорт (строки 27-29) добавить `applySkin, loadSkin`:
```ts
import {
  applyAccent, applyDensity, applyThemeMode, applySkin,
  loadAccent, loadDensity, loadThemeMode, loadSkin,
} from "./theme/tweaks";
```
В mount-effect (строки 82-86) добавить первой строкой:
```ts
    applySkin(loadSkin(getActiveId()));
```

- [ ] **Step 6: Проверка + Commit**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: PASS.
```bash
git add frontend/src/theme/tweaks.ts frontend/src/theme/tweaks.test.ts frontend/src/App.tsx
git commit -m "feat(theme): per-account skin (apple default) + applySkin wiring"
```

---

### Task 3: Селектор скина в Settings→ThemeTab

**Files:**
- Modify: `frontend/src/components/Settings.tsx:548-609` (ThemeTab)

**Interfaces:**
- Consumes: `SKINS`, `AppSkin`, `applySkin`, `loadSkin`, `saveSkin` (Task 2).
- Produces: UI-выбор «Стиль» с карточками Apple/Консоль.

- [ ] **Step 1: Импорт**

В шапке `Settings.tsx` в импорт из `../theme/tweaks` добавить `SKINS, type AppSkin, applySkin, loadSkin, saveSkin` (и иконки `Command, Terminal` из lucide, если нужны для карточек — иначе без иконок).

- [ ] **Step 2: Состояние + handler в ThemeTab**

Внутри `ThemeTab()` после `const [density,…]` добавить:
```ts
  const [skin, setSkin] = useState<AppSkin>(() => loadSkin(accountId));
  const pickSkin = (s: AppSkin) => { setSkin(s); applySkin(s); saveSkin(accountId, s); };
```

- [ ] **Step 3: Блок «Стиль» первым в разметке**

Первым дочерним блоком (перед блоком «Режим») вставить:
```tsx
      <div>
        <p className="micro" style={{ marginBottom: 10 }}>Стиль</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
          {SKINS.map(s => {
            const on = skin === s.key;
            return (
              <button key={s.key} onClick={() => pickSkin(s.key)} className="card"
                style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 7,
                  padding: "16px 8px", cursor: "pointer",
                  borderColor: on ? "var(--accent-line)" : "var(--line-soft)",
                  background: on ? "var(--accent-dim)" : "var(--bg2)",
                  color: on ? "var(--accent-hi)" : "var(--t-mid)",
                }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>{s.label}</span>
                <span style={{ fontSize: 11, color: "var(--t-low)" }}>
                  {s.key === "apple" ? "Системный вид macOS/iOS" : "Моноширинный, консольный"}
                </span>
              </button>
            );
          })}
        </div>
        <p className="hint">Apple — по умолчанию. «Консоль» возвращает моноширинный вид JetBrains Mono.</p>
      </div>
```

- [ ] **Step 4: Проверка визуально (playwright)**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Затем скриншот Settings→Тема (харнесс из Task 8, но можно разово): переключить Apple↔Консоль → шрифт/радиусы/тогглы меняются мгновенно.
Expected: tsc+vitest зелёные; при клике «Консоль» body-шрифт становится моноширинным, «Apple» — системным.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Settings.tsx
git commit -m "feat(settings): выбор скина Apple/Консоль в Тема"
```

---

### Task 4: Мобильный shell — ni-* хуки, BottomTabBar, drawer

**Files:**
- Modify: `frontend/src/App.tsx` (shell разметка)
- Create: `frontend/src/components/BottomTabBar.tsx`
- Modify: `frontend/src/components/Sidebar.tsx` (drawer-совместимость — опц. проп `onNavigate`)

**Interfaces:**
- Consumes: `Tab`, `Sidebar` (Sidebar.tsx).
- Produces: `<BottomTabBar activeTab onTabChange onMore />`; хуки-классы `ni-sidebar`/`ni-topbar`/`ni-main`/`ni-clock` в App; drawer-стейт `mobileNav`.

- [ ] **Step 1: Класс-хуки на shell в App.tsx**

- `<Sidebar …/>` обернуть НЕ нужно — вместо этого добавить `className="ni-sidebar"` внутри Sidebar (Step 4).
- `header` (строка 132): добавить `className="ni-topbar"`.
- Блок даты/статуса не имеет даты-часов; наш топбар показывает Remnawave-чип. Добавить `className="ni-clock"` на контейнер Remnawave-статуса (строка 144) — чтобы прятать на мобиле.
- `main` (строка 154): добавить `className="ni-main"`.

- [ ] **Step 2: Создать `BottomTabBar.tsx`**

```tsx
import { Activity, Rocket, ShieldCheck, Gauge, Menu } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Tab } from "./Sidebar";

const TABS: { tab: Tab; label: string; Icon: LucideIcon }[] = [
  { tab: "dashboard", label: "Статус", Icon: Activity },
  { tab: "deploy",    label: "Деплой", Icon: Rocket },
  { tab: "certs",     label: "SSL",    Icon: ShieldCheck },
  { tab: "traffic",   label: "Трафик", Icon: Gauge },
];

interface Props { activeTab: Tab; onTabChange: (t: Tab) => void; onMore: () => void; moreActive: boolean; }

export function BottomTabBar({ activeTab, onTabChange, onMore, moreActive }: Props) {
  const Tab_ = ({ Icon, label, active, onClick }:
    { Icon: LucideIcon; label: string; active: boolean; onClick: () => void }) => (
    <button onClick={onClick} style={{
      flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      gap: 3, padding: "7px 2px 4px", minHeight: 50,
      color: active ? "var(--accent)" : "var(--t-low)", transition: "color .12s",
    }}>
      <Icon size={21} />
      <span className="trunc" style={{ fontSize: 10, fontWeight: 600, lineHeight: 1 }}>{label}</span>
    </button>
  );
  return (
    <nav className="ni-tabbar" style={{
      position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 50,
      background: "var(--sidebar-bg)", borderTop: "1px solid var(--line-soft)",
      paddingBottom: "var(--safe-b)", paddingLeft: "var(--safe-l)", paddingRight: "var(--safe-r)",
      alignItems: "stretch",
    }}>
      {TABS.map(it => (
        <Tab_ key={it.tab} Icon={it.Icon} label={it.label}
          active={!moreActive && activeTab === it.tab} onClick={() => onTabChange(it.tab)} />
      ))}
      <Tab_ Icon={Menu} label="Ещё" active={moreActive} onClick={onMore} />
    </nav>
  );
}
```

- [ ] **Step 3: Drawer + BottomTabBar в App.tsx**

Импорт: `import { BottomTabBar } from "./components/BottomTabBar";`. Добавить стейт `const [mobileNav, setMobileNav] = useState(false);`.
Обернуть `onTabChange` в App так, чтобы навигация закрывала drawer: передать в Sidebar/BottomTabBar `(t) => { setTab(t); setMobileNav(false); }`.
Перед закрывающим `</div>` корня добавить:
```tsx
      {mobileNav && (
        <div className="ni-drawer" style={{ position: "fixed", inset: 0, zIndex: 55, display: "flex" }}>
          <div style={{ position: "absolute", inset: 0, background: "var(--overlay)", backdropFilter: "blur(2px)" }}
            onClick={() => setMobileNav(false)} />
          <div style={{ position: "relative", animation: "riseIn .18s ease-out" }}>
            <Sidebar activeTab={tab} onTabChange={(t) => { setTab(t); setMobileNav(false); }}
              collapsed={false} onToggle={() => {}} />
          </div>
        </div>
      )}
      <BottomTabBar activeTab={tab}
        onTabChange={(t) => { setTab(t); setMobileNav(false); }}
        onMore={() => setMobileNav(true)}
        moreActive={mobileNav || !["dashboard","deploy","certs","traffic"].includes(tab)} />
```
(`riseIn` уже определён? если нет — добавить keyframe в index.css: `@keyframes riseIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}`.)

- [ ] **Step 4: `ni-sidebar` класс на Sidebar**

В `Sidebar.tsx` на `<aside …>` (строка 62) добавить `className="ni-sidebar"`.

- [ ] **Step 5: Проверка (playwright, мобильный вьюпорт)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Скриншот-смоук 390×844: sidebar скрыт, `.ni-tabbar` виден снизу (4 таба + Ещё), клик «Ещё» открывает drawer с полной навигацией, клик по пункту закрывает drawer и переключает таб. Десктоп (1280): tabbar скрыт, sidebar виден.
Expected: поведение как описано; console clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/BottomTabBar.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat(mobile): bottom tab bar + drawer + ni-* shell hooks"
```

---

### Task 5: Page-хуки (ni-pagehead/ni-pagebody) + Dashboard reflow + grid/tbl фиксы

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx` (ni-health/ni-noderow/ni-node-name/ni-node-bars + ni-pagebody/ni-pagehead)
- Modify: `frontend/src/App.tsx:171` (certs grid адаптив)
- Modify: `frontend/src/components/DeployCard.tsx:702` (grid адаптив)
- Modify: страницы с широкими `.tbl` — обёртка `overflow-x:auto`: `TrafficRules.tsx`, `Hosts.tsx`, `infra/InfraServices.tsx`, `infra/InfraPayments.tsx`

**Interfaces:**
- Consumes: мобильные классы из Task 1.
- Produces: адаптивные заголовки/таблицы/сетки.

- [ ] **Step 1: Dashboard хуки**

В `Dashboard.tsx`: на внешний скролл-контейнер тела страницы добавить `className="ni-pagebody"` (или совместить с существующим). На health-баннер добавить `className="ni-health"`, на его блок статистики — `ni-health-stats`. На строку ноды добавить `ni-noderow`, на имя-блок — `ni-node-name`, на грид полосок аптайма — `ni-node-bars`. На заголовок страницы (если есть) — `ni-pagehead`.

- [ ] **Step 2: certs grid адаптив (App.tsx:171)**

Заменить `className="flex-1 grid grid-cols-[360px_1fr] min-h-0"` на `className="flex-1 grid grid-cols-1 lg:grid-cols-[360px_1fr] min-h-0"` (на мобиле форма и терминал в стек).

- [ ] **Step 3: DeployCard grid адаптив (DeployCard.tsx:702)**

Заменить `grid-cols-[260px_1fr]` на `grid-cols-1 sm:grid-cols-[260px_1fr]`.

- [ ] **Step 4: Обёртки для широких таблиц**

В `TrafficRules.tsx`, `Hosts.tsx`, `infra/InfraServices.tsx`, `infra/InfraPayments.tsx` — каждый `<table className="tbl">` обернуть в `<div style={{ overflowX: "auto" }}>…</div>` (или `className="overflow-x-auto"`). Только обёртка, таблицу не трогать.

- [ ] **Step 5: Проверка (playwright, 375px)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Скриншот Dashboard + certs + одна таблица на 375px: заголовки не переполняют, health-баннер переносится, таблицы скроллятся по горизонтали, certs-форма над терминалом.
Expected: без горизонтального переполнения body.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Dashboard.tsx frontend/src/App.tsx frontend/src/components/DeployCard.tsx frontend/src/components/TrafficRules.tsx frontend/src/components/Hosts.tsx frontend/src/components/infra/InfraServices.tsx frontend/src/components/infra/InfraPayments.tsx
git commit -m "feat(mobile): page-хуки, Dashboard reflow, grid/table адаптив"
```

---

### Task 6: Конвертация hardcoded-dark → токены, часть A (core-формы)

**Files (Modify):** `frontend/src/components/Settings.tsx`, `Templates.tsx`, `TrafficRules.tsx`, `MultiSelect.tsx`, `CountrySelect.tsx`

**Маппинг (Tailwind/rgba/hex → токен).** Применять как find-replace ПО КАЖДОМУ файлу, сверяя контекст:

| Из | В |
|---|---|
| `bg-gray-900` / `bg-gray-900/80` | `background:var(--bg2)` (inline) или класс `.input`/`.card` где подходит |
| `bg-gray-800` / `hover:bg-gray-800` | `var(--bg3)` |
| `bg-gray-700` / `hover:bg-gray-700` | `var(--bg3)` (hover) |
| `border-gray-700` / `border-gray-700/60/80` | `var(--line)` |
| `border-gray-800` | `var(--line-soft)` |
| `text-gray-100/200` | `var(--t-hi)` |
| `text-gray-300/400` | `var(--t-mid)` |
| `text-gray-500` | `var(--t-low)` |
| `text-gray-600` | `var(--t-faint)` |
| `bg-gray-800/20` (row hover) | `var(--row-hover)` |
| любой `#0d1117`/`#11…`/`#1a…` фон | `var(--bg1/2/3)` по роли |

Практический приём: где элемент — это карточка/инпут/кнопка/таблица, заменить Tailwind-классы на существующие классы `.card`/`.input`/`.selectbox`/`.btn .btn-soft`/`.tbl` (они уже на токенах). Где точечный цвет — inline `style={{…var(--…)}}`.

- [ ] **Step 1: MultiSelect.tsx (9 вхождений)**

Заменить hardcoded-dark на токены/классы по таблице. Кнопка-триггер → `className="input"` со стилями flex как в дизайн-прототипе (`ui.jsx` MultiSelect), панель → `background:var(--bg1);border:1px solid var(--line);box-shadow:var(--shadow-pop)`, чипы → `className="chip accent"`, строки → `className="navitem"`. Убрать фиксированный тёмный фон.

- [ ] **Step 2: CountrySelect.tsx (1) + проверить min-width**

Заменить единственный hardcoded цвет на токен. `min-w-[220px]` оставить на десктопе, но убедиться что дропдаун не переполняет 375px (при необходимости `max-width:100%`).

- [ ] **Step 3: Settings.tsx (10 — подформы Remnawave/Deploy/Optimization)**

Заменить hardcoded-dark в трёх подформах на токены/классы. Инпуты → `.input`, кнопки → `.btn .btn-*`, контейнеры-карточки → `.card`.

- [ ] **Step 4: Templates.tsx (24)**

То же по таблице. Список шаблонов/редактор → `.card`/`.input`; кнопки → `.btn`.

- [ ] **Step 5: TrafficRules.tsx (35)**

То же. Модалка → уже `.overlay`/`.modal`? если своя — перевести на токены; таблица → `.tbl`; инпуты → `.input`.

- [ ] **Step 6: Проверка (playwright, матрица light)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Скриншоты каждого экрана на apple-light И console-light: НЕТ тёмных островов; на dark — визуал не деградировал.
Expected: формы читаемы в light; dark без регресса.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/Settings.tsx frontend/src/components/Templates.tsx frontend/src/components/TrafficRules.tsx frontend/src/components/MultiSelect.tsx frontend/src/components/CountrySelect.tsx
git commit -m "refactor(theme): конвертация core-форм на токены (Settings/Templates/Traffic/MultiSelect)"
```

---

### Task 7: Конвертация hardcoded-dark → токены, часть B (infra/*)

**Files (Modify):** `frontend/src/components/infra/InfraDashboard.tsx`, `InfraProviders.tsx`, `InfraProjects.tsx`, `InfraServices.tsx`, `InfraPayments.tsx`, `InfraSettings.tsx`, `InfraApiTokens.tsx`, `infra/ui.tsx`, `infra/Toast.tsx`

**Interfaces:** та же таблица маппинга, что в Task 6.

- [ ] **Step 1: infra/ui.tsx (6) + Toast.tsx (1)**

Общие примитивы (Page/PageHeader/Field/Modal/fmt) — перевести на токены/классы. `ni-pagebody`/`ni-pagehead` хуки добавить в местные Page/PageHeader (чтобы все 7 infra-страниц получили адаптив разом).

- [ ] **Step 2: InfraDashboard.tsx (19 + 6 hex)**

Донат/линия уже на `var(--…)`? заменить оставшиеся hardcoded. Виджеты баланса/burn → `.card`.

- [ ] **Step 3: InfraProviders.tsx (20), InfraApiTokens.tsx (14)**

По таблице.

- [ ] **Step 4: InfraServices.tsx (13), InfraPayments.tsx (13)**

По таблице; таблицы уже обёрнуты в Task 5.

- [ ] **Step 5: InfraProjects.tsx (11), InfraSettings.tsx (4)**

По таблице.

- [ ] **Step 6: Проверка (playwright, все infra на light)**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Скриншоты 7 infra-страниц на apple-light/console-light: цельная тема; dark без регресса; мобильный вид (ni-pagehead в колонку).
Expected: без тёмных островов.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/infra/
git commit -m "refactor(theme): конвертация infra/* на токены + page-хуки"
```

---

### Task 8: Скриншот-матрица, регресс, полировка

**Files:**
- Create/Modify: `frontend/tests/e2e/theme-shots.mjs` (расширить существующий харнесс)

**Interfaces:** финальная верификация всего.

- [ ] **Step 1: Расширить харнесс `theme-shots.mjs`**

Матрица: skin∈{apple,console} × mode∈{light,dark} × viewport∈{desktop 1280×860, mobile 390×844}. Экраны: Dashboard, Deploy-форма (модалка нового сервера — bottom-sheet на mobile), Settings→Тема, Hosts, одна infra-страница (InfraProviders). Установка скина/режима — через `localStorage.setItem("ni_skin_<id>", …)` + `ni_thememode_<id>` до загрузки, либо клики в Settings. Сохранять в `tests/e2e/shots/matrix/<skin>-<mode>-<vp>-<screen>.png`.

- [ ] **Step 2: Прогнать матрицу**

Run: `cd frontend && node tests/e2e/theme-shots.mjs` (vite поднять на `--host 127.0.0.1`, `waitUntil:"commit"`, ждать маркер).
Expected: все PNG сгенерированы без ошибок консоли.

- [ ] **Step 3: Ревью скриншотов**

Просмотреть матрицу: (а) console-скриншоты совпадают с дореформенными (регресс-0); (б) apple-light/dark — цельные, SF-шрифт, pill-nav, iOS-тоггл зелёный; (в) mobile — tabbar снизу, sidebar скрыт, модалка = bottom-sheet, нет горизонтального скролла body.
Зафиксировать найденные дефекты и починить точечно.

- [ ] **Step 4: Полный прогон**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: всё зелёное.

- [ ] **Step 5: Обновить CLAUDE.md**

В §2 (Frontend Behavior) обновить абзац про тему: добавить ось `data-skin` (apple|console, дефолт apple, `ni_skin_<id>`), Apple-скин (SF-шрифт/iOS-контролы/glass), мобильную версию (bottom tab bar `.ni-tabbar`, drawer, bottom-sheet модалки, `ni-*` хуки, media 820/600, safe-area). Отметить, что hardcoded-dark компоненты конвертированы на токены (список «still hardcoded-dark» в §6 удалить/сократить). В §6 обновить «MultiSelect still hardcoded-dark».

- [ ] **Step 6: Commit**

```bash
git add frontend/tests/e2e/theme-shots.mjs frontend/tests/e2e/shots CLAUDE.md
git commit -m "test(theme): скриншот-матрица skin×mode×viewport + обновление CLAUDE.md"
```

---

## Self-Review (заполняется при написании — уже проверено)

- **Покрытие спеки:** ось skin (T2), CSS-порт apple+mobile (T1), селектор (T3), мобильный shell/навигация (T4), page/table/grid адаптив (T5), конвертация core (T6) + infra (T7), скриншот-матрица + CLAUDE.md (T8). Все компоненты спеки покрыты.
- **Плейсхолдеры:** нет TBD; конвертация задана таблицей маппинга + пофайловым перечнем с числом вхождений (конкретное «как»).
- **Типы:** `AppSkin`/`applySkin`/`loadSkin`/`saveSkin`/`SKINS` — единые имена во всех задачах (T2 определяет, T3 потребляет). `Tab` переиспользуется из `Sidebar.tsx` в `BottomTabBar`.
