// Shared layout primitives for the redesign — Page / PageHeader / Seg / EmptyState.
import type { ReactNode } from "react";

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
