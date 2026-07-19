import { useEffect, useState } from "react";
import { Plus, Trash2, Loader2, Send, Pencil, Bell } from "lucide-react";
import {
  type Rule, type RuleInput, type TriggerType,
  TRIGGER_LABELS, TEXT_PLACEHOLDERS,
  listRules, createRule, updateRule, deleteRule,
} from "./rulesApi";
import { RuleModal, Switch } from "./RuleBuilder";
import { toast } from "../infra/Toast";

// A "notification" is just a rule whose actions include a telegram send.
const isNotification = (r: Rule) => r.actions.some(a => a.type === "telegram");

interface QuickForm {
  name: string;
  triggerType: TriggerType;
  minutes: string;
  event: string;
  bot_token: string;
  chat_id: string;
  text: string;
}

const EMPTY_QUICK: QuickForm = {
  name: "", triggerType: "xray_down", minutes: "5", event: "node.connection_lost",
  bot_token: "", chat_id: "", text: "Нода $node недоступна",
};

export function Notifications() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [form, setForm] = useState<QuickForm>(EMPTY_QUICK);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);

  const load = async () => {
    try {
      const all = await listRules();
      setRules(all.filter(isNotification));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Не удалось загрузить уведомления");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const set = <K extends keyof QuickForm>(k: K, v: QuickForm[K]) => setForm(f => ({ ...f, [k]: v }));

  const buildInput = (): RuleInput => ({
    name: form.name.trim(),
    enabled: true,
    trigger: form.triggerType === "webhook"
      ? { type: "webhook", params: { event: form.event.trim() } }
      : form.triggerType === "cron"
        ? { type: "cron", params: { minutes: Number(form.minutes) || 15 } }
        : { type: "xray_down", params: { minutes: Number(form.minutes) || 5 } },
    conditions: [],
    actions: [{ type: "telegram", params: { bot_token: form.bot_token, chat_id: form.chat_id.trim(), text: form.text } }],
    cooldown_sec: 300,
    dry_run: false,
  });

  const create = async () => {
    if (!form.name.trim()) { setError("Укажите название"); return; }
    if (form.triggerType !== "webhook" && Number(form.minutes) <= 0) {
      setError("Минуты должны быть > 0"); return;
    }
    if (!form.chat_id.trim()) { setError("Укажите chat_id"); return; }
    if (!form.bot_token.trim()) { setError("Укажите токен бота"); return; }
    if (form.triggerType === "webhook" && !form.event.trim()) { setError("Укажите webhook-событие"); return; }
    setError(null);
    setSaving(true);
    try {
      await createRule(buildInput());
      setForm(EMPTY_QUICK);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания");
    } finally {
      setSaving(false);
    }
  };

  const onToggle = async (r: Rule) => {
    setBusyId(r.id);
    try {
      const next = await updateRule(r.id, { enabled: !r.enabled });
      setRules(rs => rs.map(x => (x.id === r.id ? next : x)));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Не удалось изменить уведомление", "error");
    } finally { setBusyId(null); }
  };

  const onDelete = async (id: string) => {
    setBusyId(id);
    try { await deleteRule(id); await load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Не удалось удалить уведомление", "error"); }
    finally { setBusyId(null); }
  };

  const onSaveEdit = async (input: RuleInput, id?: string) => {
    if (id) await updateRule(id, input); else await createRule(input);
    setEditing(null);
    await load();
  };

  return (
    <div className="flex flex-col h-full min-h-0 ni-pagebody">
      <div className="shrink-0 h-11 border-b border-[var(--line-soft)] px-4 flex items-center gap-3 ni-pagehead">
        <Bell size={15} className="text-[var(--t-mid)]" />
        <span className="text-sm font-medium text-[var(--t-hi)]">Уведомления</span>
        <span className="text-[11px] text-[var(--t-faint)]">Telegram-оповещения о событиях</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-5 max-w-2xl">
        {/* quick create */}
        <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-4 flex flex-col gap-3">
          <span className="text-xs font-semibold text-[var(--t-hi)]">Быстрое уведомление</span>

          <Row label="Название">
            <input className="input" value={form.name} disabled={saving}
              onChange={e => set("name", e.target.value)} placeholder="Падение ноды → Telegram" />
          </Row>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Row label="Когда">
              <select className="selectbox" value={form.triggerType} disabled={saving}
                onChange={e => set("triggerType", e.target.value as TriggerType)}>
                {(Object.keys(TRIGGER_LABELS) as TriggerType[]).map(t => (
                  <option key={t} value={t}>{TRIGGER_LABELS[t]}</option>
                ))}
              </select>
            </Row>
            {form.triggerType === "webhook" ? (
              <Row label="Событие">
                <input className="input" value={form.event} disabled={saving}
                  onChange={e => set("event", e.target.value)} placeholder="node.connection_lost" />
              </Row>
            ) : (
              <Row label={form.triggerType === "cron" ? "Интервал (мин)" : "Недоступна ≥ (мин)"}>
                <input type="number" min={1} className="input" value={form.minutes} disabled={saving}
                  onChange={e => set("minutes", e.target.value)} />
              </Row>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Row label="Токен бота">
              <input className="input" type="password" autoComplete="off" value={form.bot_token} disabled={saving}
                onChange={e => set("bot_token", e.target.value)} placeholder="123456:ABC..." />
            </Row>
            <Row label="Chat ID">
              <input className="input" value={form.chat_id} disabled={saving}
                onChange={e => set("chat_id", e.target.value)} placeholder="-1001234567890" />
            </Row>
          </div>

          <Row label="Текст сообщения">
            <textarea className="input min-h-[54px]" value={form.text} disabled={saving}
              onChange={e => set("text", e.target.value)} />
            <div className="flex flex-wrap gap-1 mt-1">
              {TEXT_PLACEHOLDERS.map(ph => (
                <button key={ph} type="button" disabled={saving}
                  onClick={() => set("text", `${form.text}${ph}`)}
                  className="text-[10px] px-1.5 py-0.5 rounded border border-[var(--line)] text-[var(--t-low)] hover:bg-[var(--bg3)]">
                  {ph}
                </button>
              ))}
            </div>
          </Row>

          {error && (
            <div className="px-3 py-2 rounded-md bg-[var(--err-dim)] border border-[var(--err-line)] text-xs text-[var(--err)]">
              {error}
            </div>
          )}

          <button onClick={create} disabled={saving}
            className="self-start flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm
                       bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-all disabled:opacity-50">
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Создать уведомление
          </button>
          <p className="text-[11px] text-[var(--t-faint)]">Токен сохраняется в зашифрованном виде и не возвращается в интерфейс.</p>
        </div>

        {/* list */}
        <div className="flex flex-col gap-2">
          <span className="text-xs font-semibold text-[var(--t-hi)]">Настроенные уведомления</span>
          {loadError ? (
            <div className="px-3 py-2.5 rounded-lg bg-[var(--err-dim)] border border-[var(--err-line)] text-[var(--err)] text-sm">
              Не удалось загрузить уведомления: {loadError}
            </div>
          ) : loading ? (
            <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-6">
              <Loader2 size={14} className="animate-spin" /> Загрузка...
            </div>
          ) : rules.length === 0 ? (
            <p className="text-[13px] text-[var(--t-faint)] py-4">Уведомлений пока нет.</p>
          ) : (
            rules.map(r => (
              <div key={r.id} className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)]">
                <Switch checked={r.enabled} disabled={busyId === r.id} onChange={() => onToggle(r)} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-[var(--t-hi)] truncate">{r.name}</p>
                  <p className="text-[11px] text-[var(--t-faint)] truncate">
                    {r.trigger.type === "webhook"
                      ? `webhook: ${r.trigger.params.event || "любое"}`
                      : `${TRIGGER_LABELS[r.trigger.type]} · ${r.trigger.params.minutes ?? "?"} мин`}
                  </p>
                </div>
                <button onClick={() => setEditing(r)} title="Изменить"
                  className="p-1.5 rounded-md text-[var(--t-faint)] hover:text-[var(--t-mid)] hover:bg-[var(--bg3)]">
                  <Pencil size={13} />
                </button>
                <button onClick={() => onDelete(r.id)} disabled={busyId === r.id} title="Удалить"
                  className="p-1.5 rounded-md text-[var(--t-faint)] hover:text-[var(--err)] hover:bg-[var(--bg3)] disabled:opacity-40">
                  {busyId === r.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {editing && <RuleModal initial={editing} onClose={() => setEditing(null)} onSave={onSaveEdit} />}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-[var(--t-low)] uppercase tracking-widest">{label}</span>
      {children}
    </label>
  );
}
