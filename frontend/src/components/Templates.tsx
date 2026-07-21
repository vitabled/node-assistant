import { useState, useEffect } from "react";
import { Plus, Pencil, Trash2, CheckCircle2, Star, X, Save, Loader2 } from "lucide-react";
import { MultiSelect, type SelectOption } from "./MultiSelect";
import { JsonEditor } from "./profiles/JsonEditor";

// ── Types ─────────────────────────────────────────────────────

interface Template {
  id: string;
  name: string;
  config: string;
  is_default: boolean;
  // Local host-templates auto-created as Remnawave hosts at deploy time (Ф6).
  host_template_ids?: string[];
}

// ── Template form modal ───────────────────────────────────────

function TemplateModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: Template;
  onSave: (name: string, config: string, is_default: boolean, host_template_ids: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const [name,       setName]       = useState(initial?.name ?? "");
  const [config,     setConfig]     = useState(initial?.config ?? "");
  const [isDefault,  setIsDefault]  = useState(initial?.is_default ?? false);
  const [hostIds,    setHostIds]    = useState<string[]>(initial?.host_template_ids ?? []);
  const [hostOpts,   setHostOpts]   = useState<SelectOption[]>([]);
  const [saving,     setSaving]     = useState(false);
  const [jsonError,  setJsonError]  = useState<string | null>(null);

  // Host-templates to bind → auto-created as Remnawave hosts at deploy (Ф6).
  useEffect(() => {
    fetch("/api/hosts")
      .then(r => r.json())
      .then(list => {
        if (Array.isArray(list))
          setHostOpts(list.map((h: { id: string; remark: string }) => ({ value: h.id, label: h.remark })));
      })
      .catch(() => {});
  }, []);

  const validateJson = (v: string) => {
    try { JSON.parse(v); setJsonError(null); }
    catch (e) { setJsonError((e as Error).message); }
  };

  const handleConfig = (v: string) => {
    setConfig(v);
    if (v) validateJson(v);
    else setJsonError(null);
  };

  const handleSave = async () => {
    if (!name.trim()) return;
    if (config) validateJson(config);
    if (jsonError) return;
    setSaving(true);
    try { await onSave(name, config, isDefault, hostIds); }
    finally { setSaving(false); }
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-2xl">

        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-5 py-3.5"
             style={{ borderBottom: "1px solid var(--line-soft)" }}>
          <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
            {initial ? "Редактировать шаблон" : "Новый шаблон"}
          </h2>
          <button onClick={onClose} className="iconbtn">
            <X size={15} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">

          {/* Name */}
          <div className="flex flex-col gap-1">
            <label className="label">
              Название
            </label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Xray VLESS + Reality"
              className="input"
            />
          </div>

          {/* Config JSON */}
          <div className="flex flex-col gap-1 flex-1">
            <div className="flex items-center justify-between">
              <label className="label">
                Конфигурация Xray (JSON)
              </label>
              <span className="text-[11px]" style={{ color: "var(--t-faint)" }}>
                переменные: <code>$domain</code>,{" "}
                <code>$xhttp_path</code>, <code>$name</code>
              </span>
            </div>
            {/* CodeMirror editor with syntax highlighting + error underlining (4a),
                reused from the profile editor. Fixed-height container (JsonEditor
                fills 100%). handleConfig keeps the JSON.parse save-gating. */}
            <div style={{ height: 340 }}>
              <JsonEditor value={config} onChange={handleConfig} />
            </div>
            {jsonError && (
              <p className="errmsg">{jsonError}</p>
            )}
            {!jsonError && config && (
              <p className="text-[11px]" style={{ color: "var(--ok)" }}>JSON валидный</p>
            )}
          </div>

          {/* Default toggle */}
          <label className="flex items-center gap-3 cursor-pointer select-none group">
            <button
              type="button"
              role="switch"
              aria-checked={isDefault}
              onClick={() => setIsDefault(v => !v)}
              className={`relative w-9 h-5 rounded-full transition-colors focus:outline-none
                          focus:ring-2 focus:ring-[var(--accent-line)]
                          ${isDefault ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                               transition-transform duration-200
                               ${isDefault ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <span className="text-sm text-[var(--t-low)] group-hover:text-[var(--t-hi)] transition-colors">
              Шаблон по умолчанию
            </span>
          </label>

          {/* Host-templates → auto-created as Remnawave hosts at deploy (Ф6). */}
          <MultiSelect
            label="Хосты Remnawave (создать при деплое)"
            selected={hostIds}
            onChange={setHostIds}
            options={hostOpts}
            placeholder={hostOpts.length ? "— без хостов —" : "Нет сохранённых хостов"}
            disabled={!hostOpts.length}
          />
        </div>

        {/* Footer */}
        <div className="shrink-0 flex justify-end gap-2 px-5 py-3.5"
             style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button onClick={onClose} className="btn btn-ghost">
            Отмена
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !!jsonError}
            className="btn btn-primary"
          >
            {saving
              ? <><Loader2 size={13} className="animate-spin" /> Сохранение...</>
              : <><Save size={13} /> Сохранить</>
            }
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Template row ──────────────────────────────────────────────

function TemplateRow({
  tpl,
  onEdit,
  onDelete,
  onSetDefault,
}: {
  tpl: Template;
  onEdit: () => void;
  onDelete: () => void;
  onSetDefault: () => void;
}) {
  let configPreview = "";
  try {
    const parsed = JSON.parse(tpl.config);
    configPreview = JSON.stringify(parsed).slice(0, 80);
    if (JSON.stringify(parsed).length > 80) configPreview += "...";
  } catch {
    configPreview = tpl.config.slice(0, 80);
  }

  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg hover:bg-[var(--bg3)] transition-colors group"
         style={{ border: "1px solid var(--line-soft)", background: "var(--bg2)" }}>
      {/* Default star */}
      <button
        onClick={onSetDefault}
        title={tpl.is_default ? "Шаблон по умолчанию" : "Сделать по умолчанию"}
        className={`shrink-0 transition-colors ${
          tpl.is_default
            ? "text-[var(--warn)]"
            : "text-[var(--t-faint)] hover:text-[var(--t-low)]"
        }`}
      >
        <Star size={14} fill={tpl.is_default ? "currentColor" : "none"} />
      </button>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium truncate" style={{ color: "var(--t-hi)" }}>{tpl.name}</p>
          {tpl.is_default && (
            <span className="chip warn shrink-0">
              по умолчанию
            </span>
          )}
        </div>
        {configPreview && (
          <p className="text-xs truncate font-mono mt-0.5" style={{ color: "var(--t-faint)" }}>{configPreview}</p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button onClick={onEdit} className="iconbtn" title="Редактировать">
          <Pencil size={13} />
        </button>
        <button onClick={onDelete} className="iconbtn danger" title="Удалить">
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  );
}

// ── Main Templates page ───────────────────────────────────────

export function Templates() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [modal,     setModal]     = useState<{ editing?: Template } | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetch("/api/templates").then(r => r.json());
      setTemplates(Array.isArray(data) ? data : []);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const create = async (name: string, config: string, is_default: boolean, host_template_ids: string[]) => {
    await fetch("/api/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config, is_default, host_template_ids }),
    });
    setModal(null);
    load();
  };

  const update = async (id: string, name: string, config: string, is_default: boolean, host_template_ids: string[]) => {
    await fetch(`/api/templates/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config, is_default, host_template_ids }),
    });
    setModal(null);
    load();
  };

  const remove = async (id: string) => {
    if (!confirm("Удалить шаблон?")) return;
    await fetch(`/api/templates/${id}`, { method: "DELETE" });
    load();
  };

  const setDefault = async (tpl: Template) => {
    await fetch(`/api/templates/${tpl.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_default: true }),
    });
    load();
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6">

        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-base font-semibold" style={{ color: "var(--t-hi)" }}>Шаблоны конфигурации</h1>
            <p className="text-xs mt-0.5" style={{ color: "var(--t-low)" }}>
              Xray JSON с переменными <code>$domain</code>, <code>$xhttp_path</code>,{" "}
              <code>$name</code>
            </p>
          </div>
          <button
            onClick={() => setModal({})}
            className="btn btn-primary"
          >
            <Plus size={13} /> Новый шаблон
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={20} className="animate-spin" style={{ color: "var(--t-faint)" }} />
          </div>
        ) : templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-12 h-12 rounded-full flex items-center justify-center mb-4"
                 style={{ background: "var(--bg3)" }}>
              <CheckCircle2 size={20} style={{ color: "var(--t-faint)" }} />
            </div>
            <p className="text-sm mb-1" style={{ color: "var(--t-low)" }}>Нет шаблонов</p>
            <p className="text-xs mb-5" style={{ color: "var(--t-faint)" }}>
              Создайте шаблон конфигурации Xray для автоматической регистрации в Remnawave
            </p>
            <button
              onClick={() => setModal({})}
              className="flex items-center gap-1.5 btn btn-primary"
            >
              <Plus size={14} /> Создать шаблон
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {templates.map(tpl => (
              <TemplateRow
                key={tpl.id}
                tpl={tpl}
                onEdit={() => setModal({ editing: tpl })}
                onDelete={() => remove(tpl.id)}
                onSetDefault={() => setDefault(tpl)}
              />
            ))}
          </div>
        )}
      </div>

      {modal !== null && (
        <TemplateModal
          initial={modal.editing}
          onSave={
            modal.editing
              ? (n, c, d, h) => update(modal.editing!.id, n, c, d, h)
              : create
          }
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
