import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, RefreshCw, AlertCircle, CheckCircle2,
         XCircle, Loader2, X, Clock } from "lucide-react";
import { MultiSelect, type SelectOption } from "./MultiSelect";

// ── Types ──────────────────────────────────────────────────────────────────

type Scope     = "ALL" | "SQUAD";
type Period    = "DAY" | "WEEK" | "MONTH" | "NO_RESET";
type SyncSt    = "pending" | "synced" | "error";

interface TrafficRule {
  id:             string;
  node_uuid:      string;
  node_name:      string;
  scope:          Scope;
  squad_uuids:    string[];
  squad_names:    string[];
  limit_gb:       number;
  period:         Period;
  created_at:     string;
  last_synced_at: string | null;
  sync_status:    SyncSt;
  sync_error:     string | null;
}

interface RwNode  { uuid: string; name: string; address: string }
interface Squad   { uuid: string; name: string }

interface FormState {
  node_uuid:   string;
  node_name:   string;
  scope:       Scope;
  squad_uuids: string[];
  limit_gb:    string;
  period:      Period;
}

const DEFAULT_FORM: FormState = {
  node_uuid:   "",
  node_name:   "",
  scope:       "ALL",
  squad_uuids: [],
  limit_gb:    "10",
  period:      "MONTH",
};

// ── Helpers ────────────────────────────────────────────────────────────────

const PERIOD_LABELS: Record<Period, string> = {
  DAY:      "В день",
  WEEK:     "В неделю",
  MONTH:    "В месяц",
  NO_RESET: "Без ограничений",
};

function fmtLimit(rule: TrafficRule): string {
  if (rule.limit_gb === 0 || rule.period === "NO_RESET") return "Без лимита";
  return `${rule.limit_gb} ГБ`;
}

function SyncBadge({ rule, onSync }: { rule: TrafficRule; onSync: () => void }) {
  const [loading, setLoading] = useState(false);
  const handle = async () => {
    setLoading(true);
    await onSync();
    setLoading(false);
  };

  if (rule.sync_status === "synced") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                       bg-green-950/60 border border-green-800/50 text-green-400 text-xs">
        <CheckCircle2 size={10} /> Синхр.
      </span>
    );
  }
  if (rule.sync_status === "error") {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer
                   bg-red-950/60 border border-red-800/50 text-red-400 text-xs
                   hover:bg-red-900/50 transition-colors"
        title={rule.sync_error ?? "Ошибка"}
        onClick={handle}
      >
        {loading ? <Loader2 size={10} className="animate-spin" /> : <XCircle size={10} />}
        Ошибка
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer
                 bg-yellow-950/60 border border-yellow-800/50 text-yellow-400 text-xs
                 hover:bg-yellow-900/50 transition-colors"
      onClick={handle}
    >
      {loading ? <Loader2 size={10} className="animate-spin" /> : <Clock size={10} />}
      Ожидание
    </span>
  );
}

// ── Modal ──────────────────────────────────────────────────────────────────

interface ModalProps {
  initial:       FormState;
  nodes:         RwNode[];
  intSquads:     SelectOption[];
  extSquads:     SelectOption[];
  squadsLoading: boolean;
  onClose:       () => void;
  onSave:        (f: FormState) => Promise<void>;
}

function Modal({ initial, nodes, intSquads, extSquads, squadsLoading, onClose, onSave }: ModalProps) {
  const [form,      setForm]      = useState<FormState>(initial);
  const [saving,    setSaving]    = useState(false);
  const [apiError,  setApiError]  = useState<string | null>(null);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.node_uuid) { setApiError("Выберите ноду"); return; }
    if (form.scope === "SQUAD" && !form.squad_uuids.length) {
      setApiError("Выберите хотя бы один сквад"); return;
    }
    const gb = parseFloat(form.limit_gb);
    if (isNaN(gb) || gb < 0) { setApiError("Объём трафика — неверное значение"); return; }
    if (form.scope === "ALL" && form.period !== "MONTH" && form.period !== "NO_RESET") {
      setApiError(
        'Для области «Все пользователи» поддерживается только период «В месяц» или '
        + '«Без ограничений» — Remnawave управляет нодовым лимитом с ежемесячным сбросом.'
      );
      return;
    }
    setApiError(null);
    setSaving(true);
    try {
      await onSave({ ...form, limit_gb: form.limit_gb });
    } catch (err) {
      setApiError(err instanceof Error ? err.message : "Ошибка сервера");
    } finally {
      setSaving(false);
    }
  };

  const allSquadOptions: SelectOption[] = [
    ...intSquads.map(s => ({ ...s, label: `[INT] ${s.label}` })),
    ...extSquads.map(s => ({ ...s, label: `[EXT] ${s.label}` })),
  ];
  const selectedSquadOptions = allSquadOptions.filter(o => form.squad_uuids.includes(o.value));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-full max-w-md bg-gray-900 border border-gray-700/60 rounded-xl
                      shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800/60">
          <h2 className="text-sm font-semibold text-white">Ограничение трафика</h2>
          <button onClick={onClose}
            className="text-gray-600 hover:text-gray-300 transition-colors focus:outline-none">
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="overflow-y-auto flex flex-col gap-4 p-5">

          {/* Node */}
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
              Нода
            </label>
            <select
              value={form.node_uuid}
              onChange={e => {
                const node = nodes.find(n => n.uuid === e.target.value);
                set("node_uuid", e.target.value);
                set("node_name", node?.name ?? "");
              }}
              disabled={saving}
              className="w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2
                         text-sm text-gray-100 focus:outline-none focus:ring-1
                         focus:border-blue-500/70 focus:ring-blue-500/20
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <option value="">— выберите ноду —</option>
              {nodes.map(n => (
                <option key={n.uuid} value={n.uuid}>{n.name} ({n.address})</option>
              ))}
            </select>
          </div>

          {/* Scope */}
          <div className="flex flex-col gap-2">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
              Кому применить
            </label>
            <div className="flex gap-3">
              {(["ALL", "SQUAD"] as Scope[]).map(s => (
                <label key={s}
                  className="flex items-center gap-2 cursor-pointer text-sm text-gray-400
                             hover:text-gray-200 transition-colors select-none">
                  <input type="radio" name="scope" value={s}
                    checked={form.scope === s}
                    onChange={() => set("scope", s)}
                    disabled={saving}
                    className="accent-blue-500"
                  />
                  {s === "ALL" ? "Все пользователи (по умолчанию)" : "Конкретный сквад"}
                </label>
              ))}
            </div>
            {form.scope === "ALL" && (
              <p className="text-[11px] text-amber-500/80">
                Ограничение применяется как квота пропускной способности ноды в целом
                (суммарный трафик через сервер). Поддерживается только период «В месяц»
                или «Без ограничений».
              </p>
            )}
            {form.scope === "SQUAD" && (
              <p className="text-[11px] text-blue-400/70">
                Лимиты применяются к каждому пользователю сквада глобально
                (не ограничены одной нодой) — такова возможность API Remnawave.
              </p>
            )}
          </div>

          {/* Squad multi-select (only when scope=SQUAD) */}
          {form.scope === "SQUAD" && (
            <MultiSelect
              label="Сквады"
              selected={form.squad_uuids}
              onChange={uuids => {
                set("squad_uuids", uuids);
                const names = allSquadOptions
                  .filter(o => uuids.includes(o.value))
                  .map(o => o.label);
                set("squad_names" as keyof FormState, names as any);
              }}
              options={allSquadOptions}
              placeholder={squadsLoading ? "Загрузка..." : "— выберите сквады —"}
              disabled={saving || squadsLoading}
            />
          )}

          {/* Limit GB */}
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
              Объём трафика (ГБ)
            </label>
            <input
              type="number"
              min="0"
              step="any"
              value={form.limit_gb}
              onChange={e => set("limit_gb", e.target.value)}
              disabled={saving || form.period === "NO_RESET"}
              placeholder="0 = без лимита"
              className="w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2
                         text-sm text-gray-100 focus:outline-none focus:ring-1
                         focus:border-blue-500/70 focus:ring-blue-500/20
                         disabled:opacity-40 disabled:cursor-not-allowed"
            />
            <p className="text-[11px] text-gray-600">0 = без ограничений</p>
          </div>

          {/* Period */}
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
              Период обновления лимита
            </label>
            <select
              value={form.period}
              onChange={e => {
                const p = e.target.value as Period;
                set("period", p);
                if (p === "NO_RESET") set("limit_gb", "0");
              }}
              disabled={saving}
              className="w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2
                         text-sm text-gray-100 focus:outline-none focus:ring-1
                         focus:border-blue-500/70 focus:ring-blue-500/20
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {(Object.entries(PERIOD_LABELS) as [Period, string][])
                .filter(([p]) => form.scope === "ALL" ? (p === "MONTH" || p === "NO_RESET") : true)
                .map(([val, label]) => (
                  <option key={val} value={val}>{label}</option>
                ))}
            </select>
          </div>

          {/* API error */}
          {apiError && (
            <div className="px-3 py-2 rounded-md bg-red-950/50 border border-red-800/50
                            text-xs text-red-400">
              {apiError}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-1">
            <button type="submit" disabled={saving}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                         font-semibold text-sm bg-blue-600 hover:bg-blue-500 transition-all
                         active:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed
                         focus:outline-none focus:ring-2 focus:ring-blue-500/50">
              {saving
                ? <><Loader2 size={14} className="animate-spin" /> Сохранение...</>
                : "Сохранить и применить"
              }
            </button>
            <button type="button" onClick={onClose} disabled={saving}
              className="px-4 py-2.5 rounded-lg text-sm font-medium text-gray-400
                         hover:text-gray-200 hover:bg-gray-800 transition-colors
                         focus:outline-none focus:ring-1 focus:ring-gray-700">
              Отмена
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function TrafficRules() {
  const [rules,         setRules]         = useState<TrafficRule[]>([]);
  const [nodes,         setNodes]         = useState<RwNode[]>([]);
  const [intSquads,     setIntSquads]     = useState<SelectOption[]>([]);
  const [extSquads,     setExtSquads]     = useState<SelectOption[]>([]);
  const [squadsLoading, setSquadsLoading] = useState(false);
  const [loading,       setLoading]       = useState(true);
  const [rwError,       setRwError]       = useState<string | null>(null);

  const [showModal,  setShowModal]  = useState(false);
  const [editRule,   setEditRule]   = useState<TrafficRule | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // ── Data loading ──────────────────────────────────────────────

  const loadRules = async () => {
    try {
      const r = await fetch("/api/traffic-rules");
      const data = await r.json();
      setRules(Array.isArray(data) ? data : []);
    } catch { setRules([]); }
    setLoading(false);
  };

  useEffect(() => {
    loadRules();
    // Load nodes + squads from Remnawave
    setSquadsLoading(true);
    Promise.all([
      fetch("/api/remnawave/nodes").then(r => r.ok ? r.json() : []).catch(() => []),
      fetch("/api/remnawave/squads/internal").then(r => r.ok ? r.json() : []).catch(() => []),
      fetch("/api/remnawave/squads/external").then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(([ns, ints, exts]) => {
      if (Array.isArray(ns) && ns.length === 0 && !Array.isArray(ns)) {
        setRwError("Remnawave не настроен или недоступен");
      }
      setNodes(Array.isArray(ns) ? ns : []);
      setIntSquads((Array.isArray(ints) ? ints : []).map((s: Squad) => ({ value: s.uuid, label: s.name })));
      setExtSquads((Array.isArray(exts) ? exts : []).map((s: Squad) => ({ value: s.uuid, label: s.name })));
    }).catch(() => setRwError("Ошибка загрузки данных Remnawave"))
      .finally(() => setSquadsLoading(false));
  }, []);

  // ── Actions ───────────────────────────────────────────────────

  const handleSave = async (form: FormState) => {
    const body = {
      node_uuid:   form.node_uuid,
      node_name:   form.node_name,
      scope:       form.scope,
      squad_uuids: form.scope === "SQUAD" ? form.squad_uuids : [],
      squad_names: form.scope === "SQUAD"
        ? [...intSquads, ...extSquads]
            .filter(o => form.squad_uuids.includes(o.value))
            .map(o => o.label)
        : [],
      limit_gb:    parseFloat(form.limit_gb) || 0,
      period:      form.period,
    };

    let res: Response;
    if (editRule) {
      res = await fetch(`/api/traffic-rules/${editRule.id}`, {
        method:  "PATCH",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
    } else {
      res = await fetch("/api/traffic-rules", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
      });
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    setShowModal(false);
    setEditRule(null);
    await loadRules();
  };

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    await fetch(`/api/traffic-rules/${id}`, { method: "DELETE" });
    setDeletingId(null);
    await loadRules();
  };

  const handleSync = async (rule: TrafficRule) => {
    await fetch(`/api/traffic-rules/${rule.id}/sync`, { method: "POST" });
    await loadRules();
  };

  const openCreate = () => { setEditRule(null); setShowModal(true); };
  const openEdit   = (r: TrafficRule) => { setEditRule(r); setShowModal(true); };

  const modalInitial: FormState = editRule
    ? {
        node_uuid:   editRule.node_uuid,
        node_name:   editRule.node_name,
        scope:       editRule.scope,
        squad_uuids: editRule.squad_uuids,
        limit_gb:    String(editRule.limit_gb),
        period:      editRule.period,
      }
    : { ...DEFAULT_FORM };

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header bar */}
      <div className="shrink-0 h-11 border-b border-gray-800/80 px-4 flex items-center gap-3">
        <span className="text-sm font-medium text-white">Ограничение трафика</span>
        <button
          onClick={openCreate}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                     font-medium bg-blue-600 hover:bg-blue-500 text-white transition-colors
                     focus:outline-none focus:ring-2 focus:ring-blue-500/50"
        >
          <Plus size={13} /> Создать ограничение
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">

        {/* Remnawave not configured warning */}
        {rwError && (
          <div className="mb-4 flex items-center gap-2 px-3 py-2.5 rounded-lg
                          bg-amber-950/40 border border-amber-800/50 text-amber-400 text-sm">
            <AlertCircle size={14} className="shrink-0" />
            {rwError}. Настройте подключение в <strong className="ml-1">Настройки → Remnawave</strong>.
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-gray-600 text-sm py-20">
            <Loader2 size={16} className="animate-spin" /> Загрузка...
          </div>
        ) : rules.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-20 text-gray-600 text-sm">
            <p>Правил пока нет.</p>
            <button onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                         font-medium border border-gray-700 hover:bg-gray-800
                         hover:text-gray-300 transition-colors">
              <Plus size={13} /> Создать первое правило
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-800/60">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800/60 text-[11px] text-gray-600
                               uppercase tracking-widest">
                  <th className="px-4 py-2.5 text-left font-medium">Нода</th>
                  <th className="px-4 py-2.5 text-left font-medium">Область</th>
                  <th className="px-4 py-2.5 text-left font-medium">Лимит</th>
                  <th className="px-4 py-2.5 text-left font-medium">Период</th>
                  <th className="px-4 py-2.5 text-left font-medium">Синхронизация</th>
                  <th className="px-4 py-2.5 text-left font-medium">Действия</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/40">
                {rules.map(rule => (
                  <tr key={rule.id} className="hover:bg-gray-800/20 transition-colors">
                    <td className="px-4 py-3 text-gray-200 font-mono text-xs">
                      {rule.node_name}
                    </td>
                    <td className="px-4 py-3">
                      {rule.scope === "ALL" ? (
                        <span className="text-gray-400">Все пользователи</span>
                      ) : (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-blue-400">Сквад</span>
                          {rule.squad_names.map((n, i) => (
                            <span key={i} className="text-[11px] text-gray-500">{n}</span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-300">{fmtLimit(rule)}</td>
                    <td className="px-4 py-3 text-gray-400">{PERIOD_LABELS[rule.period]}</td>
                    <td className="px-4 py-3">
                      <SyncBadge rule={rule} onSync={() => handleSync(rule)} />
                      {rule.last_synced_at && (
                        <div className="text-[10px] text-gray-600 mt-0.5">
                          {new Date(rule.last_synced_at).toLocaleString("ru-RU")}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={() => openEdit(rule)}
                          className="p-1.5 rounded-md text-gray-600 hover:text-gray-300
                                     hover:bg-gray-700 transition-colors focus:outline-none"
                          title="Редактировать"
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          onClick={() => handleSync(rule)}
                          className="p-1.5 rounded-md text-gray-600 hover:text-blue-400
                                     hover:bg-gray-700 transition-colors focus:outline-none"
                          title="Повторно применить"
                        >
                          <RefreshCw size={13} />
                        </button>
                        <button
                          onClick={() => handleDelete(rule.id)}
                          disabled={deletingId === rule.id}
                          className="p-1.5 rounded-md text-gray-600 hover:text-red-400
                                     hover:bg-gray-700 transition-colors focus:outline-none
                                     disabled:opacity-40"
                          title="Удалить"
                        >
                          {deletingId === rule.id
                            ? <Loader2 size={13} className="animate-spin" />
                            : <Trash2 size={13} />
                          }
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <Modal
          initial={modalInitial}
          nodes={nodes}
          intSquads={intSquads}
          extSquads={extSquads}
          squadsLoading={squadsLoading}
          onClose={() => { setShowModal(false); setEditRule(null); }}
          onSave={handleSave}
        />
      )}
    </div>
  );
}
