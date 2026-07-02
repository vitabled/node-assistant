// Shared layout primitives for the redesign — Page / PageHeader / Seg.
import type { ReactNode } from "react";

export function Page({ children, max = 1060 }: { children: ReactNode; max?: number }) {
  return (
    <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
      <div style={{ maxWidth: max, margin: "0 auto", padding: "22px 26px 40px" }}>{children}</div>
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
          {icon && <span style={{ color: "var(--accent-hi)", display: "flex" }}>{icon}</span>}
          {title}
        </h1>
        {subtitle && <p className="sub">{subtitle}</p>}
      </div>
      {actions && <div style={{ display: "flex", alignItems: "center", gap: 8, flex: "none" }}>{actions}</div>}
    </div>
  );
}

export interface SegOption { v: string | number; l: string }
export function Seg({ options, value, onChange, accent, mini, style }: {
  options: SegOption[]; value: string | number; onChange: (v: never) => void;
  accent?: boolean; mini?: boolean; style?: React.CSSProperties;
}) {
  return (
    <div className={`seg ${accent ? "accent" : ""} ${mini ? "mini" : ""}`} style={style}>
      {options.map(o => (
        <button key={o.v} type="button" className={value === o.v ? "on" : ""}
          onClick={() => onChange(o.v as never)}>{o.l}</button>
      ))}
    </div>
  );
}
