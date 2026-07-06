import type { ReactNode } from "react";
import { deployJobsKey } from "../../auth/store";

// Shared dark-theme primitives for the infra-billing pages (Status-Page style).

export const inputCls = "input";

export function PageHeader({ icon, title, subtitle, actions }: {
  icon: ReactNode; title: string; subtitle?: string; actions?: ReactNode;
}) {
  return (
    <div className="ni-pagehead flex items-center justify-between mb-5">
      <div>
        <h1 className="text-base font-semibold text-[var(--t-hi)] flex items-center gap-2">{icon} {title}</h1>
        {subtitle && <p className="text-xs text-[var(--t-low)] mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="ni-pagehead-actions flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="ni-pagebody max-w-5xl mx-auto px-6 py-6">{children}</div>
    </div>
  );
}

export function Field({ label, value, onChange, placeholder, type = "text" }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} spellCheck={false} className={inputCls} />
    </div>
  );
}

export function SelectField({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[];
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)} className="selectbox">
        {options.length === 0 && <option value="">— нет —</option>}
        {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer: ReactNode; wide?: boolean;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className={`bg-[var(--bg1)] border border-[var(--line)] rounded-xl w-full ${wide ? "max-w-lg" : "max-w-md"} p-5 max-h-[90vh] overflow-y-auto`}>
        <h2 className="text-sm font-semibold text-[var(--t-hi)] mb-4">{title}</h2>
        <div className="flex flex-col gap-3">{children}</div>
        <div className="flex justify-end gap-2 mt-5">{footer}</div>
      </div>
    </div>
  );
}

// ── Formatting ────────────────────────────────────────────────
export const fmtNum = (v: number, cur = "") =>
  `${(v ?? 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}${cur ? " " + cur : ""}`;

export const fmtDate = (isoOrTs: string | number) => {
  const d = typeof isoOrTs === "number" ? new Date(isoOrTs * 1000) : new Date(isoOrTs);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
};

export const fmtDateShort = (iso: string) => {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
};

// Deploy nodes for node-linking selectors — read from the DeployDashboard's
// localStorage (deploy_jobs), avoiding an extra backend round-trip.
export function loadDeployNodes(): { value: string; label: string }[] {
  try {
    const jobs = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
    return (Array.isArray(jobs) ? jobs : []).map((j: { taskId: string; domain: string; ip: string }) =>
      ({ value: j.taskId, label: `${j.domain} (${j.ip})` }));
  } catch { return []; }
}
