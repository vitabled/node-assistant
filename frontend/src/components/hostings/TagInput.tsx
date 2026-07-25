// Controlled tag editor (Wave-8 §1). Chips + a text input with datalist
// autocomplete fed by the account-level tag pool (`GET /api/hostings/tags`).
// Mirrors the backend normalisation: ≤10 tags, ≤24 chars each, deduped, trimmed.
import { useEffect, useState, type KeyboardEvent } from "react";
import { Tag, X, Plus } from "lucide-react";
import { hostingsApi } from "./api";

export function TagInput({ label, value, onChange }: {
  label?: string;
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [suggest, setSuggest] = useState<string[]>([]);

  useEffect(() => { hostingsApi.tags().then(setSuggest).catch(() => {}); }, []);

  const add = (raw: string) => {
    const t = raw.replace(/[\r\n]/g, " ").trim().slice(0, 24);
    if (!t) return;
    if (value.includes(t) || value.length >= 10) { setDraft(""); return; }
    onChange([...value, t]);
    setDraft("");
  };
  const remove = (t: string) => onChange(value.filter(x => x !== t));

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(draft); }
    else if (e.key === "Backspace" && !draft && value.length) remove(value[value.length - 1]);
  };

  const remaining = suggest.filter(s => !value.includes(s));

  return (
    <div className="flex flex-col gap-1.5">
      {label && <label className="label">{label}</label>}
      <div className="flex flex-wrap gap-1.5">
        {value.map(t => (
          <span key={t} className="flex items-center gap-1 text-[11px] rounded-full px-2 py-0.5 bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)]">
            <Tag size={10} /> {t}
            <button type="button" onClick={() => remove(t)} className="hover:text-[var(--t-hi)]" title="Убрать тег"><X size={10} /></button>
          </span>
        ))}
        {value.length === 0 && <span className="text-[11px] text-[var(--t-faint)]">Тегов нет</span>}
      </div>
      {value.length < 10 && (
        <div className="flex items-center gap-2">
          <input value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={onKey}
            list="hosting-tag-suggest" placeholder="Новый тег (Enter)" spellCheck={false} className="input flex-1" maxLength={24} />
          <datalist id="hosting-tag-suggest">
            {remaining.map(s => <option key={s} value={s} />)}
          </datalist>
          <button type="button" onClick={() => add(draft)} disabled={!draft.trim()}
            className="flex items-center gap-1 px-2 py-1.5 rounded-md text-[11px] bg-[var(--bg3)] text-[var(--t-mid)] hover:text-[var(--accent-hi)] disabled:opacity-40">
            <Plus size={12} /> Добавить
          </button>
        </div>
      )}
    </div>
  );
}
