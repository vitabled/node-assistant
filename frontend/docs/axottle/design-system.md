# Дизайн-система Axottle — снято с живой панели v1.8.3

Источник: локально развёрнутая панель Axottle v1.8.3 (`http://127.0.0.1:8123`), её собственный
CSS (2 чанка, 255 КБ) и строковые таблицы её JS-чанков. Как развёрнута — `deploy_panel.md`.
Машинный снимок всех значений — `tokens.json` (пересобирается `../extract_design.py`).

Всё, что ниже, — **измеренные значения панели**, не реконструкция по скриншотам.

---

## 1. Оси оформления

Панель — не «тема», а **три независимые оси**, выставленные атрибутами на `<html>` и на корневом
контейнере `[data-axo-new]`:

| Ось | Атрибут | Значения | По умолчанию |
|---|---|---|---|
| Режим | `data-axo-mode` | `light`, `dark` | `light` (в разметке панели — `dark`) |
| Палитра | `data-axo-palette` | *(нет атрибута)* = **blush**, `mint` | blush |
| Стиль | `data-style` | `cloud` (скругления + тени), `sharp` (прямые углы, теней нет) | cloud |

Корневой атрибут `data-axo-new` включает новую систему целиком: он же задаёт фон, шрифт,
`font-feature-settings:"cv11","ss01","ss03"`, подсветку выделения, скроллбар и фокус-кольцо.
Старая (`--color-*`) система остаётся как совместимый слой — режим прокидывает в неё значения:
`--color-bg: var(--axo-bg); --color-surface: var(--axo-surface); …`.

Переключение оси в живом рендере проверено: скриншоты `screenshots/panel-*.png` сняты
перестановкой этих же атрибутов.

## 2. Палитры

Четыре набора (2 палитры × 2 режима). Полные наборы — `tokens.json → tokens.palettes`;
ниже ядро.

### blush · light (базовая, «панельная»)

| Токен | Значение | Роль |
|---|---|---|
| `--axo-bg` | `#FBF4F4` | холст |
| `--axo-bg-elev` | `#FDFBFB` | поднятый холст |
| `--axo-surface` | `#FFFFFF` | карточки, поля, попапы |
| `--axo-surface-2` | `#FCF5F5` | подложка, hover |
| `--axo-surface-3` | `#F7EBEB` | третий уровень / дорожки |
| `--axo-border` | `#F2E3E3` | основная граница |
| `--axo-border-strong` | `#E3CCCC` | граница полей и «сильных» контуров |
| `--axo-text` | `#1A1313` | заголовки и значения |
| `--axo-text-muted` | `#6B5B5B` | основной текст |
| `--axo-text-faint` | `#9A8B8B` | мета, placeholder, подписи |
| `--axo-accent` | `#C82C3C` | акцент (кнопка, активная вкладка) |
| `--axo-accent-hover` | `#AC2433` | hover акцента |
| `--axo-accent-soft` | `#FDECEE` | фон акцентного чипа |
| `--axo-accent-text` | `#FFFFFF` | текст НА акценте |

### blush · dark

| Токен | Значение |
|---|---|
| `--axo-bg` | `#0A0708` |
| `--axo-surface` / `-2` / `-3` | `#181113` / `#1E1618` / `#251B1E` |
| `--axo-border` / `--axo-border-strong` | `#281D20` / `#35272A` |
| `--axo-text` / `-muted` / `-faint` | `#F2EBEB` / `#A99C9C` / `#776C6C` |
| `--axo-accent` / `-hover` | `#FC9CA1` / `#FFB4B8` |
| `--axo-accent-soft` | `rgba(252,156,161,0.14)` |
| `--axo-accent-text` | `#2A0E10` |

### mint · light

| Токен | Значение |
|---|---|
| `--axo-bg` / `-elev` | `#F1F6F4` / `#FBFCFC` |
| `--axo-surface` / `-2` / `-3` | `#FFFFFF` / `#F6FAF8` / `#EEF4F1` |
| `--axo-border` / `--axo-border-strong` | `#E3ECE7` / `#CFDBD4` |
| `--axo-text` / `-muted` / `-faint` | `#0F1A16` / `#5B6B65` / `#8A9893` |
| `--axo-accent` / `-hover` / `-soft` | `#0E8E72` / `#0B7861` / `#DCEFE7` |

### mint · dark

| Токен | Значение |
|---|---|
| `--axo-bg` / `-elev` | `#07090C` / `#0B0F14` |
| `--axo-surface` / `-2` / `-3` | `#11161D` / `#161C25` / `#1C2330` |
| `--axo-border` / `--axo-border-strong` | `#1E2632` / `#2A3340` |
| `--axo-text` / `-muted` / `-faint` | `#EAF1ED` / `#9AA8A2` / `#6A7770` |
| `--axo-accent` / `-hover` / `-soft` | `#34D8A8` / `#5BE5B7` / `rgba(52,216,168,0.12)` |
| `--axo-accent-text` | `#06120E` |

⚠️ `--axo-text-faint` в тёмных палитрах (`#776C6C`, `#6A7770`) на своих поверхностях даёт
**3.6–3.9 : 1** — ниже AA 4.5 для обычного текста. Это осознанный размен панели (мета-текст),
но у NA есть измеримый контрастный гейт (`frontend/src/theme/contrast.test.ts`), поэтому при
переносе faint берётся светлее/темнее до 4.5 **и расхождение фиксируется**, а не замалчивается.

### Семантика статусов

| Роль | blush light | blush dark | mint light | mint dark |
|---|---|---|---|---|
| ok | `#0E7A52` | `#3FCB8E` | `#16A07A` | `#34D8A8` |
| warn | `#8A5F05` | `#F1B748` | `#C58A12` | `#F1B748` |
| crit | `#AF4519` | `#F97A45` | `#D14B4B` | `#FF6B6B` |
| info | `#2264A8` | `#6BB7F1` | `#2F7AB8` | `#6BB7F1` |
| mute | `#9A8B8B` | `#776C6C` | `#8A9893` | `#6A7770` |

У каждой роли есть парный `*-soft` (фон чипа) и, у crit, `--axo-status-crit-text` (текст на
сплошном crit). В тёмных режимах `soft` — это `rgba(...,0.14–0.16)`, в светлых — плотный hex.

### Графики

`--axo-chart-1..6` (light: `#0E8E72 #2A78D6 #EDA100 #6B4FD8 #E34948 #EB6834`; dark: приглушённая
версия `#199E70 #3987E5 #C98500 #9085E9 #E66767 #D95926`) + `--axo-chart-track`,
`--axo-chart-grid`.

## 3. Структурные токены

```
Радиусы:  xs 8  · sm 12 · md 16 · lg 20 · xl 24 · 2xl 28 · pill 999   (px)
Отступы:  1:4  2:8   3:12  4:16  5:20  6:24  8:32  10:40  12:48      (px)
Cтиль cloud: --radius-card 1.25rem(20) --radius-btn .75rem(12) --radius-input .75rem(12) --radius-panel 1.5rem(24)
Стиль sharp: --radius-panel 0 --shadow-panel none --panel-gap 0
Тени (cloud, dark): sm 0 1px 2px rgba(0,0,0,.3) · md 0 4px 16px rgba(0,0,0,.35),0 1px 2px rgba(0,0,0,.25)
                    lg 0 12px 40px rgba(0,0,0,.45),0 2px 6px rgba(0,0,0,.3) · xl 0 24px 64px rgba(0,0,0,.55)
Тени (cloud, light): sm 0 1px 2px rgba(26,19,19,.04),0 1px 1px rgba(26,19,19,.03)
                     md 0 4px 16px rgba(26,19,19,.05),0 1px 2px rgba(26,19,19,.03)
                     lg 0 12px 40px rgba(26,19,19,.08),0 2px 6px rgba(26,19,19,.04)
                     xl 0 24px 64px rgba(26,19,19,.12)
Фокус:     --axo-shadow-glow: 0 0 0 4px <accent @ .12 (light) / .18 (dark)>
Hover акцента: --axo-shadow-accent-hover: 0 6px 18px <accent @ .20–.25>
Easing:    --axo-ease: cubic-bezier(0.22,1,0.36,1)   ← входы/«дыхание»
           --axo-ease-out: cubic-bezier(0.16,1,0.3,1)
Длительности (из переходов): .15s · .18s · .2s · .3s · .5s
```

Шрифты: `--axo-font-sans: "Google Sans","Inter","Manrope","Geist",ui-sans-serif,system-ui,…`,
`--axo-font-mono: "Google Sans Code","JetBrains Mono","Geist Mono",ui-monospace,…`.
Веса: 400 / 500 / 600 / 700 (у кнопок и акцентов — `font-medium` = 500).
Логотип-вордмарк — отдельная гарнитура: `"Cause","Avenir Next"`, 27px/600, `letter-spacing:-.045em`.

## 4. Компонентный слой

### 4.1 Именованные классы панели (реальные правила из CSS)

| Класс | Правило |
|---|---|
| `.axo-card` | `background:var(--axo-surface); border:1px solid var(--axo-border); border-radius:var(--axo-radius-xl); box-shadow:var(--axo-shadow-sm)` |
| `.axo-card-elevated` | то же, но `box-shadow:var(--axo-shadow-md)` |
| `.axo-card-flat` | `background:var(--axo-surface-2); border:1px solid var(--axo-border); border-radius:var(--axo-radius-lg)` |
| `.axo-divider` | `height:1px; width:100%; background:var(--axo-border)` |
| `.axo-input` | `appearance:none; background:var(--axo-field-bg, var(--axo-surface)); background-clip:padding-box; caret-color:var(--axo-text)` + `color-scheme` по режиму |
| `.axo-focus-ring:focus-visible` | `outline:none; box-shadow:var(--axo-shadow-glow)` |
| `.axo-skeleton` | `linear-gradient(90deg, surface-2 0, surface-3 50%, surface-2 100%)`, `background-size:800px 100%`, `animation: axo-shimmer 1.4s linear infinite`, `radius: radius-sm` |
| `.axo-hit::after` | невидимая зона нажатия: `max(100%,44px) × max(100%,44px)` по центру — **все мелкие кнопки бьют в 44px** |
| `.axo-pulse-ok` / `.axo-pulse-crit` | `animation: axo-pulse-ok 2.4s var(--axo-ease) infinite` / `axo-pulse-crit 1.6s`, и оба гасятся `animation:none!important` в режиме без анимаций |
| `.axo-app-bg` | фон приложения: два радиальных «свечения» (`--axo-fx-1`, `--axo-fx-2`) поверх `--axo-bg` |
| `.axo-sheet-grab` | «язычок» мобильного bottom-sheet: 36×4, `radius:999`, `background:border-strong` |
| `.axo-brand-wordmark`, `.axo-provider-logo`, `.axo-scroll-x` | вордмарк; подложка логотипа провайдера (`--axo-logo-bg/--axo-logo-border`); горизонтальный скролл-контейнер |

Плюс целый блок для встроенного ИИ-ассистента (Axosun): `.axo-chat-user-bubble`
(`border-radius:16px 16px 5px 16px` — «скошенный» уголок пользователя, градиент accent-soft→surface),
`.axo-chat-message-agent` (вертикальная линия акцента слева), `.axo-inline-approval` (карточка
подтверждения опасного действия с `data-risk=critical|destructive|high`),
`.axo-plan-branch`, `.axo-agent-clarification*`, `.axo-agent-access-option[data-mode=…]`.
Это самый интересный для нас слой — интерфейс «агент спрашивает разрешение».

### 4.2 Рецепты на Tailwind-утилитах (вынуты из JS-чанков панели)

Кнопка, база (одинакова для всех вариантов):
`axo-focus-ring inline-flex items-center justify-center gap-2 font-medium select-none whitespace-nowrap cursor-pointer transition-[background,color,border-color,box-shadow] duration-200 ease-out disabled:cursor-not-allowed disabled:opacity-50 disabled:pointer-events-none`

| Вариант | Остаток рецепта |
|---|---|
| **primary** | `bg-[var(--axo-accent)] text-[var(--axo-accent-text)] hover:bg-[var(--axo-accent-hover)] shadow-[0_1px_2px_rgba(0,0,0,0.06)] hover:shadow-[var(--axo-shadow-accent-hover)]` |
| **primary L** | `h-12 px-5 text-[15px] rounded-[16px]` |
| **primary M** | `h-10 px-4 text-sm rounded-[14px]` |
| **secondary** | `h-9 px-3 text-[13px] rounded-[12px] border border-[var(--axo-border-strong)] text-[var(--axo-text)] hover:bg-[var(--axo-surface-2)]` |
| **icon** | `h-10 w-10 rounded-[12px] border border-[var(--axo-border)] bg-[var(--axo-surface)] text-[var(--axo-text)] hover:bg-[var(--axo-surface-2)]` |
| **icon ghost (mobile)** | `h-11 w-11 rounded-full border-0 bg-transparent text-[var(--axo-text-muted)] hover:bg-[var(--axo-accent-soft)] hover:text-[var(--axo-text)]` |
| **micro** | `h-8 px-2.5 rounded-[10px] text-[11px] font-medium uppercase tracking-wider text-[var(--axo-text-muted)] hover:text-[var(--axo-text)] hover:bg-[var(--axo-surface-2)]` |

Поле ввода:
`axo-input axo-focus-ring w-full bg-[var(--axo-field-bg)] border border-[var(--axo-border-strong)] text-[var(--axo-text)] placeholder:text-[var(--axo-text-faint)] transition-[border-color,box-shadow,background] duration-200 ease-out hover:border-[var(--axo-text-faint)] focus:border-[var(--axo-accent)] disabled:opacity-60 h-10 rounded-[14px] text-sm px-4`
(вариант с иконкой — `pl-10`; textarea/select в диалогах — `min-h-[44px] lg:min-h-[42px] rounded-[12px] px-3 text-[16px] lg:text-[13px] focus:border-accent focus:shadow-[0_0_0_3px_var(--axo-accent-soft)]`).

Прочее:

* **Попап/меню:** `absolute right-0 top-[calc(100%+8px)] z-50 w-48 rounded-[14px] border border-[var(--axo-border)] bg-[var(--axo-surface)] shadow-[var(--axo-shadow-md)] overflow-hidden`; широкий вариант — `w-[min(390px,calc(100vw-24px))] rounded-[18px] shadow-[var(--axo-shadow-lg)] z-[80]`.
* **Футер диалога:** `px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4 border-t border-[var(--axo-border)] bg-[var(--axo-surface-2)]/40 flex flex-wrap items-center justify-end gap-2 max-sm:[&>button]:flex-1`.
* **Алерт crit:** `rounded-[14px] border border-[var(--axo-status-crit)]/30 bg-[var(--axo-status-crit-soft)] text-[var(--axo-status-crit)]` (граница — тот же цвет на 30 %).
* **Алерт warn:** `rounded-[14px] border border-[var(--axo-status-warn)]/40 bg-[var(--axo-status-warn-soft)] p-3 text-[12px] leading-relaxed inline-flex items-start gap-2.5`.
* **Чип активный / неактивный:** `bg-[var(--axo-accent-soft)] text-[var(--axo-accent)] border-transparent` ↔ `bg-[var(--axo-surface-2)] text-[var(--axo-text)] border-[var(--axo-border)]`; статусные чипы — `bg-[var(--axo-status-ok-soft)] text-[var(--axo-status-ok)] border-transparent`.
* **Код-строка:** `rounded-[8px] border border-[var(--axo-border)] bg-[var(--axo-surface)] px-2.5 py-2 text-[12px] font-mono whitespace-pre-wrap break-all`.
* **Лог/терминал:** `text-[11px] font-mono rounded-[12px] bg-[var(--axo-surface-2)] border border-[var(--axo-border)] p-3 max-h-[360px] overflow-y-auto`.
* **Мелкая ссылка-действие:** `axo-hit text-[11px] font-medium text-[var(--axo-text-muted)] hover:text-[var(--axo-accent)] inline-flex items-center gap-1 whitespace-nowrap`.
* **Панель-таблица (сегменты доступа агента):** `grid grid-cols-3 gap-3 p-3 rounded-[12px] border border-[var(--axo-border)] bg-[var(--axo-surface-2)]/70`, активный сегмент — `background:var(--axo-surface); box-shadow:var(--axo-shadow-sm)`.

### 4.3 Ключевые кадры

`axo-shimmer` (скелетоны), `axo-pulse-ok` / `axo-pulse-crit` (живые индикаторы), `axo-fade-in`,
`axo-slide-up`, `axo-edge-pop` / `axo-edge-menu-in` / `axo-edge-tip-in` / `axo-edge-dot`
(«кромка»/тултипы), `axo-agent-exterior-breathe` (свечение вокруг окна агента).

## 5. Типографика и плотность

* База UI — `text-sm` (13–14px), мета — 11–12px, микроподписи — 10–10.5px, кнопка панели —
  13–15px по размеру. Крупные заголовки: шаг визарда `text-[28px] sm:text-[36px] font-semibold tracking-tight`.
* Аптайт-«капс» только у микро-кнопок: `text-[11px] uppercase tracking-wider`.
* Мобильный ввод намеренно крупнее десктопного (`text-[16px] lg:text-[13px]`) — иначе iOS
  зумит страницу при фокусе.
* Плотность задаётся не отдельным токеном, а стилем (`--panel-gap` 12px в cloud → 0 в sharp)
  и размерами контролов (`h-8`/`h-10`/`h-12`).

## 6. Поведение и доступность (что панель делает системно)

1. **Фокус — тень, а не обводка:** `outline:none; box-shadow: 0 0 0 4px accent/12–18%`. Один
   класс `axo-focus-ring` на все интерактивные элементы.
2. **44px-зона нажатия:** `axo-hit` растягивает псевдоэлемент до 44×44 — мелкая иконка
   остаётся мелкой визуально, но промахнуться нельзя.
3. **Уважение к «без анимаций»:** пульсации гасятся `animation:none!important`.
4. **Мобильные мелочи:** `pb-[max(0.75rem,env(safe-area-inset-bottom))]` в футерах диалогов,
   `max-sm:[&>button]:flex-1` — кнопки на всю ширину, bottom-sheet с «язычком».
5. **Прозрачные поверхности** считаются через `color-mix(in srgb, var(--axo-surface) 84%, transparent)`
   — тема меняется, а «стекло» остаётся согласованным.

## 7. Что берём в NA (карта переноса)

| Axo | NA сейчас | Куда ложится |
|---|---|---|
| `--axo-bg` / `-elev` | `--bg0` / `--bg1` | skin `axottle` переопределяет существующие имена → все экраны подхватывают без правок JSX |
| `--axo-surface` / `-2` / `-3` | `--bg2` / `--bg3` / `--row-hover` | то же |
| `--axo-border` / `-strong` | `--line` / `--line-soft` | то же |
| `--axo-text` / `-muted` / `-faint` | `--t-hi` / `--t-mid` / `--t-low` / `--t-faint` | то же (+ контрастный гейт) |
| `--axo-accent*` | `--accent*` (инлайн из `applyAccent`) | новый акцент `axottle` в `ACCENTS` |
| `--axo-status-*` | `--ok/--warn/--err/--info-*` | то же |
| `--axo-radius-*` | `--r-sm/md/lg` | 12 / 20 / 24 (+ `--r-2xl` 28, `--r-pill`) |
| `--axo-shadow-*` | `--shadow-pop/--shadow-modal` | cloud-значения |
| `--axo-font-sans/mono` | `--font/--mono` | Google Sans/Inter/Manrope + Google Sans Code/JetBrains |
| `data-axo-mode` | `data-theme` (есть) | режим NA переиспользуется |
| `data-axo-palette` | — (нет) | новая ось: `blush` (по умолчанию) / `mint` |
| `data-style` | — (нет) | новая ось: `cloud` (по умолчанию) / `sharp` |
| `.axo-*` классы | свои `.btn/.input/.card/…` | axo-классы добавляются рядом, старые не ломаются |

## 8. Пробелы (честно)

* Внутренние экраны панели (дашборд, ноды, таблицы, настройки) **не отрендерены** — за
  лицензионным ключом. Их разметка есть в ассетах, но живой рендер не снят.
* Точные правила `.axo-*` для агентского окна сняты полностью; экранные композиции (сетки
  страниц) — нет.
* Панель обфусцирована (`javascript-obfuscator 5.5.1`), имена внутренних компонентов
  недоступны — поэтому выше описан **слой токенов и рецептов**, а не структура кода.
