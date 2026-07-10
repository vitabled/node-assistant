// Diagnostics panel — ajv schema errors + protocol diagnostics.
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).

import { AlertOctagon, AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import type { XrayConfig } from './core/types';
import { validateFullConfig } from './core/validators';
import { runFullDiagnostics, type Diagnostic } from './core/diagnostics';

interface Row {
  severity: 'critical' | 'warning' | 'info';
  where: string;
  message: string;
  suggestion?: string;
}

// Combined result used by both the panel and the sync-gate (blockers > 0).
export function collectDiagnostics(config: XrayConfig | null): { rows: Row[]; blockers: number } {
  if (!config) return { rows: [], blockers: 0 };
  const rows: Row[] = [];

  // ajv structural / reference errors are critical blockers — EXCEPT closed-enum
  // violations (unknown protocol/network/security), which drop to a warning so a
  // config using a value newer than our enum lists can still sync.
  for (const e of validateFullConfig(config)) {
    rows.push({ severity: e.keyword === 'enum' ? 'warning' : 'critical', where: e.field, message: e.message });
  }
  // Semantic diagnostics (protocol incompatibilities, dangling tags).
  for (const d of runFullDiagnostics(config) as Diagnostic[]) {
    rows.push({
      severity: d.severity === 'info' ? 'info' : d.severity,
      where: d.itemIndex != null ? `${d.section}[${d.itemIndex}]${d.field ? '.' + d.field : ''}` : d.section,
      message: d.message,
      suggestion: d.suggestion,
    });
  }
  const blockers = rows.filter(r => r.severity === 'critical').length;
  return { rows, blockers };
}

const META = {
  critical: { Icon: AlertOctagon,  color: 'var(--err)',  dim: 'var(--err-dim)',  line: 'var(--err-line)' },
  warning:  { Icon: AlertTriangle, color: 'var(--warn)', dim: 'var(--warn-dim)', line: 'var(--warn-line)' },
  info:     { Icon: Info,          color: 'var(--t-mid)', dim: 'var(--bg3)',      line: 'var(--line)' },
} as const;

export function DiagnosticsPanel({ config }: { config: XrayConfig | null }) {
  const { rows } = collectDiagnostics(config);

  if (rows.length === 0) {
    return (
      <div className="card card-p" style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--ok)' }}>
        <CheckCircle2 size={16} />
        <span style={{ fontSize: 13 }}>Проблем не найдено — конфиг валиден.</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map((r, i) => {
        const m = META[r.severity];
        return (
          <div key={i} className="card" style={{
            display: 'flex', gap: 10, padding: '10px 12px',
            borderColor: m.line, background: m.dim,
          }}>
            <m.Icon size={15} style={{ color: m.color, flex: 'none', marginTop: 1 }} />
            <div style={{ minWidth: 0 }}>
              <p style={{ fontSize: 12.5, color: 'var(--t-hi)' }}>{r.message}</p>
              <p className="num" style={{ fontSize: 11, color: 'var(--t-low)', marginTop: 2 }}>{r.where}</p>
              {r.suggestion && <p style={{ fontSize: 11.5, color: m.color, marginTop: 3 }}>→ {r.suggestion}</p>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
