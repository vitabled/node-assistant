// ============================================================
// Rules API client + shared types — node-installer «Автоматизация»
// Backs both RuleBuilder («Автоматизация») and Notifications («Уведомления»),
// which are two views over the same /api/rules resource (Ф1 backend). Auth is
// attached globally by the fetch interceptor — no per-call token here.
// ============================================================

export type TriggerType = "xray_down" | "webhook" | "cron";
export type ActionType =
  | "telegram" | "hide_hosts" | "show_hosts"
  | "node_disable" | "node_enable" | "user_disable" | "user_enable";
export type Op = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "contains" | "in" | "exists";
export type Join = "and" | "or";

// Backend masks a vaulted telegram bot-token with this exact string; sending it
// back unchanged keeps the existing token (see api/rules._ingest_actions).
export const TOKEN_MASK = "••••";

export interface Trigger { type: TriggerType; params: Record<string, any> }
export interface Condition { field: string; op: Op; value: any; join: Join }
export interface Action { type: ActionType; params: Record<string, any> }

export interface Rule {
  id: string;
  name: string;
  enabled: boolean;
  trigger: Trigger;
  conditions: Condition[];
  actions: Action[];
  cooldown_sec: number;
  dry_run: boolean;
  last_fired_at?: number | null;
}

export interface RuleInput {
  name: string;
  enabled: boolean;
  trigger: Trigger;
  conditions: Condition[];
  actions: Action[];
  cooldown_sec: number;
  dry_run: boolean;
}

export interface PlanItem {
  type: string;
  executed: boolean;
  dry_run: boolean;
  ok: boolean;
  plan?: Record<string, any>;
  detail?: string;
}

export interface TestResult {
  event: Record<string, any>;
  evaluation: { should_fire: boolean; reason: string; dry_run: boolean };
  plan: PlanItem[];
}

async function jsonOrThrow(res: Response): Promise<any> {
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    throw new Error(formatError(err, res.statusText));
  }
  return res.status === 204 ? null : res.json();
}

// Turn a FastAPI error body into a readable message. CRUCIAL: never surface the
// validation `input` field — it echoes the value the user just typed, which for a
// telegram action is the plaintext bot-token (would defeat the password masking).
function formatError(err: any, fallback: string): string {
  const d = err?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const msgs = d.map((e: any) => {
      const loc = Array.isArray(e?.loc) ? e.loc.filter((x: any) => x !== "body").join(".") : "";
      return loc ? `${loc}: ${e?.msg ?? "ошибка"}` : (e?.msg ?? "ошибка");
    });
    return msgs.join("; ") || fallback;
  }
  return fallback;
}

// Throws on a non-OK response (401/500/network) so the caller can distinguish a
// load error from a genuinely empty account — never silently collapses to [].
export const listRules = async (): Promise<Rule[]> => {
  const data = await fetch("/api/rules").then(jsonOrThrow);
  return Array.isArray(data) ? data : [];
};

export const createRule = (body: RuleInput): Promise<Rule> =>
  fetch("/api/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

export const updateRule = (id: string, body: Partial<RuleInput>): Promise<Rule> =>
  fetch(`/api/rules/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

export const deleteRule = (id: string): Promise<null> =>
  fetch(`/api/rules/${id}`, { method: "DELETE" }).then(jsonOrThrow);

export const testRule = (id: string): Promise<TestResult> =>
  fetch(`/api/rules/${id}/test`, { method: "POST" }).then(jsonOrThrow);

// Dry-run an UNSAVED rule draft — the backend evaluates it without persisting or
// vaulting its token, so previewing a new rule never leaves an orphan behind.
export const testRuleBody = (body: RuleInput): Promise<TestResult> =>
  fetch("/api/rules/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(jsonOrThrow);

// ── UI metadata (labels) ──────────────────────────────────────
export const TRIGGER_LABELS: Record<TriggerType, string> = {
  xray_down: "Нода недоступна (xray)",
  webhook: "Webhook-событие Remnawave",
  cron: "По расписанию (cron)",
};

export const ACTION_LABELS: Record<ActionType, string> = {
  telegram: "Telegram-уведомление",
  hide_hosts: "Скрыть хосты",
  show_hosts: "Показать хосты",
  node_disable: "Отключить ноду",
  node_enable: "Включить ноду",
  user_disable: "Отключить пользователя",
  user_enable: "Включить пользователя",
};

export const OP_LABELS: Record<Op, string> = {
  eq: "равно", ne: "не равно", gt: ">", gte: "≥", lt: "<", lte: "≤",
  contains: "содержит", in: "в списке", exists: "существует",
};

// Placeholders usable in a telegram action `text` (substituted from the event).
export const TEXT_PLACEHOLDERS = [
  "$hostname", "$node", "$event", "$stableId", "$group", "$account_id",
];

// Fields commonly available on events (condition builder hints; free-text allowed).
export const CONDITION_FIELDS = [
  "node", "name", "stableId", "group", "down_seconds",
  "event", "scope", "nodeName", "nodeUuid", "userUuid",
];

// The dry-run reason codes the engine returns, in human-readable form.
export const REASON_LABELS: Record<string, string> = {
  matched: "Условия выполнены — правило сработает",
  disabled: "Правило выключено",
  trigger_mismatch: "Тип события не совпал с триггером",
  hysteresis_not_met: "Порог по времени недоступности не достигнут",
  node_mismatch: "Фильтр по ноде не совпал",
  event_mismatch: "Событие webhook не совпало",
  scope_mismatch: "Scope webhook не совпал",
  cooldown: "Активен антифлап (cooldown)",
  conditions_unmet: "Условия не выполнены",
  unknown_trigger: "Неизвестный триггер",
};
