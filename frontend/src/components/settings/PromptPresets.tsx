import { useEffect, useState } from "react";
import { Loader2, GitFork, Plus, Pencil, Trash2, Save, X } from "lucide-react";
import { toast } from "../infra/Toast";

interface Preset {
  id: string;
  name: string;
  text: string;
  builtin: boolean;
  source_url?: string | null;
  license?: string | null;
  unavailable?: boolean;
}

// System-prompt presets for the AI agent (Wave-5 Plan I). Self-contained: reads
// the active preset from /api/ai/config and persists changes back through it.
export function PromptPresets() {
  const [presets, setPresets] = useState<Preset[]>([]);
  const [activeId, setActiveId] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<{ id: string | null; name: string; text: string } | null>(null);

  const load = async () => {
    try {
      const [pr, cfg] = await Promise.all([
        fetch("/api/ai/prompts").then(r => r.json()),
        fetch("/api/ai/config").then(r => r.json()),
      ]);
      setPresets(pr);
      setActiveId(cfg.active_preset_id || "");
    } catch { toast("Не удалось загрузить пресеты", "error"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const active = presets.find(p => p.id === activeId) || presets.find(p => p.id === "default");

  const setActive = async (id: string) => {
    setActiveId(id);
    try {
      const cfg = await fetch("/api/ai/config").then(r => r.json());
      const r = await fetch("/api/ai/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...cfg, active_preset_id: id }),
      });
      if (!r.ok) throw new Error();
      toast("Активный пресет обновлён", "success");
    } catch { toast("Не удалось сохранить выбор", "error"); }
  };

  const fork = async (id: string) => {
    try { await fetch(`/api/ai/prompts/${id}/fork`, { method: "POST" }); await load(); toast("Форк создан", "success"); }
    catch { toast("Ошибка", "error"); }
  };
  const del = async (id: string) => {
    try { const r = await fetch(`/api/ai/prompts/${id}`, { method: "DELETE" }); if (!r.ok) throw new Error(); await load(); toast("Удалено", "success"); }
    catch { toast("Ошибка", "error"); }
  };
  const saveEdit = async () => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!name || !editing.text.trim()) { toast("Имя и текст обязательны", "error"); return; }
    try {
      const url = editing.id ? `/api/ai/prompts/${editing.id}` : "/api/ai/prompts";
      const r = await fetch(url, { method: editing.id ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, text: editing.text }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Ошибка");
      setEditing(null); await load(); toast("Сохранено", "success");
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
  };

  if (loading) return <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-6"><Loader2 size={16} className="animate-spin" /> Загрузка...</div>;

  return (
    <div className="card card-p flex flex-col gap-3">
      <span className="text-sm font-semibold text-[var(--t-hi)]">Инструкции (системный промпт)</span>
      <label className="flex flex-col gap-1">
        <span className="micro">Активный пресет</span>
        <select className="input" value={activeId || "default"} onChange={e => setActive(e.target.value)}>
          {presets.map(p => (
            <option key={p.id} value={p.id} disabled={p.unavailable}>
              {p.name}{p.builtin ? " · встроенный" : ""}{p.unavailable ? " (недоступен)" : ""}
            </option>
          ))}
        </select>
      </label>

      {active && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="micro">Превью</span>
            {active.license && <span className="chip" style={{ fontSize: 10 }}>{active.license}</span>}
            {active.source_url && <a href={active.source_url} target="_blank" rel="noreferrer" className="micro" style={{ color: "var(--accent-hi)" }}>источник</a>}
            <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
              {active.builtin
                ? <button className="btn btn-sm" onClick={() => fork(active.id)} title="Форкнуть в свой"><GitFork size={13} /> Форк</button>
                : <>
                    <button className="btn btn-sm" onClick={() => setEditing({ id: active.id, name: active.name, text: active.text })}><Pencil size={13} /></button>
                    <button className="btn btn-sm" onClick={() => del(active.id)}><Trash2 size={13} /></button>
                  </>}
              <button className="btn btn-sm" onClick={() => setEditing({ id: null, name: "", text: "" })}><Plus size={13} /> Новый</button>
            </div>
          </div>
          <textarea className="input font-mono text-xs" readOnly value={active.text} style={{ minHeight: 120, resize: "vertical" }} />
          {active.unavailable && <p className="hint" style={{ color: "var(--warn)" }}>Пресет не вендорен — активировать нельзя. См. источник.</p>}
        </div>
      )}

      {editing && (
        <div className="flex flex-col gap-2 border-t border-[var(--line-soft)] pt-3">
          <input className="input" placeholder="Имя пресета" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} />
          <textarea className="input font-mono text-xs" placeholder="Текст системного промпта" value={editing.text}
            onChange={e => setEditing({ ...editing, text: e.target.value })} style={{ minHeight: 140, resize: "vertical" }} />
          <div className="flex justify-end gap-2">
            <button className="btn btn-sm" onClick={() => setEditing(null)}><X size={13} /> Отмена</button>
            <button className="btn btn-sm btn-primary" onClick={saveEdit}><Save size={13} /> Сохранить</button>
          </div>
        </div>
      )}
    </div>
  );
}
