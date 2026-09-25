/**
 * Axottle UI kit — базовый набор компонентов дизайн-системы Axottle для NA.
 *
 * Источник дизайна: публичный CSS Axottle (`/root/work/axottle_transfer/design/all.css`),
 * разбор — `design-system.md` + `tokens.json` рядом с ним. Стили живут в `src/theme/axottle.css`
 * (классы `.axo-*`), токены — в `tailwind.config.ts`. Компоненты намеренно тонкие: разметка
 * и классы, без своей логики, чтобы новый экран собирался из них, а не из инлайн-стилей.
 *
 * Почему отдельный файл, а не правка `ui.tsx`: `ui.tsx` уже читают 18 файлов панели и покрыт
 * тестами; этот кит — дополнение (кнопка/поле/карточка/таблица/статус/табы/диалог/тост/скелетон/
 * пустое состояние/крошки/каркас/переключатель темы). Существующий кит переезжает на дизайн
 * Axottle через токены скина `axottle` (`data-skin`), без правок JSX.
 *
 * ⚠️ Цвета — только токенами (`var(--…)` через классы `.axo-*` или arbitrary-значения Tailwind).
 * В `src/theme/contrast.test.ts` есть гейт, который запрещает `text-gray-500`-подобные классы:
 * такие игнорируют тему и акцент.
 */
import {
  createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore,
  type ButtonHTMLAttributes, type CSSProperties, type InputHTMLAttributes, type ReactNode,
  type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from "react";
import { Check, ChevronRight, Info, Search, TriangleAlert, X } from "lucide-react";
import { applyThemeMode, saveThemeMode, type ThemeMode } from "./tweaks";
import { getActiveId } from "../auth/store";

export type AxoTone = "neutral" | "success" | "warning" | "danger" | "info";
const TONE_CLASS: Record<AxoTone, string> = {
  neutral: "", success: "tone-success", warning: "tone-warning",
  danger: "tone-danger", info: "tone-info",
};

/* ── Статус/бейдж — .platform-status (+ .tone-*): пилюля с точкой в цвет currentColor ── */
export function AxoStatus(
  { tone = "neutral", dot = true, children, className = "", style }:
  { tone?: AxoTone; dot?: boolean; children: ReactNode; className?: string; style?: CSSProperties },
) {
  return (
    <span className={`axo-status ${dot ? "" : "no-dot"} ${TONE_CLASS[tone]} ${className}`.trim()} style={style}>
      {children}
    </span>
  );
}

/* ── Кнопка — .platform-button: сплошная акцентная и «тихая», hover с подъёмом на 1px ── */
export type AxoBtnVariant = "primary" | "quiet" | "ghost" | "danger";
export type AxoBtnSize = "sm" | "md" | "lg";

export function AxoButton(
  { variant = "primary", size = "md", icon, children, className = "", ...rest }:
  { variant?: AxoBtnVariant; size?: AxoBtnSize; icon?: ReactNode } &
  ButtonHTMLAttributes<HTMLButtonElement>,
) {
  const cls = [
    "axo-btn",
    variant !== "primary" ? `is-${variant}` : "",
    size !== "md" ? `is-${size}` : "",
    className,
  ].filter(Boolean).join(" ");
  return (
    <button type="button" {...rest} className={cls}>
      {icon}
      {children}
    </button>
  );
}

/** Иконочная кнопка (38×38, радиус 11px) — `.axo-btn.is-icon`. */
export function AxoIconButton({ icon, label, ...rest }: { icon: ReactNode; label: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" aria-label={label} title={label} {...rest} className="axo-btn is-icon">
      {icon}
    </button>
  );
}

/* ── Поля ── */
export function AxoField(
  { label, hint, error, htmlFor, children }:
  { label?: ReactNode; hint?: ReactNode; error?: ReactNode; htmlFor?: string; children: ReactNode },
) {
  return (
    <label className="axo-field" htmlFor={htmlFor}>
      {label && <span>{label}</span>}
      {children}
      {error ? <span className="axo-errmsg">{error}</span> : hint ? <span className="axo-hint">{hint}</span> : null}
    </label>
  );
}

export function AxoInput({ invalid, className = "", ...rest }: { invalid?: boolean } & InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={`axo-input ${invalid ? "is-err" : ""} ${className}`.trim()} />;
}

export function AxoTextarea({ className = "", ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={`axo-textarea ${className}`.trim()} />;
}

export function AxoSelect(
  { options, placeholder, className = "", ...rest }:
  { options: { value: string; label: string; disabled?: boolean }[]; placeholder?: string } &
  SelectHTMLAttributes<HTMLSelectElement>,
) {
  return (
    <select {...rest} className={`axo-select-native ${className}`.trim()}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map(o => (
        <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
      ))}
    </select>
  );
}

export function AxoCheckbox({ label, ...rest }: { label: ReactNode } & InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="axo-check">
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}

/** Свитч — роль switch вместо нативного чекбокса: дорожка 34×19, ползунок 11px. */
export function AxoSwitch(
  { checked, onChange, label, disabled, id }:
  { checked: boolean; onChange: (next: boolean) => void; label?: ReactNode; disabled?: boolean; id?: string },
) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
      <button
        type="button" role="switch" id={id} aria-checked={checked} disabled={disabled}
        className="axo-switch" onClick={() => onChange(!checked)}
      />
      {label && <span style={{ color: "var(--t-low)", fontSize: 13 }}>{label}</span>}
    </span>
  );
}

/* ── Карточка — .platform-card: рамка 1px, радиус 18px, влёт card-in .45s ── */
export function AxoCard(
  { title, subtitle, icon, actions, footer, interactive, className = "", children }:
  { title?: ReactNode; subtitle?: ReactNode; icon?: ReactNode; actions?: ReactNode; footer?: ReactNode;
    interactive?: boolean; className?: string; children?: ReactNode },
) {
  return (
    <section className={`axo-card ${interactive ? "is-interactive" : ""} ${className}`.trim()}>
      {(title || icon) && (
        <header className="axo-card-head">
          {icon && <span className="axo-card-icon">{icon}</span>}
          <div style={{ minWidth: 0, flex: 1 }}>
            <h2>{title}</h2>
            {subtitle && <small>{subtitle}</small>}
          </div>
          {actions}
        </header>
      )}
      {children && <div className="axo-card-body">{children}</div>}
      {footer && <footer className="axo-card-foot">{footer}</footer>}
    </section>
  );
}

/* ── Таблица — .data-table: моно-шапка капсом, строки 68px, hover ── */
export function AxoTable(
  { head, children, className = "", caption }:
  { head: ReactNode[]; children: ReactNode; className?: string; caption?: string },
) {
  return (
    <div className={`axo-table-wrap ${className}`.trim()}>
      <table className="axo-table">
        {caption && <caption className="axo-hint" style={{ padding: "8px 16px", textAlign: "left" }}>{caption}</caption>}
        <thead>
          <tr>{head.map((h, i) => <th key={i} scope="col">{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ── Табы — .platform-filter-pills: активная пилюля фоном, без подчёркиваний ── */
export function AxoTabs<T extends string>(
  { tabs, value, onChange, accent, className = "" }:
  { tabs: { value: T; label: ReactNode; icon?: ReactNode }[]; value: T; onChange: (v: T) => void;
    accent?: boolean; className?: string },
) {
  return (
    <div className={`axo-tabs ${accent ? "is-accent" : ""} ${className}`.trim()} role="tablist">
      {tabs.map(t => (
        <button
          key={t.value} type="button" role="tab" aria-selected={t.value === value}
          className={t.value === value ? "is-active" : ""} onClick={() => onChange(t.value)}
        >
          {t.icon}
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ── Модалка/диалог — .platform-dialog: слой с blur(6px), панель 430/560px, радиус 20px ── */
export function AxoDialog(
  { open, onClose, title, text, icon, wide, footer, children }:
  { open: boolean; onClose: () => void; title: ReactNode; text?: ReactNode; icon?: ReactNode;
    wide?: boolean; footer?: ReactNode; children?: ReactNode },
) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="axo-dialog-layer" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`axo-dialog ${wide ? "is-wide" : ""}`.trim()} role="dialog" aria-modal="true">
        <button type="button" className="axo-dialog-close" aria-label="Закрыть" onClick={onClose}><X size={16} /></button>
        {icon && <span className={`axo-dialog-icon ${title && !text ? "" : ""}`.trim()}>{icon}</span>}
        <h2 style={{ marginTop: icon ? 19 : 0 }}>{title}</h2>
        {text && <p>{text}</p>}
        {children}
        {footer && <div className="axo-dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

/** Диалог подтверждения — тот же .axo-dialog с опасным тоном. */
export function AxoConfirm(
  { open, onClose, onConfirm, title, text, confirmLabel = "Подтвердить", cancelLabel = "Отмена", danger }:
  { open: boolean; onClose: () => void; onConfirm: () => void; title: ReactNode; text?: ReactNode;
    confirmLabel?: string; cancelLabel?: string; danger?: boolean },
) {
  return (
    <AxoDialog
      open={open} onClose={onClose} title={title} text={text} wide={false}
      icon={danger ? <TriangleAlert size={22} /> : <Info size={22} />}
      footer={<>
        <AxoButton variant="quiet" onClick={onClose}>{cancelLabel}</AxoButton>
        <AxoButton variant={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</AxoButton>
      </>}
    />
  );
}

/* ── Тосты — .ConnectionCanvasDemo_toast: одна стопка, width min(430px, 100vw-24px) ──
   Императивный API: `axoToast({ tone, title, text })` из любого места. Хост рендерит стопку и
   не создаёт DOM, пока стопка пуста. */
export interface AxoToastItem { id: number; tone: AxoTone; title: string; text?: string }
let _toasts: AxoToastItem[] = [];
let _seq = 0;
const _subs = new Set<() => void>();
const _emit = () => { _subs.forEach(f => f()); };
const _subscribe = (f: () => void) => { _subs.add(f); return () => { _subs.delete(f); }; };
const _snapshot = () => _toasts;

export function axoToast(t: { tone?: AxoTone; title: string; text?: string; ttl?: number }): number {
  const id = ++_seq;
  _toasts = [..._toasts, { id, tone: t.tone ?? "neutral", title: t.title, text: t.text }];
  _emit();
  const ttl = t.ttl ?? 5000;
  if (ttl > 0) setTimeout(() => axoDismiss(id), ttl);
  return id;
}
export function axoDismiss(id: number): void {
  _toasts = _toasts.filter(x => x.id !== id);
  _emit();
}
/** Только для тестов: сбросить стопку. */
export function axoClearToasts(): void { _toasts = []; _emit(); }

export function AxoToastHost() {
  const items = useSyncExternalStore(_subscribe, _snapshot, _snapshot);
  if (items.length === 0) return null; // пустая стопка не должна добавлять DOM
  return (
    <div className="axo-toast-host" role="status" aria-live="polite">
      {items.map(t => (
        <div key={t.id} className={`axo-toast is-${t.tone}`}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <strong>{t.title}</strong>
            {t.text && <span>{t.text}</span>}
          </div>
          <button type="button" className="axo-btn is-ghost is-sm" aria-label="Закрыть" onClick={() => axoDismiss(t.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

/* ── Скелетон — .skeleton: блок + бегущий градиент 1.4s ── */
export function AxoSkeleton({ variant = "line", className = "", height }: { variant?: "line" | "title" | "copy" | "card" | "table"; className?: string; height?: number }) {
  const v = variant === "line" ? "" : `is-${variant}`;
  return <div className={`axo-skeleton ${v} ${className}`.trim()} style={height ? { height } : undefined} aria-hidden="true" />;
}
export function AxoSkeletonPage() {
  return (
    <div className="axo-skeleton-page" aria-busy="true">
      <AxoSkeleton variant="title" />
      <AxoSkeleton variant="copy" />
      <AxoSkeleton variant="card" />
      <AxoSkeleton variant="table" />
    </div>
  );
}

/* ── Пустое состояние — .platform-state: пунктирная рамка, плитка иконки 56×56 ── */
export function AxoEmpty(
  { icon, title, text, action, error, compact }:
  { icon?: ReactNode; title: ReactNode; text?: ReactNode; action?: ReactNode; error?: boolean; compact?: boolean },
) {
  return (
    <div className={`axo-empty ${error ? "is-error" : ""} ${compact ? "is-compact" : ""}`.trim()}>
      {icon && <span className="axo-empty-icon">{icon}</span>}
      <div style={compact ? { minWidth: 0 } : undefined}>
        <h2>{title}</h2>
        {text && <p>{text}</p>}
        {action && <div className="axo-empty-actions">{action}</div>}
      </div>
    </div>
  );
}

/* ── Хлебные крошки — надзаголовок-секция (моно, капс) + текущий экран ── */
export function AxoBreadcrumbs(
  { items, current }: { items: { label: string; onClick?: () => void }[]; current: ReactNode },
) {
  return (
    <nav className="axo-breadcrumbs" aria-label="Хлебные крошки">
      {items.map((it, i) => (
        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          {it.onClick
            ? <button type="button" onClick={it.onClick}>{it.label}</button>
            : <span style={{ color: "var(--t-low)" }}>{it.label}</span>}
          <ChevronRight size={13} className="sep" />
        </span>
      ))}
      <span className="cur" aria-current="page">{current}</span>
    </nav>
  );
}

/** Заголовок страницы: надзаголовок (секция) + h1 + пояснение + действия. */
export function AxoPageHeader(
  { eyebrow, title, sub, actions, crumb }:
  { eyebrow?: ReactNode; title: ReactNode; sub?: ReactNode; actions?: ReactNode; crumb?: ReactNode },
) {
  return (
    <header style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24, flexWrap: "wrap" }}>
      <div style={{ minWidth: 0 }}>
        {crumb ?? (eyebrow ? <span className="axo-eyebrow">{eyebrow}</span> : null)}
        <h1 className="axo-page-title">{title}</h1>
        {sub && <p className="axo-page-sub">{sub}</p>}
      </div>
      {actions && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{actions}</div>}
    </header>
  );
}

/* ── Прогресс — .platform-progress: дорожка 6px, заливка акцентом, scaleX .7s ── */
export function AxoProgress({ value, max = 100, label }: { value: number; max?: number; label?: ReactNode }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div>
      {label && (
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 7 }}>
          <span style={{ color: "var(--t-low)", fontSize: 12 }}>{label}</span>
          <span className="axo-mono" style={{ color: "var(--t-hi)", fontSize: 12 }}>{Math.round(pct)}%</span>
        </div>
      )}
      <div className="axo-progress-track" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={max}>
        <div className="axo-progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ── Поиск — .platform-search: 42px, радиус 11px, прозрачный инпут ── */
export function AxoSearch(
  { value, onChange, placeholder = "Поиск…", className = "" }:
  { value: string; onChange: (v: string) => void; placeholder?: string; className?: string },
) {
  return (
    <div className={`axo-search ${className}`.trim()}>
      <Search size={15} />
      <input value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} aria-label={placeholder} />
    </div>
  );
}

/* ── Каркас приложения: сетка 248px + контент, топбар sticky с blur ──
   Карта разделов и живые бейджи — из docs Axottle (panel__navigation.md):
   четыре группы («Главное», «Инфраструктура», «Сервисы», «Управление»), счётчики
   у «Здоровье», «Конфиги», «Ноды». Здесь — только геометрия и состояния. */
export interface AxoNavItem { key: string; label: ReactNode; icon?: ReactNode; badge?: ReactNode; onSelect?: () => void }
export interface AxoNavGroup { label: string; items: AxoNavItem[] }

export function AxoSidebar(
  { groups, active, onSelect, head, foot, open, className = "" }:
  { groups: AxoNavGroup[]; active: string; onSelect: (key: string) => void; head?: ReactNode; foot?: ReactNode;
    open?: boolean; className?: string },
) {
  return (
    <aside className={`axo-shell-sidebar ${open ? "is-open" : ""} ${className}`.trim()}>
      {head && <div className="axo-shell-head">{head}</div>}
      <nav className="platform-nav" style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {groups.map(g => (
          <div key={g.label}>
            <div className="ni-nav-section" style={{ margin: "12px 10px 4px" }}>{g.label}</div>
            {g.items.map(it => (
              <button
                key={it.key} type="button"
                className={`navitem ${it.key === active ? "active" : ""}`.trim()}
                style={{ position: "relative" }}
                aria-current={it.key === active ? "page" : undefined}
                onClick={() => { it.onSelect?.(); onSelect(it.key); }}
              >
                {it.icon}
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{it.label}</span>
                {it.badge}
              </button>
            ))}
          </div>
        ))}
      </nav>
      {foot && <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 3 }}>{foot}</div>}
    </aside>
  );
}

export function AxoTopbar(
  { left, search, actions, className = "" }:
  { left?: ReactNode; search?: ReactNode; actions?: ReactNode; className?: string },
) {
  return (
    <header className={`axo-topbar ${className}`.trim()}>
      <div style={{ minWidth: 0, flex: 1, display: "flex", alignItems: "center", gap: 12 }}>{left}</div>
      {search}
      {actions && <div style={{ display: "flex", alignItems: "center", gap: 10 }}>{actions}</div>}
    </header>
  );
}

export function AxoShell(
  { sidebar, children, contentStyle }:
  { sidebar: ReactNode; children: ReactNode; contentStyle?: CSSProperties },
) {
  return (
    <div className="axo-shell">
      {sidebar}
      <div className="axo-shell-main">{children}</div>
    </div>
  );
}

/* ── Переключатель темы — каркас, пункт из доков: «верхняя панель — глобальный поиск,
   переключатель светлой/тёмной темы и меню выхода» (panel__overview.md).
   Меняет только resolved-тему (data-theme) и запоминает её для аккаунта — как Settings → Тема. ── */
export function AxoThemeToggle(
  { accountId, className = "" }:
  { accountId?: string | null; className?: string },
) {
  const id = accountId === undefined ? getActiveId() : accountId;
  // Текущее РАЗРЕШЁННОЕ состояние читаем из data-theme: его выставил App на монтировании
  // (может быть следствием режима «Системная»). Первое нажатие переключает от того, что
  // видно сейчас, и дальше режим становится явным — ровно как выбор в Settings → Тема.
  const [resolved, setResolved] = useState<"light" | "dark">(() =>
    typeof document !== "undefined" && document.documentElement.dataset.theme === "light" ? "light" : "dark");

  const toggle = useCallback(() => {
    const next: ThemeMode = resolved === "dark" ? "light" : "dark";
    setResolved(next);
    saveThemeMode(id, next);
    applyThemeMode(next);
  }, [resolved, id]);

  const dark = resolved === "dark";
  return (
    <button
      type="button" className={`axo-btn is-icon ${className}`.trim()}
      aria-label={dark ? "Светлая тема" : "Тёмная тема"} title={dark ? "Светлая тема" : "Тёмная тема"}
      aria-pressed={dark} onClick={toggle}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

/* Иконки солнца/луны нарисованы инлайном, чтобы не тянуть второй набор в кит. */
function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

/* Мелочи кита: галочка-строка и «секция» карточки — частые в формах панели. */
export function AxoCheckRow({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: ok ? "var(--ok)" : "var(--t-low)" }}>
      {ok ? <Check size={14} /> : <span style={{ width: 14, textAlign: "center" }}>—</span>}
      <span>{children}</span>
    </div>
  );
}

export function AxoSection({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <span className="axo-eyebrow">{title}</span>
        {actions}
      </div>
      {children}
    </section>
  );
}

/* Контекст пригодится будущим экранам, которым нужен доступ к текущему аккаунту кита. */
export const AxoAccountContext = createContext<string | null>(null);
export function useAxoAccount(): string | null { return useContext(AxoAccountContext); }

export { TONE_CLASS as AXO_TONE_CLASS };
