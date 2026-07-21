import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Pencil, Trash2, Upload, FileJson, FileCode2 } from "lucide-react";
import { toast } from "../infra/Toast";
import { JsonEditor } from "../profiles/JsonEditor";
import { configApi, KINDS, coreOf, labelOf, type ConfigTemplate, type TemplateKind } from "./api";

// The type→editor framework: JSON cores open in our schema-validated JsonEditor
// (the Xray-JSON binding — stateless, never touches the global xray_profile store);
// YAML cores open in a plain editor. Mihomo gets a visual editor in Plan E.
function TemplateEditor(
  { kind, value, onChange }: { kind: TemplateKind; value: string; onChange: (v: string) => void },
) {
  if (coreOf(kind) === "json") {
    return <JsonEditor value={value} onChange={onChange} schemaMode="full" />;
  }
  return (
    <div className="flex flex-col gap-1">
      {kind === "mihomo" && (
        <p className="hint">Визуальный редактор Mihomo появится в Плане E. Пока — текстовый YAML.</p>
      )}
      <textarea className="input font-mono text-xs" style={{ minHeight: 320, resize: "vertical" }}
        value={value} onChange={e => onChange(e.target.value)} spellCheck={false}
        placeholder={"proxies: []\nproxy-groups: []\nrules: []"} />
    </div>
  );
}

interface Editing { tpl: ConfigTemplate | null; kind: TemplateKind; name: string; content: string }

export function ConfigTemplates() {
  const [items, setItems] = useState<ConfigTemplate[] | null>(null);
  const [edit, setEdit] = useState<Editing | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = async () => {
    try { setItems(await configApi.list()); }
    catch (e) { toast(e instanceof Error ? e.message : "Ошибка загрузки", "error"); }
  };
  useEffect(() => { load(); }, []);

  const grouped = useMemo(() => {
    const by: Record<string, ConfigTemplate[]> = {};
    for (const t of items ?? []) (by[t.kind] ||= []).push(t);
    return by;
  }, [items]);

  const openNew = (kind: TemplateKind) =>
    setEdit({ tpl: null, kind, name: "", content: coreOf(kind) === "json" ? "{\n  \n}" : "" });

  const openEdit = (t: ConfigTemplate) =>
    setEdit({
      tpl: t, kind: t.kind, name: t.name,
      content: coreOf(t.kind) === "json"
        ? JSON.stringify(t.content_json ?? {}, null, 2)
        : (t.content_yaml ?? ""),
    });

  const save = async () => {
    if (!edit) return;
    const name = edit.name.trim();
    if (!name) { toast("Укажите имя шаблона", "error"); return; }
    let body: Partial<ConfigTemplate> = { name, kind: edit.kind, content_json: null, content_yaml: null };
    if (coreOf(edit.kind) === "json") {
      try { body.content_json = JSON.parse(edit.content || "{}"); }
      catch { toast("Невалидный JSON", "error"); return; }
    } else {
      body.content_yaml = edit.content;
    }
    setSaving(true);
    try {
      if (edit.tpl) await configApi.update(edit.tpl.id, body);
      else await configApi.create(body);
      setEdit(null);
      await load();
      toast("Сохранено", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка сохранения", "error");
    } finally { setSaving(false); }
  };

  const remove = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); setTimeout(() => setConfirmId(c => (c === id ? null : c)), 3000); return; }
    setConfirmId(null);
    try { await configApi.remove(id); await load(); toast("Удалено", "success"); }
    catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
  };

  const exportToPanel = async (id: string) => {
    try { await configApi.exportToPanel(id); toast("Отправлено в панель Remnawave", "success"); }
    catch (e) { toast(e instanceof Error ? e.message : "Не удалось (панель не настроена?)", "error"); }
  };

  return (
    <div className="ni-pagebody" style={{ flex: 1, overflowY: "auto", padding: 20 }}>
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Пользовательские конфиги</h2>
          <p className="hint">Шаблоны конфигов по типам клиента (как subscription-templates Remnawave).</p>
        </div>
        <div className="ni-pagehead-actions" style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {KINDS.map(k => (
            <button key={k.key} className="btn btn-sm" onClick={() => openNew(k.key)}>
              <Plus size={13} /> {k.label}
            </button>
          ))}
        </div>
      </div>

      {items === null ? (
        <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-10">
          <Loader2 size={16} className="animate-spin" /> Загрузка...
        </div>
      ) : items.length === 0 ? (
        <p className="hint">Шаблонов пока нет. Создайте первый кнопкой выше.</p>
      ) : (
        KINDS.filter(k => grouped[k.key]?.length).map(k => (
          <div key={k.key} style={{ marginBottom: 18 }}>
            <p className="micro" style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
              {k.core === "json" ? <FileJson size={13} /> : <FileCode2 size={13} />} {k.label}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {grouped[k.key].map(t => (
                <div key={t.id} className="card" style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px" }}>
                  <span style={{ flex: 1, fontSize: 13, color: "var(--t-hi)" }}>{t.name}</span>
                  <button className="iconbtn" title="Отправить в панель" onClick={() => exportToPanel(t.id)}><Upload size={14} /></button>
                  <button className="iconbtn" title="Редактировать" onClick={() => openEdit(t)}><Pencil size={14} /></button>
                  <button className={`iconbtn ${confirmId === t.id ? "text-[var(--err)]" : ""}`} title="Удалить" onClick={() => remove(t.id)}><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      {edit && (
        <div className="overlay" onClick={() => !saving && setEdit(null)}>
          <div className="modal" style={{ maxWidth: 760, width: "100%" }} onClick={e => e.stopPropagation()}>
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 14, fontWeight: 700, color: "var(--t-hi)" }}>
                  {edit.tpl ? "Редактирование" : "Новый шаблон"} · {labelOf(edit.kind)}
                </span>
              </div>
              <label className="flex flex-col gap-1">
                <span className="micro">Имя</span>
                <input className="input" value={edit.name} disabled={saving}
                  onChange={e => setEdit({ ...edit, name: e.target.value })} />
              </label>
              <div className="flex flex-col gap-1">
                <span className="micro">Содержимое</span>
                <TemplateEditor kind={edit.kind} value={edit.content}
                  onChange={v => setEdit({ ...edit, content: v })} />
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button className="btn" disabled={saving} onClick={() => setEdit(null)}>Отмена</button>
                <button className="btn btn-primary" disabled={saving} onClick={save}>
                  {saving ? <Loader2 size={14} className="animate-spin" /> : null} Сохранить
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
