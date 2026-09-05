// Shared layout primitives for the redesign — Page / PageHeader / Seg / EmptyState,
// plus the S1 structural controls: Field / InputShell / Select / Toggle / Tabs /
// Badge / Stat / Card / Table. All skin-agnostic (CSS variables only), mobile-first,
// interactive targets ≥44px on touch. No logic here — pure presentational wrappers.
import {
  useId, useState, useEffect, useRef,
  type ReactNode, type CSSProperties, type InputHTMLAttributes,
} from "react";
import { ChevronDown, Eye, EyeOff, X } from "lucide-react";
import { AnimatePresence, motion, type Variants } from "motion/react";

export function Page({ children, max = 1060 }: { children: ReactNode; max?: number }) {
  // .ni-pagebody — чтобы применялся мобильный padding-override из index.css.
  return (
    <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
      <div className="ni-pagebody" style={{ maxWidth: max, margin: "0 auto", padding: "22px 26px 40px" }}>{children}</div>
    </div>
  );
}

export function PageHeader({ icon, title, subtitle, actions }: {
  icon?: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 14, marginBottom: 18 }}>
      <div style={{ minWidth: 0 }}>
        <h1 className="h1" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {/* .ni-pageicon — хук для тайла иконки под скином nodeflow; на
              остальных скинах стилей не имеет и ничего не меняет. */}
          {icon && <span className="ni-pageicon" style={{ color: "var(--accent-hi)", display: "flex" }}>{icon}</span>}
          {title}
        </h1>
        {subtitle && <p className="sub">{subtitle}</p>}
      </div>
      {actions && <div style={{ display: "flex", alignItems: "center", gap: 8, flex: "none" }}>{actions}</div>}
    </div>
  );
}

export interface SegOption { v: string | number; l: string; icon?: ReactNode }
export function Seg({ options, value, onChange, accent, mini, style }: {
  options: SegOption[]; value: string | number; onChange: (v: never) => void;
  accent?: boolean; mini?: boolean; style?: React.CSSProperties;
}) {
  return (
    <div className={`seg ${accent ? "accent" : ""} ${mini ? "mini" : ""}`} style={style}>
      {options.map(o => (
        <button key={o.v} type="button" className={value === o.v ? "on" : ""}
          onClick={() => onChange(o.v as never)}>
          {o.icon}{o.l}
        </button>
      ))}
    </div>
  );
}

// Единое пустое состояние (B8): мягкая cyan-иконка → яркий заголовок →
// приглушённый подсказка → (опц.) действие. Заменяет разрозненные «серый текст
// в рамке» пустые состояния по всем экранам.
export function EmptyState({ icon, title, hint, action }: {
  icon?: ReactNode; title: ReactNode; hint?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="card" style={{
      padding: 32, textAlign: "center", display: "flex",
      flexDirection: "column", alignItems: "center", gap: 8,
    }}>
      {icon && (
        <span style={{
          width: 40, height: 40, borderRadius: 8, display: "grid", placeItems: "center",
          background: "var(--accent-dim)", border: "1px solid var(--accent-line)",
          color: "var(--accent-hi)",
        }}>{icon}</span>
      )}
      <p style={{ fontSize: 14, fontWeight: 600, color: "var(--t-hi)", margin: 0 }}>{title}</p>
      {hint && <p className="sub" style={{ margin: 0 }}>{hint}</p>}
      {action}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   S1 structural controls (Remnawave-стиль, skin-agnostic)
   ──────────────────────────────────────────────────────────────── */

// Нижний слой поля: стилизованный <input> (та же поверхность/фокус, что и
// `.input`), плюс опциональный reveal-тумблер для секретных значений.
export function InputShell({ secret, error, className, ...rest }:
  InputHTMLAttributes<HTMLInputElement> & { secret?: boolean; error?: boolean }) {
  const [show, setShow] = useState(false);
  const type = secret ? (show ? "text" : "password") : rest.type;
  return (
    <div style={secret ? { position: "relative" } : undefined}>
      <input
        {...rest}
        type={type}
        autoComplete="off"
        spellCheck={false}
        className={`input ${secret ? "pr-9" : ""} ${error ? "err" : ""} ${className ?? ""}`}
      />
      {secret && (
        <button type="button" tabIndex={-1} aria-label={show ? "Скрыть" : "Показать"}
          onClick={() => setShow(v => !v)}
          className="absolute inset-y-0 right-0 flex items-center px-2.5
                     text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
          {show ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      )}
    </div>
  );
}

// Верхний слой поля: label + (любой контрол) + error/hint. `control` — дети
// (InputShell / Select / …), чтобы Field не зашивал тип контрола.
export function Field({ label, error, hint, required, htmlFor, children }: {
  label?: ReactNode; error?: string; hint?: ReactNode; required?: boolean;
  htmlFor?: string; children: ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {label && (
        <label className="label" htmlFor={htmlFor}>
          {label}{required && <span style={{ color: "var(--err)", marginLeft: 2 }}>*</span>}
        </label>
      )}
      {children}
      {error && <p className="errmsg">{error}</p>}
      {!error && hint && <p className="hint">{hint}</p>}
    </div>
  );
}

export interface SelectOption { value: string; label: string; disabled?: boolean }

// Кастомный дропдаун поверх нативного <select>. a11y: role=combobox/listbox/option,
// aria-expanded/aria-activedescendant, стрелки/Home/End/Enter, Escape закрывает,
// клик вне — закрывает. Триггер — кнопка ≥44px на touch.
export function Select({ options, value, onChange, placeholder = "— выберите —",
  disabled, error, "aria-label": ariaLabel, id, style }: {
  options: SelectOption[]; value: string; onChange: (v: string) => void;
  placeholder?: string; disabled?: boolean; error?: boolean;
  "aria-label"?: string; id?: string; style?: CSSProperties;
}) {
  const autoId = useId();
  const baseId = id ?? `ni-select-${autoId}`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);

  const selected = options.find(o => o.value === value);

  const openList = () => {
    if (disabled) return;
    setActive(Math.max(0, options.findIndex(o => o.value === value)));
    setOpen(true);
  };
  const choose = (v: string) => { onChange(v); setOpen(false); btnRef.current?.focus(); };

  // Внешний клик + Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setOpen(false); btnRef.current?.focus(); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Подмотка активного пункта в зону видимости.
  useEffect(() => {
    if (!open || active < 0) return;
    const el = menuRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!open) { openList(); return; }
    }
    if (!open) return;
    if (e.key === "ArrowDown") setActive(i => Math.min(options.length - 1, i + 1));
    else if (e.key === "ArrowUp") setActive(i => Math.max(0, i - 1));
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(options.length - 1);
    else if (e.key === "Enter" || e.key === " ") {
      const o = options[active];
      if (o && !o.disabled) choose(o.value);
    } else if (e.key === "Tab") setOpen(false);
  };

  return (
    <div ref={rootRef} className="ni-select" style={style}>
      <button
        ref={btnRef} type="button" id={baseId}
        role="combobox" aria-expanded={open} aria-haspopup="listbox"
        aria-controls={`${baseId}-menu`} aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        className={`ni-select-trigger ${error ? "err" : ""}`}
      >
        <span className={selected ? "trunc" : "trunc"} style={{ color: selected ? "var(--t-hi)" : "var(--t-faint)" }}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown size={14} style={{ flex: "none", color: "var(--t-faint)" }} />
      </button>
      {open && (
        <ul ref={menuRef} id={`${baseId}-menu`} role="listbox"
          aria-activedescendant={active >= 0 ? `${baseId}-opt-${active}` : undefined}
          className="ni-select-menu">
          {options.map((o, i) => (
            <li key={o.value} id={`${baseId}-opt-${i}`} role="option"
              aria-selected={o.value === value} aria-disabled={o.disabled || undefined}
              className={`ni-select-option ${i === active ? "active" : ""}`}
              onMouseDown={e => e.preventDefault()}
              onClick={() => { if (!o.disabled) choose(o.value); }}>
              <span className="trunc">{o.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Switch. Доступное имя — из оборачивающего <label> (текст в <span>), так что
// `getByRole("switch", { name })` находит его. Отрисовывается классом `.switch`
// (уже стилизован и skin-aware).
export function Toggle({ label, checked, onChange, disabled }: {
  label: ReactNode; checked: boolean; onChange: () => void; disabled?: boolean;
}) {
  return (
    <label className={`flex items-center gap-2.5 cursor-pointer select-none ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
      <button type="button" role="switch" aria-checked={checked} onClick={onChange} disabled={disabled}
        className={`switch ${checked ? "on" : ""}`} />
      <span style={{ color: "var(--t-low)" }}>{label}</span>
    </label>
  );
}

// Underline-табы (не Seg): плоский список с подчёркиванием активного пункта.
export interface TabOption { value: string; label: ReactNode; icon?: ReactNode }
export function Tabs({ tabs, value, onChange, className, style }: {
  tabs: TabOption[]; value: string; onChange: (v: string) => void;
  className?: string; style?: CSSProperties;
}) {
  return (
    <div role="tablist" className={`ni-tabs ${className ?? ""}`} style={style}>
      {tabs.map(t => (
        <button key={t.value} type="button" role="tab" aria-selected={value === t.value}
          className={`ni-tabs-tab ${value === t.value ? "on" : ""}`}
          onClick={() => onChange(t.value)}>
          {t.icon}{t.label}
        </button>
      ))}
    </div>
  );
}

// Badge — тонкий чип состояния (переиспользует `.chip` с тонами).
export function Badge({ tone = "neutral", children, className, style }: {
  tone?: "neutral" | "ok" | "warn" | "err" | "accent"; children: ReactNode;
  className?: string; style?: CSSProperties;
}) {
  return <span className={`chip ${tone} ${className ?? ""}`} style={style}>{children}</span>;
}

// Stat — подпись (uppercase) + крупное значение + опц. примечание.
export function Stat({ label, value, hint, className, style }: {
  label: ReactNode; value: ReactNode; hint?: ReactNode; className?: string; style?: CSSProperties;
}) {
  return (
    <div className={className} style={{ display: "flex", flexDirection: "column", gap: 2, ...style }}>
      <span className="micro">{label}</span>
      <span className="num" style={{ fontSize: 20, fontWeight: 700, color: "var(--t-hi)", lineHeight: 1.1 }}>{value}</span>
      {hint && <span className="sub" style={{ margin: 0 }}>{hint}</span>}
    </div>
  );
}

// Card — каркас с опц. header (title/subtitle/actions) и footer.
export function Card({ title, subtitle, actions, footer, children, className, style, bodyStyle }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; footer?: ReactNode;
  children: ReactNode; className?: string; style?: CSSProperties; bodyStyle?: CSSProperties;
}) {
  const hasHead = !!(title || subtitle || actions);
  return (
    <div className={`card ${className ?? ""}`} style={style}>
      {hasHead && (
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 10, padding: "14px 16px",
          borderBottom: "1px solid var(--line-soft)",
        }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            {title && <p style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)", margin: 0 }}>{title}</p>}
            {subtitle && <p className="sub" style={{ margin: 0 }}>{subtitle}</p>}
          </div>
          {actions && <div style={{ display: "flex", alignItems: "center", gap: 8, flex: "none" }}>{actions}</div>}
        </div>
      )}
      <div style={{ padding: 16, ...bodyStyle }}>{children}</div>
      {footer && (
        <div style={{ padding: "10px 16px", borderTop: "1px solid var(--line-soft)" }}>{footer}</div>
      )}
    </div>
  );
}

// Table — скролл-контейнер + таблица с липкой шапкой. `head` — массив ячеек.
export function Table({ head, children, className, style }: {
  head?: ReactNode[]; children: ReactNode; className?: string; style?: CSSProperties;
}) {
  return (
    <div className={`ni-table ${className ?? ""}`} style={{ overflow: "auto", borderRadius: "var(--r-md)", ...style }}>
      <table className="tbl">
        {head && (
          <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        )}
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────
   S3: общий модальный примитив (Modal / FormDialog)
   ──────────────────────────────────────────────────────────────── */

export type ModalSize = "sm" | "md" | "lg";

const MODAL_MAX: Record<ModalSize, number> = { sm: 420, md: 560, lg: 860 };

// Enter/exit — единый ритм 150–200ms (fade оверлея, slide-up панели).
const overlayV: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.18, ease: "easeOut" } },
  exit:    { opacity: 0, transition: { duration: 0.15, ease: "easeIn" } },
};
const panelV: Variants = {
  initial: { opacity: 0, y: 18, scale: 0.99 },
  animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.18, ease: "easeOut" } },
  exit:    { opacity: 0, y: 12, scale: 0.99, transition: { duration: 0.15, ease: "easeIn" } },
};

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Overlay с blur + центрирование (на мобиле — slide-up снизу через
// `.ni-modal-overlay` в index.css). Esc / клик по оверлею закрывают, фокус
// замыкается внутри панели и возвращается на прежний элемент при закрытии.
// `open` управляет появлением; родитель рендерит <Modal> безусловно, чтобы
// AnimatePresence могла проиграть exit-анимацию.
export function Modal({ open, onClose, size = "md", ariaLabel, children,
  className, panelClassName, closeOnOverlay = true, closeOnEsc = true }: {
  open: boolean;
  onClose: () => void;
  size?: ModalSize;
  ariaLabel?: string;
  children: ReactNode;
  className?: string;
  panelClassName?: string;
  closeOnOverlay?: boolean;
  closeOnEsc?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const prevActive = document.activeElement as HTMLElement | null;

    // Переносим фокус внутрь диалога (первый focusable, иначе сама панель).
    const panel = panelRef.current;
    if (panel) {
      const first = panel.querySelector<HTMLElement>(FOCUSABLE);
      (first ?? panel).focus();
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (closeOnEsc) { e.preventDefault(); onClose(); }
        return;
      }
      if (e.key !== "Tab" || !panelRef.current) return;
      const focusables = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !panelRef.current.contains(active)) { e.preventDefault(); last.focus(); }
      } else if (active === last || !panelRef.current.contains(active)) {
        e.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prevActive?.focus?.();
    };
  }, [open, onClose, closeOnEsc]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className={`ni-modal-overlay ${className ?? ""}`}
          variants={overlayV}
          initial="initial"
          animate="animate"
          exit="exit"
          onMouseDown={e => { if (closeOnOverlay && e.target === e.currentTarget) onClose(); }}
        >
          <motion.div
            ref={panelRef}
            className={`ni-modal-panel ${panelClassName ?? ""}`}
            style={{ maxWidth: MODAL_MAX[size] }}
            variants={panelV}
            role="dialog"
            aria-modal="true"
            aria-label={ariaLabel}
            tabIndex={-1}
          >
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// FormDialog — Modal с готовой шапкой (title/subtitle/закрыть), телом и опц.
// футером. Для «Сменить домен» / «Образ remnanode» и любых простых диалогов.
export function FormDialog({ open, onClose, title, subtitle, icon, size = "md",
  footer, children, closeOnOverlay = true }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  size?: ModalSize;
  footer?: ReactNode;
  children: ReactNode;
  closeOnOverlay?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size={size}
      closeOnOverlay={closeOnOverlay}
      ariaLabel={typeof title === "string" ? title : undefined}
    >
      <div className="ni-modal-head">
        <div className="flex items-center gap-2.5 min-w-0">
          {icon && <span style={{ color: "var(--accent-hi)", display: "flex", flex: "none" }}>{icon}</span>}
          <div className="min-w-0">
            <h2 className="text-sm font-semibold truncate" style={{ color: "var(--t-hi)", margin: 0 }}>{title}</h2>
            {subtitle && <p className="text-xs truncate" style={{ color: "var(--t-low)", margin: 0 }}>{subtitle}</p>}
          </div>
        </div>
        <button type="button" onClick={onClose} className="iconbtn" aria-label="Закрыть" title="Закрыть">
          <X size={15} />
        </button>
      </div>
      <div className="ni-modal-body">{children}</div>
      {footer && <div className="ni-modal-foot">{footer}</div>}
    </Modal>
  );
}
