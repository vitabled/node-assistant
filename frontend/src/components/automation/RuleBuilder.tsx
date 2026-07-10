import { useEffect, useState } from "react";
import {
  Plus, Pencil, Trash2, X, Loader2, FlaskConical, Zap,
  CheckCircle2, XCircle, ChevronRight,
} from "lucide-react";
import {
  type Rule, type RuleInput, type Trigger, type Condition, type Action,
  type TriggerType, type ActionType, type Op, type Join, type TestResult,
  TOKEN_MASK, TRIGGER_LABELS, ACTION_LABELS, OP_LABELS, REASON_LABELS,
  TEXT_PLACEHOLDERS, CONDITION_FIELDS,
  listRules, createRule, updateRule, deleteRule, testRuleBody,
} from "./rulesApi";
import { toast } from "../infra/Toast";

// ── defaults ──────────────────────────────────────────────────
const emptyRule = (): RuleInput => ({
  name: "",
  enabled: false,
  trigger: { type: "xray_down", params: { minutes: 5 } },
  conditions: [],
  actions: [],
  cooldown_sec: 300,
  dry_run: false,
});

const newAction = (type: ActionType): Action => {
  switch (type) {
    case "telegram": return { type, params: { chat_id: "", text: "", bot_token: "" } };
    case "hide_hosts":
    case "show_hosts": return { type, params: { node_uuid: "" } };
    case "node_disable":
    case "node_enable": return { type, params: { node_uuid: "" } };
    default: return { type, params: { user_uuid: "" } };
  }
};

// ── summaries for the list ────────────────────────────────────
function triggerSummary(t: Trigger): string {
  if (t.type === "xray_down") {
    const node = t.params.node || t.params.stableId;
    return `Нода недоступна ≥ ${t.params.minutes ?? "?"} мин${node ? ` (${node})` : ""}`;
  }
  if (t.type === "webhook") return `Webhook: ${t.params.event || "любое событие"}`;
  const min = t.params.minutes || (t.params.interval_sec ? Math.round(t.params.interval_sec / 60) : "?");
  return `Каждые ${min} мин`;
}

// ── main ──────────────────────────────────────────────────────
export function RuleBuilder() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = async () => {
    try {
      setRules(await listRules());
      setLoadError(null);
    } catch (e) {
      // A load failure must not masquerade as an empty account.
      setLoadError(e instanceof Error ? e.message : "Не удалось загрузить правила");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const openCreate = () => { setEditing(null); setModalOpen(true); };
  const openEdit = (r: Rule) => { setEditing(r); setModalOpen(true); };

  const onSave = async (input: RuleInput, id?: string) => {
    if (id) await updateRule(id, input);
    else await createRule(input);
    setModalOpen(false);
    setEditing(null);
    await load();
  };

  const onDelete = async (id: string) => {
    setBusyId(id);
    try { await deleteRule(id); await load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Не удалось удалить правило", "error"); }
    finally { setBusyId(null); }
  };

  const onToggle = async (r: Rule) => {
    setBusyId(r.id);
    try {
      const next = await updateRule(r.id, { enabled: !r.enabled });
      setRules(rs => rs.map(x => (x.id === r.id ? next : x)));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось изменить правило", "error");
    } finally { setBusyId(null); }
  };

  return (
    <div className="flex flex-col h-full min-h-0 ni-pagebody">
      <div className="shrink-0 h-11 border-b border-[var(--line-soft)] px-4 flex items-center gap-3 ni-pagehead">
        <span className="text-sm font-medium text-[var(--t-hi)]">Автоматизация</span>
        <span className="text-[11px] text-[var(--t-faint)]">если событие → выполнить действия</span>
        <button
          onClick={openCreate}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                     bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)] ni-pagehead-actions"
        >
          <Plus size={13} /> Создать правило
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loadError && (
          <div className="mb-4 px-3 py-2.5 rounded-lg bg-[var(--err-dim)] border border-[var(--err-line)] text-[var(--err)] text-sm">
            Не удалось загрузить правила: {loadError}
          </div>
        )}
        {loading ? (
          <div className="flex items-center justify-center gap-2 text-[var(--t-faint)] text-sm py-20">
            <Loader2 size={16} className="animate-spin" /> Загрузка...
          </div>
        ) : loadError ? null : rules.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-20 text-[var(--t-faint)] text-sm">
            <Zap size={22} className="opacity-40" />
            <p>Правил автоматизации пока нет.</p>
            <button onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                         border border-[var(--line)] hover:bg-[var(--bg3)] hover:text-[var(--t-mid)] transition-colors">
              <Plus size={13} /> Создать первое правило
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-[var(--line-soft)]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--line-soft)] text-[11px] text-[var(--t-faint)] uppercase tracking-widest">
                  <th className="px-4 py-2.5 text-left font-medium">Название</th>
                  <th className="px-4 py-2.5 text-left font-medium">Триггер</th>
                  <th className="px-4 py-2.5 text-left font-medium">Действия</th>
                  <th className="px-4 py-2.5 text-left font-medium">Статус</th>
                  <th className="px-4 py-2.5 text-right font-medium">Действия</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--line-soft)]">
                {rules.map(rule => (
                  <tr key={rule.id} className="hover:bg-[var(--row-hover)] transition-colors">
                    <td className="px-4 py-3 text-[var(--t-hi)] font-medium">
                      {rule.name}
                      {rule.dry_run && (
                        <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-[var(--warn-dim)] text-[var(--warn)] border border-[var(--warn-line)]">
                          тест-режим
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-[var(--t-mid)] text-xs">{triggerSummary(rule.trigger)}</td>
                    <td className="px-4 py-3 text-[var(--t-mid)] text-xs">
                      {rule.actions.length === 0
                        ? <span className="text-[var(--err)]">нет действий</span>
                        : rule.actions.map(a => ACTION_LABELS[a.type] || a.type).join(", ")}
                    </td>
                    <td className="px-4 py-3">
                      <Switch checked={rule.enabled} disabled={busyId === rule.id}
                        onChange={() => onToggle(rule)} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1.5">
                        <button onClick={() => openEdit(rule)} title="Редактировать"
                          className="p-1.5 rounded-md text-[var(--t-faint)] hover:text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors">
                          <Pencil size={13} />
                        </button>
                        <button onClick={() => onDelete(rule.id)} disabled={busyId === rule.id} title="Удалить"
                          className="p-1.5 rounded-md text-[var(--t-faint)] hover:text-[var(--err)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-40">
                          {busyId === rule.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
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

      {modalOpen && (
        <RuleModal
          initial={editing}
          onClose={() => { setModalOpen(false); setEditing(null); }}
          onSave={onSave}
        />
      )}
    </div>
  );
}

// ── editor modal ──────────────────────────────────────────────
interface ModalProps {
  initial: Rule | null;
  onClose: () => void;
  onSave: (input: RuleInput, id?: string) => Promise<void>;
}

export function RuleModal({ initial, onClose, onSave }: ModalProps) {
  const [form, setForm] = useState<RuleInput>(() =>
    initial
      ? {
          name: initial.name,
          enabled: initial.enabled,
          trigger: JSON.parse(JSON.stringify(initial.trigger)),
          conditions: JSON.parse(JSON.stringify(initial.conditions || [])),
          actions: JSON.parse(JSON.stringify(initial.actions || [])),
          cooldown_sec: initial.cooldown_sec ?? 300,
          dry_run: initial.dry_run ?? false,
        }
      : emptyRule()
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const set = <K extends keyof RuleInput>(k: K, v: RuleInput[K]) => setForm(f => ({ ...f, [k]: v }));
  const setTrigParam = (k: string, v: any) =>
    setForm(f => ({ ...f, trigger: { ...f.trigger, params: { ...f.trigger.params, [k]: v } } }));
  const setActionParam = (i: number, k: string, v: any) =>
    setForm(f => ({
      ...f,
      actions: f.actions.map((a, idx) => (idx === i ? { ...a, params: { ...a.params, [k]: v } } : a)),
    }));

  // ── client validation (mirrors the backend gates) ──
  const validate = (): string | null => {
    if (!form.name.trim()) return "Укажите название правила";
    const t = form.trigger;
    if (t.type === "xray_down" && (!t.params.minutes || Number(t.params.minutes) <= 0))
      return "Триггер xray_down: минуты должны быть > 0";
    if (t.type === "cron" && (!t.params.minutes || Number(t.params.minutes) <= 0))
      return "Триггер cron: интервал (минуты) должен быть > 0";
    for (const c of form.conditions) {
      if (!String(c.field || "").trim())
        return "Условие без поля — укажите поле или удалите условие";
    }
    if (form.actions.length === 0) return "Добавьте хотя бы одно действие";
    for (const a of form.actions) {
      if (a.type === "telegram") {
        if (!String(a.params.chat_id || "").trim()) return "Telegram: укажите chat_id";
        const tok = String(a.params.bot_token || "");
        // On a NEW rule a token is required; editing keeps the vaulted token (token_ref).
        if (!a.params.token_ref && (!tok || tok === TOKEN_MASK))
          return "Telegram: укажите токен бота";
      } else if (a.type === "hide_hosts" || a.type === "show_hosts") {
        const has = a.params.node_uuid || a.params.config_profile_uuid ||
          (String(a.params.host_uuids || "").trim());
        if (!has) return `${ACTION_LABELS[a.type]}: задайте селектор (нода / профиль / uuid хостов)`;
      } else if (a.type.startsWith("node_")) {
        if (!String(a.params.node_uuid || "").trim()) return `${ACTION_LABELS[a.type]}: укажите node_uuid`;
      } else if (a.type.startsWith("user_")) {
        if (!String(a.params.user_uuid || "").trim()) return `${ACTION_LABELS[a.type]}: укажите user_uuid`;
      }
    }
    return null;
  };

  // Normalize params before sending:
  //  - host_uuids / `in`-condition values: comma-string → array (backend `in`
  //    does list membership; a bare string would degrade to a substring test);
  //  - strip UI-only underscore keys (`_touched`) so they never reach rules.json;
  //  - drop an EMPTY telegram bot_token when a token_ref exists, so an accidental
  //    clear doesn't wipe the vaulted token (only MASK/omission means "keep").
  const toPayload = (): RuleInput => ({
    ...form,
    cooldown_sec: Number(form.cooldown_sec) || 0,
    trigger: { ...form.trigger, params: stripUi(normNums(form.trigger.params)) },
    conditions: form.conditions.map(c =>
      c.op === "in" && typeof c.value === "string"
        ? { ...c, value: c.value.split(/\s*,\s*/).filter(Boolean) }
        : c
    ),
    actions: form.actions.map(a => {
      const p = stripUi(a.params);
      if ((a.type === "hide_hosts" || a.type === "show_hosts") && typeof p.host_uuids === "string") {
        const arr = p.host_uuids.split(/[\s,]+/).filter(Boolean);
        if (arr.length) p.host_uuids = arr; else delete p.host_uuids;
      }
      if (a.type === "telegram" && p.token_ref && (!p.bot_token || p.bot_token === TOKEN_MASK)) {
        delete p.bot_token; // keep the existing vaulted token
      }
      return { ...a, params: p };
    }),
  });

  const handleSave = async () => {
    const err = validate();
    if (err) { setError(err); return; }
    setError(null);
    setSaving(true);
    try {
      await onSave(toPayload(), initial?.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  };

  // Dry-run the DRAFT — the stateless endpoint evaluates the current form without
  // persisting or vaulting anything, so previewing never leaves an orphan rule.
  const handleTest = async () => {
    const err = validate();
    if (err) { setError(err); return; }
    setError(null);
    setTesting(true);
    try {
      setTest(await testRuleBody(toPayload()));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка проверки");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-3">
      <div className="w-full max-w-2xl bg-[var(--bg1)] border border-[var(--line)] rounded-xl shadow-2xl flex flex-col max-h-[92vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--line-soft)]">
          <h2 className="text-sm font-semibold text-[var(--t-hi)]">
            {initial ? "Редактировать правило" : "Новое правило"}
          </h2>
          <button onClick={onClose} className="text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="overflow-y-auto flex flex-col gap-5 p-5">
          {/* name */}
          <Field label="Название">
            <input className="input" value={form.name} disabled={saving}
              onChange={e => set("name", e.target.value)} placeholder="Например: Уведомить при падении ноды" />
          </Field>

          {/* trigger */}
          <section className="flex flex-col gap-3 p-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)]">
            <SectionTitle>Триггер — когда сработать</SectionTitle>
            <Field label="Тип события">
              <select className="selectbox" value={form.trigger.type} disabled={saving}
                onChange={e => set("trigger", { type: e.target.value as TriggerType, params: defaultTriggerParams(e.target.value as TriggerType) })}>
                {(Object.keys(TRIGGER_LABELS) as TriggerType[]).map(t => (
                  <option key={t} value={t}>{TRIGGER_LABELS[t]}</option>
                ))}
              </select>
            </Field>
            <TriggerParams type={form.trigger.type} params={form.trigger.params} onSet={setTrigParam} disabled={saving} />
          </section>

          {/* conditions */}
          <section className="flex flex-col gap-3 p-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)]">
            <div className="flex items-center justify-between">
              <SectionTitle>Условия (необязательно)</SectionTitle>
              <button onClick={() => set("conditions", [...form.conditions, { field: "", op: "eq", value: "", join: "and" }])}
                className="text-xs flex items-center gap-1 text-[var(--accent-hi)] hover:underline">
                <Plus size={12} /> условие
              </button>
            </div>
            {form.conditions.length === 0 && (
              <p className="text-[11px] text-[var(--t-faint)]">Без условий правило сработает на каждом событии триггера.</p>
            )}
            {form.conditions.map((c, i) => (
              <ConditionRow key={i} c={c} last={i === form.conditions.length - 1} disabled={saving}
                onChange={nc => set("conditions", form.conditions.map((x, idx) => (idx === i ? nc : x)))}
                onRemove={() => set("conditions", form.conditions.filter((_, idx) => idx !== i))} />
            ))}
          </section>

          {/* actions */}
          <section className="flex flex-col gap-3 p-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)]">
            <div className="flex items-center justify-between">
              <SectionTitle>Действия — что выполнить</SectionTitle>
              <select className="selectbox !w-auto text-xs" value="" disabled={saving}
                aria-label="Добавить действие"
                onChange={e => { if (e.target.value) set("actions", [...form.actions, newAction(e.target.value as ActionType)]); }}>
                <option value="">+ добавить действие</option>
                {(Object.keys(ACTION_LABELS) as ActionType[]).map(a => (
                  <option key={a} value={a}>{ACTION_LABELS[a]}</option>
                ))}
              </select>
            </div>
            {form.actions.length === 0 && (
              <p className="text-[11px] text-[var(--err)]">Добавьте хотя бы одно действие.</p>
            )}
            {form.actions.map((a, i) => (
              <ActionCard key={i} action={a} disabled={saving}
                onSetParam={(k, v) => setActionParam(i, k, v)}
                onRemove={() => set("actions", form.actions.filter((_, idx) => idx !== i))} />
            ))}
          </section>

          {/* options */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Антифлап cooldown (сек)">
              <input type="number" min={0} className="input" value={form.cooldown_sec} disabled={saving}
                onChange={e => set("cooldown_sec", Number(e.target.value))} />
            </Field>
            <div className="flex flex-col gap-2 justify-end">
              <Toggle label="Включено" checked={form.enabled} disabled={saving} onChange={v => set("enabled", v)} />
              <Toggle label="Тест-режим (dry-run — не выполнять действия)" checked={form.dry_run} disabled={saving} onChange={v => set("dry_run", v)} />
            </div>
          </div>

          {/* test result */}
          {test && <TestPanel test={test} />}

          {error && (
            <div className="px-3 py-2 rounded-md bg-[var(--err-dim)] border border-[var(--err-line)] text-xs text-[var(--err)]">
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-2 px-5 py-4 border-t border-[var(--line-soft)]">
          <button onClick={handleTest} disabled={saving || testing}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium border border-[var(--line)]
                       text-[var(--t-mid)] hover:bg-[var(--bg3)] hover:text-[var(--t-hi)] transition-colors disabled:opacity-50">
            {testing ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />} Проверить
          </button>
          <button onClick={handleSave} disabled={saving || testing}
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-lg font-semibold text-sm
                       bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-all disabled:opacity-50">
            {saving ? <><Loader2 size={14} className="animate-spin" /> Сохранение...</> : "Сохранить"}
          </button>
          <button onClick={onClose} disabled={saving}
            className="px-4 py-2.5 rounded-lg text-sm font-medium text-[var(--t-mid)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors">
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

// ── trigger params ────────────────────────────────────────────
function defaultTriggerParams(type: TriggerType): Record<string, any> {
  if (type === "xray_down") return { minutes: 5 };
  if (type === "webhook") return { event: "" };
  return { minutes: 15 };
}

function TriggerParams({ type, params, onSet, disabled }: {
  type: TriggerType; params: Record<string, any>; onSet: (k: string, v: any) => void; disabled: boolean;
}) {
  if (type === "xray_down") {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Недоступна ≥ (минут)">
          <input type="number" min={1} className="input" value={params.minutes ?? ""} disabled={disabled}
            onChange={e => onSet("minutes", Number(e.target.value))} />
        </Field>
        <Field label="Фильтр по ноде (имя/stableId, необязательно)">
          <input className="input" value={params.node ?? ""} disabled={disabled}
            onChange={e => onSet("node", e.target.value)} placeholder="все ноды" />
        </Field>
      </div>
    );
  }
  if (type === "webhook") {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field label="Событие (event)">
          <input className="input" value={params.event ?? ""} disabled={disabled}
            onChange={e => onSet("event", e.target.value)} placeholder="node.connection_lost" />
        </Field>
        <Field label="Scope (необязательно)">
          <input className="input" value={params.scope ?? ""} disabled={disabled}
            onChange={e => onSet("scope", e.target.value)} placeholder="node" />
        </Field>
      </div>
    );
  }
  return (
    <Field label="Интервал (минут)">
      <input type="number" min={1} className="input" value={params.minutes ?? ""} disabled={disabled}
        onChange={e => onSet("minutes", Number(e.target.value))} />
    </Field>
  );
}

// ── condition row ─────────────────────────────────────────────
function ConditionRow({ c, last, disabled, onChange, onRemove }: {
  c: Condition; last: boolean; disabled: boolean;
  onChange: (c: Condition) => void; onRemove: () => void;
}) {
  const noValue = c.op === "exists";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <input list="cond-fields" className="input !w-36" value={c.field} disabled={disabled}
          onChange={e => onChange({ ...c, field: e.target.value })} placeholder="поле" />
        <datalist id="cond-fields">{CONDITION_FIELDS.map(f => <option key={f} value={f} />)}</datalist>
        <select className="selectbox !w-32" value={c.op} disabled={disabled}
          onChange={e => onChange({ ...c, op: e.target.value as Op })}>
          {(Object.keys(OP_LABELS) as Op[]).map(op => <option key={op} value={op}>{OP_LABELS[op]}</option>)}
        </select>
        {!noValue && (
          <input className="input flex-1 !min-w-[100px]" value={c.value ?? ""} disabled={disabled}
            onChange={e => onChange({ ...c, value: e.target.value })}
            placeholder={c.op === "in" ? "a, b, c" : "значение"} />
        )}
        <button onClick={onRemove} disabled={disabled} className="p-1.5 rounded-md text-[var(--t-faint)] hover:text-[var(--err)] hover:bg-[var(--bg3)]">
          <Trash2 size={13} />
        </button>
      </div>
      {!last && (
        <div className="flex gap-2 pl-1">
          {(["and", "or"] as Join[]).map(j => (
            <label key={j} className="flex items-center gap-1 text-[11px] text-[var(--t-mid)] cursor-pointer">
              <input type="radio" checked={c.join === j} disabled={disabled}
                onChange={() => onChange({ ...c, join: j })} className="accent-[var(--accent)]" />
              {j === "and" ? "И" : "ИЛИ"}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ── action card ───────────────────────────────────────────────
function ActionCard({ action, disabled, onSetParam, onRemove }: {
  action: Action; disabled: boolean; onSetParam: (k: string, v: any) => void; onRemove: () => void;
}) {
  const p = action.params;
  return (
    <div className="flex flex-col gap-2 p-3 rounded-md border border-[var(--line-soft)] bg-[var(--bg1)]">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-[var(--t-hi)]">{ACTION_LABELS[action.type]}</span>
        <button onClick={onRemove} disabled={disabled} className="p-1 rounded text-[var(--t-faint)] hover:text-[var(--err)]">
          <Trash2 size={12} />
        </button>
      </div>

      {action.type === "telegram" && (
        <>
          <Field label="Токен бота">
            <input className="input" type="password" autoComplete="off"
              value={p.token_ref && !p._touched ? TOKEN_MASK : (p.bot_token ?? "")} disabled={disabled}
              onChange={e => { onSetParam("bot_token", e.target.value); onSetParam("_touched", true); }}
              placeholder={p.token_ref ? "•••• (оставьте, чтобы не менять)" : "123456:ABC..."} />
          </Field>
          <Field label="Chat ID">
            <input className="input" value={p.chat_id ?? ""} disabled={disabled}
              onChange={e => onSetParam("chat_id", e.target.value)} placeholder="-1001234567890" />
          </Field>
          <Field label="Текст">
            <textarea className="input min-h-[60px]" value={p.text ?? ""} disabled={disabled}
              onChange={e => onSetParam("text", e.target.value)} placeholder="Нода $node недоступна" />
            <div className="flex flex-wrap gap-1 mt-1">
              {TEXT_PLACEHOLDERS.map(ph => (
                <button key={ph} type="button" disabled={disabled}
                  onClick={() => onSetParam("text", `${p.text ?? ""}${ph}`)}
                  className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--line)] text-[var(--t-low)] hover:bg-[var(--bg3)]">
                  {ph}
                </button>
              ))}
            </div>
          </Field>
        </>
      )}

      {(action.type === "hide_hosts" || action.type === "show_hosts") && (
        <>
          <p className="text-[11px] text-[var(--t-faint)]">Задайте хотя бы один селектор (иначе операция отклоняется).</p>
          <Field label="node_uuid (хосты этой ноды)">
            <input className="input" value={p.node_uuid ?? ""} disabled={disabled}
              onChange={e => onSetParam("node_uuid", e.target.value)} placeholder="uuid ноды" />
          </Field>
          <Field label="config_profile_uuid">
            <input className="input" value={p.config_profile_uuid ?? ""} disabled={disabled}
              onChange={e => onSetParam("config_profile_uuid", e.target.value)} placeholder="uuid профиля" />
          </Field>
          <Field label="host_uuids (через запятую)">
            <input className="input" value={typeof p.host_uuids === "string" ? p.host_uuids : (p.host_uuids || []).join(", ")}
              disabled={disabled} onChange={e => onSetParam("host_uuids", e.target.value)} placeholder="uuid1, uuid2" />
          </Field>
        </>
      )}

      {(action.type === "node_disable" || action.type === "node_enable") && (
        <Field label="node_uuid">
          <input className="input" value={p.node_uuid ?? ""} disabled={disabled}
            onChange={e => onSetParam("node_uuid", e.target.value)} placeholder="uuid ноды (или из события)" />
        </Field>
      )}

      {(action.type === "user_disable" || action.type === "user_enable") && (
        <Field label="user_uuid">
          <input className="input" value={p.user_uuid ?? ""} disabled={disabled}
            onChange={e => onSetParam("user_uuid", e.target.value)} placeholder="uuid пользователя (или из события)" />
        </Field>
      )}
    </div>
  );
}

// ── dry-run result ────────────────────────────────────────────
function TestPanel({ test }: { test: TestResult }) {
  const fire = test.evaluation.should_fire;
  return (
    <div className="rounded-lg border p-3 flex flex-col gap-2"
      style={{ borderColor: fire ? "var(--ok-line)" : "var(--warn-line)", background: fire ? "var(--ok-dim)" : "var(--warn-dim)" }}>
      <div className="flex items-center gap-2 text-sm" style={{ color: fire ? "var(--ok)" : "var(--warn)" }}>
        {fire ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
        {REASON_LABELS[test.evaluation.reason] || test.evaluation.reason}
      </div>
      {test.plan.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-[11px] text-[var(--t-faint)] uppercase tracking-widest">План действий (без выполнения)</p>
          {test.plan.map((pl, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs text-[var(--t-mid)]">
              <ChevronRight size={12} className="mt-0.5 flex-none" />
              <span>
                <b>{ACTION_LABELS[pl.type as ActionType] || pl.type}</b>
                {planDetail(pl)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// A short, human summary of a planned action's target (all action types, not
// just telegram) from the backend's masked plan.
function planDetail(pl: { type: string; plan?: Record<string, any>; detail?: string }): string {
  const p = pl.plan || {};
  if (pl.type === "telegram") {
    return `${p.text ? ` — «${p.text}»` : ""}${p.chat_id ? ` → ${p.chat_id}` : ""}`;
  }
  if (pl.type === "hide_hosts" || pl.type === "show_hosts") {
    const sel = p.node_uuid ? `нода ${p.node_uuid}`
      : p.config_profile_uuid ? `профиль ${p.config_profile_uuid}`
      : Array.isArray(p.host_uuids) ? `${p.host_uuids.length} хост(ов)`
      : "селектор не задан";
    return ` — ${sel}`;
  }
  if (p.node_uuid) return ` — нода ${p.node_uuid}`;
  if (p.user_uuid) return ` — пользователь ${p.user_uuid}`;
  return pl.detail ? ` — ${pl.detail}` : "";
}

// ── tiny shared UI ────────────────────────────────────────────
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-[var(--t-low)] uppercase tracking-widest">{label}</span>
      {children}
    </label>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <span className="text-xs font-semibold text-[var(--t-hi)]">{children}</span>;
}

export function Switch({ checked, disabled, onChange }: {
  checked: boolean; disabled?: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative w-9 h-5 rounded-full transition-colors flex-none focus:outline-none
                  focus:ring-2 focus:ring-[var(--accent-line)] disabled:opacity-40
                  ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
      <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                        transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0"}`} />
    </button>
  );
}

export function Toggle({ label, checked, disabled, onChange }: {
  label: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
      <Switch checked={checked} disabled={disabled} onChange={onChange} />
      {label}
    </label>
  );
}

// Coerce numeric-looking trigger params to numbers (minutes/interval_sec).
function normNums(params: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = { ...params };
  for (const k of ["minutes", "interval_sec"]) {
    if (out[k] !== undefined && out[k] !== "") out[k] = Number(out[k]);
  }
  return out;
}

// Drop UI-only keys (underscore-prefixed, e.g. `_touched`) before sending.
function stripUi(params: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(params)) if (!k.startsWith("_")) out[k] = v;
  return out;
}
