// Raw-JSON section editor modal (CodeMirror6 + ajv lint).
// Ported from bropines/xray-config-ui-editor (MIT, © 2026 Sergey Pinus).

import { useState } from 'react';
import { X, Save, AlertTriangle } from 'lucide-react';
import { JsonEditor } from './JsonEditor';
import type { SchemaMode } from './core/schema';

interface Props {
  title: string;
  data: unknown;
  schemaMode: SchemaMode;
  onClose: () => void;
  onSave: (newData: unknown) => void;
}

export function SectionJsonModal({ title, data, schemaMode, onClose, onSave }: Props) {
  const [text, setText] = useState(() => JSON.stringify(data ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    try {
      const parsed = text.trim() ? JSON.parse(text) : {};
      setError(null);
      onSave(parsed);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" style={{ maxWidth: 780, height: '80vh', display: 'flex', flexDirection: 'column' }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '12px 18px', borderBottom: '1px solid var(--line-soft)', flex: 'none',
        }}>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--t-hi)' }}>Раздел: {title}</h2>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div style={{ flex: 1, minHeight: 0, padding: 14 }}>
          <JsonEditor value={text} onChange={setText} schemaMode={schemaMode} />
        </div>

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 18px', color: 'var(--err)', fontSize: 12.5 }}>
            <AlertTriangle size={14} /> Некорректный JSON: {error}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 18px', borderTop: '1px solid var(--line-soft)', flex: 'none' }}>
          <button onClick={onClose} className="btn btn-soft">Отмена</button>
          <button onClick={save} className="btn btn-primary"><Save size={13} /> Сохранить</button>
        </div>
      </div>
    </div>
  );
}
