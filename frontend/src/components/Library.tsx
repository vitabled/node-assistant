import { useEffect, useState } from "react";
import { Loader2, Upload, FileText, StickyNote, Download, Trash2, Plus, Save, X } from "lucide-react";
import { toast } from "./infra/Toast";

interface Item {
  id: string;
  kind: "file" | "note";
  name: string;
  filename?: string;
  mime?: string;
  size?: number;
  created_at: number;
}

const fmtSize = (n?: number) => (n == null ? "" : n < 1024 ? `${n} Б` : n < 1048576 ? `${(n / 1024).toFixed(1)} КБ` : `${(n / 1048576).toFixed(1)} МБ`);

// Wave-5 Plan C (scoped) — «Библиотека»: files + markdown notes. Text extraction,
// full-text search and rich viewers are deferred.
export function Library() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [editing, setEditing] = useState<{ id: string | null; name: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  const load = async () => { try { setItems(await fetch("/api/library").then(r => r.json())); } catch { toast("Ошибка загрузки", "error"); } };
  useEffect(() => { load(); }, []);

  const upload = async (f: File) => {
    setBusy(true);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await fetch("/api/library/upload", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Ошибка");
      await load(); toast("Загружено", "success");
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); } finally { setBusy(false); }
  };

  const openNote = async (id: string | null) => {
    if (id) { const n = await fetch(`/api/library/notes/${id}`).then(r => r.json()); setEditing({ id, name: n.name, text: n.text || "" }); }
    else setEditing({ id: null, name: "", text: "" });
  };
  const saveNote = async () => {
    if (!editing) return;
    const name = editing.name.trim();
    if (!name) { toast("Имя обязательно", "error"); return; }
    try {
      const url = editing.id ? `/api/library/notes/${editing.id}` : "/api/library/notes";
      const r = await fetch(url, { method: editing.id ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, text: editing.text }) });
      if (!r.ok) throw new Error();
      setEditing(null); await load(); toast("Сохранено", "success");
    } catch { toast("Ошибка сохранения", "error"); }
  };
  const del = async (id: string) => {
    if (confirmDel !== id) { setConfirmDel(id); setTimeout(() => setConfirmDel(c => (c === id ? null : c)), 3000); return; }
    setConfirmDel(null);
    try { await fetch(`/api/library/${id}`, { method: "DELETE" }); await load(); toast("Удалено", "success"); } catch { toast("Ошибка", "error"); }
  };
  const download = async (it: Item) => {
    try {
      const r = await fetch(`/api/library/files/${it.id}`); if (!r.ok) throw new Error();
      const blob = await r.blob(); const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = it.filename || it.name; a.click(); URL.revokeObjectURL(url);
    } catch { toast("Не удалось скачать", "error"); }
  };

  return (
    <div className="ni-pagebody" style={{ flex: 1, overflowY: "auto", padding: 20 }}>
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Библиотека</h2>
          <p className="hint">Заметки и файлы (pdf/doc/xlsx/…). Хранятся приватно в вашем аккаунте.</p>
        </div>
        <div className="ni-pagehead-actions" style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className="btn btn-sm" onClick={() => openNote(null)}><Plus size={13} /> Заметка</button>
          <label className="btn btn-sm" style={{ opacity: busy ? 0.5 : 1, cursor: busy ? "not-allowed" : "pointer" }}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />} Загрузить
            <input type="file" style={{ display: "none" }} disabled={busy}
              onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.currentTarget.value = ""; }} />
          </label>
        </div>
      </div>

      {items === null ? (
        <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-10"><Loader2 size={16} className="animate-spin" /> Загрузка...</div>
      ) : items.length === 0 ? (
        <p className="hint">Пусто. Создайте заметку или загрузите файл.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {items.map(it => (
            <div key={it.id} className="card" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px" }}>
              {it.kind === "note" ? <StickyNote size={15} style={{ color: "var(--accent-hi)" }} /> : <FileText size={15} style={{ color: "var(--t-low)" }} />}
              <span style={{ flex: 1, fontSize: 13, color: "var(--t-hi)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{it.name}</span>
              {it.kind === "file" && <span className="micro">{fmtSize(it.size)}</span>}
              {it.kind === "note"
                ? <button className="iconbtn" title="Редактировать" onClick={() => openNote(it.id)}><FileText size={14} /></button>
                : <button className="iconbtn" title="Скачать" onClick={() => download(it)}><Download size={14} /></button>}
              <button className={`iconbtn ${confirmDel === it.id ? "text-[var(--err)]" : ""}`} title="Удалить" onClick={() => del(it.id)}><Trash2 size={14} /></button>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="overlay" onClick={() => setEditing(null)}>
          <div className="modal" style={{ maxWidth: 720, width: "100%" }} onClick={e => e.stopPropagation()}>
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: "var(--t-hi)" }}>{editing.id ? "Заметка" : "Новая заметка"}</span>
              <input className="input" placeholder="Название" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} />
              <textarea className="input font-mono text-xs" placeholder="Markdown…" value={editing.text}
                onChange={e => setEditing({ ...editing, text: e.target.value })} style={{ minHeight: 320, resize: "vertical" }} />
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn" onClick={() => setEditing(null)}><X size={13} /> Отмена</button>
                <button className="btn btn-primary" onClick={saveNote}><Save size={13} /> Сохранить</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
