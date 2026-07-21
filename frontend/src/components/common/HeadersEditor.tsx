import { useState } from "react";
import { Trash2, Plus, ChevronUp, ChevronDown } from "lucide-react";
import {
  type HeaderRow, recordToRows, rowsToRecord, isValidHeaderName, HEADER_PRESETS,
} from "./headers";

interface Props {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  label?: string;
  disabled?: boolean;
}

// Controlled key-value HTTP-headers editor (Wave-5 Plan F). Keeps internal rows
// (order + transient empty/dup names) and emits a normalised Record on every
// edit. Seeded once from `value` — the owner remounts it on open (e.g. modal).
export function HeadersEditor({ value, onChange, label, disabled }: Props) {
  const [rows, setRows] = useState<HeaderRow[]>(() => recordToRows(value));

  const push = (next: HeaderRow[]) => { setRows(next); onChange(rowsToRecord(next)); };
  const setRow = (i: number, patch: Partial<HeaderRow>) => push(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = (name = "") => push([...rows, { name, value: "" }]);
  const delRow = (i: number) => push(rows.filter((_, idx) => idx !== i));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= rows.length) return;
    const n = [...rows];
    [n[i], n[j]] = [n[j], n[i]];
    push(n);
  };

  return (
    <div className="flex flex-col gap-1.5">
      {label && <span className="micro">{label}</span>}
      {rows.map((r, i) => {
        const bad = r.name.trim() !== "" && !isValidHeaderName(r.name.trim());
        return (
          <div key={i} className="flex items-center gap-1.5">
            <input className="input" style={{ flex: "0 0 38%", borderColor: bad ? "var(--warn-line)" : undefined }}
              placeholder="Header-Name" value={r.name} disabled={disabled}
              onChange={e => setRow(i, { name: e.target.value })} />
            <input className="input" style={{ flex: 1 }} placeholder="значение" value={r.value} disabled={disabled}
              onChange={e => setRow(i, { value: e.target.value })} />
            <button type="button" className="iconbtn" disabled={disabled || i === 0} title="Вверх" onClick={() => move(i, -1)}><ChevronUp size={13} /></button>
            <button type="button" className="iconbtn" disabled={disabled || i === rows.length - 1} title="Вниз" onClick={() => move(i, 1)}><ChevronDown size={13} /></button>
            <button type="button" className="iconbtn" disabled={disabled} title="Удалить" onClick={() => delRow(i)}><Trash2 size={13} /></button>
          </div>
        );
      })}
      {rows.some(r => r.name.trim() !== "" && !isValidHeaderName(r.name.trim())) && (
        <span style={{ color: "var(--warn)", fontSize: 11 }}>
          Недопустимое имя заголовка — такие строки не сохраняются.
        </span>
      )}
      <div className="flex items-center gap-2">
        <button type="button" className="btn btn-sm" disabled={disabled} onClick={() => addRow()}>
          <Plus size={13} /> Заголовок
        </button>
        <select className="input" style={{ maxWidth: 180 }} disabled={disabled} value=""
          onChange={e => { if (e.target.value) addRow(e.target.value); }}>
          <option value="">Пресет…</option>
          {HEADER_PRESETS.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
      </div>
    </div>
  );
}
