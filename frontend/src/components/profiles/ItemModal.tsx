// ============================================================
// Inbound / Outbound GUI section editor (form + JSON sub-blocks).
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).
// Top-level fields are edited as a form; the protocol `settings` and
// `streamSettings` blocks get raw-JSON sub-editors (seeded on open) so every
// advanced key stays reachable. Validation errors are shown inline.
// ============================================================

import { useState } from 'react';
import { X, Save, ChevronDown, ChevronRight } from 'lucide-react';
import { PROTOCOLS, NETWORKS, SECURITIES } from './core/schema';
import { validateInbound, validateOutbound } from './core/validators';
import type { Inbound, Outbound } from './core/types';

type Kind = 'inbound' | 'outbound';

interface Props {
  kind: Kind;
  initial?: Inbound | Outbound;
  onClose: () => void;
  onSave: (item: Inbound | Outbound) => void;
}

// A collapsible raw-JSON block; re-seeds its text from `value` when (re)opened.
function JsonBlock({ label, value, onChange }: {
  label: string; value: unknown; onChange: (v: any) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [err, setErr] = useState<string | null>(null);

  const toggle = () => {
    if (!open) { setText(JSON.stringify(value ?? {}, null, 2)); setErr(null); }
    setOpen(o => !o);
  };
  const blur = () => {
    const t = text.trim();
    if (!t) { onChange(undefined); setErr(null); return; }
    try { onChange(JSON.parse(t)); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <div className="flex flex-col gap-1">
      <button type="button" onClick={toggle} className="btn btn-soft" style={{ justifyContent: 'flex-start' }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {label}
      </button>
      {open && (
        <>
          <textarea value={text} onChange={e => setText(e.target.value)} onBlur={blur} rows={8} spellCheck={false}
            placeholder="{}" className={`input font-mono text-xs ${err ? 'err' : ''}`} style={{ resize: 'vertical' }} />
          {err && <p className="errmsg">Некорректный JSON: {err}</p>}
        </>
      )}
    </div>
  );
}

export function ItemModal({ kind, initial, onClose, onSave }: Props) {
  const [item, setItem] = useState<any>(() =>
    initial ? JSON.parse(JSON.stringify(initial)) : { protocol: 'vless', tag: '', settings: {}, streamSettings: { network: 'tcp', security: 'none' } });

  const set = (patch: Record<string, unknown>) => setItem((it: any) => ({ ...it, ...patch }));
  const setStream = (patch: Record<string, unknown>) => setItem((it: any) => ({ ...it, streamSettings: { ...(it.streamSettings || {}), ...patch } }));

  const errors = kind === 'inbound' ? validateInbound(item) : validateOutbound(item);
  const canSave = !!item.protocol && !!item.tag;

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg" style={{ maxHeight: '86vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', borderBottom: '1px solid var(--line-soft)', flex: 'none' }}>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--t-hi)' }}>
            {initial ? 'Редактировать' : 'Новый'} {kind === 'inbound' ? 'inbound' : 'outbound'}
          </h2>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 12, overflowY: 'auto' }}>
          <div className="flex flex-col gap-1">
            <label className="label">Tag *</label>
            <input className="input" value={item.tag || ''} onChange={e => set({ tag: e.target.value })} placeholder="proxy" autoComplete="off" spellCheck={false} />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="label">Протокол</label>
              <select className="selectbox" value={item.protocol} onChange={e => set({ protocol: e.target.value })}>
                {PROTOCOLS.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            {kind === 'inbound' && (
              <div className="flex flex-col gap-1">
                <label className="label">Порт</label>
                <input className="input" type="number" value={item.port ?? ''} onChange={e => set({ port: e.target.value === '' ? undefined : Number(e.target.value) })} placeholder="443" />
              </div>
            )}
          </div>

          {kind === 'inbound' && (
            <div className="flex flex-col gap-1">
              <label className="label">Listen</label>
              <input className="input" value={item.listen || ''} onChange={e => set({ listen: e.target.value || undefined })} placeholder="0.0.0.0" autoComplete="off" spellCheck={false} />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="label">Транспорт (network)</label>
              <select className="selectbox" value={item.streamSettings?.network || 'tcp'} onChange={e => setStream({ network: e.target.value })}>
                {NETWORKS.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="label">Security</label>
              <select className="selectbox" value={item.streamSettings?.security || 'none'} onChange={e => setStream({ security: e.target.value })}>
                {SECURITIES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <JsonBlock label="settings (JSON)" value={item.settings} onChange={v => set({ settings: v })} />
          <JsonBlock label="streamSettings (JSON)" value={item.streamSettings} onChange={v => set({ streamSettings: v })} />

          {errors.length > 0 && (
            <div className="card" style={{ padding: '8px 12px', borderColor: 'var(--warn-line)', background: 'var(--warn-dim)' }}>
              <p className="micro" style={{ color: 'var(--warn)', marginBottom: 4 }}>Замечания</p>
              {errors.slice(0, 6).map((e, i) => (
                <p key={i} style={{ fontSize: 11.5, color: 'var(--t-mid)' }}>{e.field}: {e.message}</p>
              ))}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 18px', borderTop: '1px solid var(--line-soft)', flex: 'none' }}>
          <button onClick={onClose} className="btn btn-soft">Отмена</button>
          <button onClick={() => onSave(item)} disabled={!canSave} className="btn btn-primary"><Save size={13} /> Сохранить</button>
        </div>
      </div>
    </div>
  );
}
