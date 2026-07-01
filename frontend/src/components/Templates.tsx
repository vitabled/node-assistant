import { useState, useEffect } from "react";
import { Plus, Pencil, Trash2, CheckCircle2, Star, X, Save, Loader2 } from "lucide-react";

// ── Types ─────────────────────────────────────────────────────

interface Template {
  id: string;
  name: string;
  config: string;
  is_default: boolean;
}

// ── Template form modal ───────────────────────────────────────

function TemplateModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: Template;
  onSave: (name: string, config: string, is_default: boolean) => Promise<void>;
  onClose: () => void;
}) {
  const [name,       setName]       = useState(initial?.name ?? "");
  const [config,     setConfig]     = useState(initial?.config ?? "");
  const [isDefault,  setIsDefault]  = useState(initial?.is_default ?? false);
  const [saving,     setSaving]     = useState(false);
  const [jsonError,  setJsonError]  = useState<string | null>(null);

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
    try { await onSave(name, config, isDefault); }
    finally { setSaving(false); }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-950 border border-gray-700/60 rounded-xl w-full max-w-2xl
                      flex flex-col overflow-hidden shadow-2xl max-h-[90vh]">

        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-5 py-3.5
                        border-b border-gray-800">
          <h2 className="text-sm font-semibold text-white">
            {initial ? "Редактировать шаблон" : "Новый шаблон"}
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded text-gray-600 hover:text-gray-200 hover:bg-gray-800
                       transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">

          {/* Name */}
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
              Название
            </label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Xray VLESS + Reality"
              className="w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2
                         text-sm text-gray-100 placeholder:text-gray-700
                         focus:outline-none focus:ring-1 focus:border-blue-500/70
                         focus:ring-blue-500/20 transition-colors"
            />
          </div>

          {/* Config JSON */}
          <div className="flex flex-col gap-1 flex-1">
            <div className="flex items-center justify-between">
              <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
                Конфигурация Xray (JSON)
              </label>
              <span className="text-[11px] text-gray-600">
                переменные: <code className="text-blue-400">$domain</code>,{" "}
                <code className="text-blue-400">$name</code>
              </span>
            </div>
            <textarea
              value={config}
              onChange={e => handleConfig(e.target.value)}
              rows={16}
              spellCheck={false}
              placeholder='{"inbounds": [...]}'
              className={`w-full bg-gray-900/80 border rounded-md px-3 py-2.5 text-xs
                          font-mono text-gray-100 placeholder:text-gray-700 resize-y
                          focus:outline-none focus:ring-1 transition-colors
                          ${jsonError
                            ? "border-red-600/70 focus:ring-red-500/20"
                            : "border-gray-700/80 focus:border-blue-500/70 focus:ring-blue-500/20"
                          }`}
            />
            {jsonError && (
              <p className="text-[11px] text-red-400">{jsonError}</p>
            )}
            {!jsonError && config && (
              <p className="text-[11px] text-green-500">JSON валидный</p>
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
                          focus:ring-2 focus:ring-blue-500/40
                          ${isDefault ? "bg-blue-600" : "bg-gray-700"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                               transition-transform duration-200
                               ${isDefault ? "translate-x-4" : "translate-x-0"}`} />
            </button>
            <span className="text-sm text-gray-400 group-hover:text-gray-200 transition-colors">
              Шаблон по умолчанию
            </span>
          </label>
        </div>

        {/* Footer */}
        <div className="shrink-0 flex justify-end gap-2 px-5 py-3.5 border-t border-gray-800">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-md text-sm font-medium text-gray-400
                       hover:text-gray-200 hover:bg-gray-800 transition-colors"
          >
            Отмена
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !!jsonError}
            className="flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium
                       bg-blue-600 hover:bg-blue-500 text-white transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
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
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-gray-800/60
                    bg-gray-900/40 hover:bg-gray-900/60 transition-colors group">
      {/* Default star */}
      <button
        onClick={onSetDefault}
        title={tpl.is_default ? "Шаблон по умолчанию" : "Сделать по умолчанию"}
        className={`shrink-0 transition-colors ${
          tpl.is_default
            ? "text-yellow-400"
            : "text-gray-700 hover:text-gray-500"
        }`}
      >
        <Star size={14} fill={tpl.is_default ? "currentColor" : "none"} />
      </button>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-white truncate">{tpl.name}</p>
          {tpl.is_default && (
            <span className="shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded
                             bg-yellow-950/50 border border-yellow-800/40 text-yellow-500">
              по умолчанию
            </span>
          )}
        </div>
        {configPreview && (
          <p className="text-xs text-gray-600 truncate font-mono mt-0.5">{configPreview}</p>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onEdit}
          className="p-1.5 rounded text-gray-600 hover:text-gray-200 hover:bg-gray-700
                     transition-colors"
          title="Редактировать"
        >
          <Pencil size={13} />
        </button>
        <button
          onClick={onDelete}
          className="p-1.5 rounded text-gray-600 hover:text-red-400 hover:bg-red-950/30
                     transition-colors"
          title="Удалить"
        >
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

  const create = async (name: string, config: string, is_default: boolean) => {
    await fetch("/api/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config, is_default }),
    });
    setModal(null);
    load();
  };

  const update = async (id: string, name: string, config: string, is_default: boolean) => {
    await fetch(`/api/templates/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config, is_default }),
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
            <h1 className="text-base font-semibold text-white">Шаблоны конфигурации</h1>
            <p className="text-xs text-gray-500 mt-0.5">
              Xray JSON с переменными <code className="text-blue-400">$domain</code> и{" "}
              <code className="text-blue-400">$name</code>
            </p>
          </div>
          <button
            onClick={() => setModal({})}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                       bg-blue-600 hover:bg-blue-500 text-white transition-colors"
          >
            <Plus size={13} /> Новый шаблон
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={20} className="animate-spin text-gray-600" />
          </div>
        ) : templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-12 h-12 rounded-full bg-gray-800/60 flex items-center justify-center mb-4">
              <CheckCircle2 size={20} className="text-gray-600" />
            </div>
            <p className="text-gray-500 text-sm mb-1">Нет шаблонов</p>
            <p className="text-gray-700 text-xs mb-5">
              Создайте шаблон конфигурации Xray для автоматической регистрации в Remnawave
            </p>
            <button
              onClick={() => setModal({})}
              className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm
                         bg-blue-600 hover:bg-blue-500 text-white transition-colors"
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
              ? (n, c, d) => update(modal.editing!.id, n, c, d)
              : create
          }
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
