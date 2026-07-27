// Typed client for /api/cloudflare (Wave-9 Plan B).
// Auth rides on the global fetch interceptor (auth/apiClient.ts) — no headers here.

export interface CfConfig {
  enabled: boolean;
  account_id: string;
  has_token: boolean;
  default_contact: Record<string, unknown>;
}

export interface CfAccount { id: string; name?: string }

export interface CfSummary {
  profile: { balance: number | null; currency: string; payment_method_present: boolean };
  paygo: Record<string, unknown>;
  subscriptions_total_monthly: number;
  next_charge_at: string | null;
  /** Sub-requests the token had no rights for — shown instead of silent zeros. */
  degraded: string[];
}

export interface CfSubscription {
  id?: string;
  price?: number;
  currency?: string;
  frequency?: string;
  state?: string;
  rate_plan?: { id?: string; currency?: string; scope?: string } | null;
  current_period_start?: string;
  current_period_end?: string;
}

export interface CfDomain {
  name?: string;
  expires_at?: string;
  auto_renew?: boolean;
  privacy?: boolean;
  locked?: boolean;
  status?: string;
  [k: string]: unknown;
}

export interface CfCandidate {
  name: string;
  available: boolean;
  price: number | null;
  currency: string;
  period_years: number;
}

export interface CfRegisterResult {
  ok: boolean;
  domain: string;
  price: number;
  currency: string;
  state: string;
  workflow: Record<string, unknown>;
}

/** FastAPI `{detail}` → Error. Never echo the request body: it can carry a token. */
export async function jsonOrThrow<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : `Ошибка ${res.status}`);
  }
  return body as T;
}

export const messageOf = (e: unknown) =>
  e instanceof Error ? e.message : "Неизвестная ошибка";

const get = <T>(path: string) => fetch(`/api/cloudflare${path}`).then(jsonOrThrow<T>);
const send = <T>(path: string, body: unknown, method = "POST") =>
  fetch(`/api/cloudflare${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then(jsonOrThrow<T>);

export const getConfig = () => get<CfConfig>("/config");
export const saveConfig = (body: {
  enabled: boolean; account_id: string; api_token?: string;
  default_contact?: Record<string, unknown>;
}) => send<CfConfig>("/config", body);
export const testConnection = () =>
  send<{ ok: boolean; accounts: CfAccount[]; error: string }>("/test", {});
export const listAccounts = () => get<CfAccount[]>("/accounts");

export const getSummary = (refresh = false) =>
  get<CfSummary>(`/billing/summary${refresh ? "?refresh=1" : ""}`);
export const listSubscriptions = (refresh = false) =>
  get<CfSubscription[]>(`/subscriptions${refresh ? "?refresh=1" : ""}`);
export const getUsage = (from = "", to = "", refresh = false) => {
  const p = new URLSearchParams();
  if (from) p.set("from", from);
  if (to) p.set("to", to);
  if (refresh) p.set("refresh", "1");
  const qs = p.toString();
  return get<unknown>(`/usage${qs ? `?${qs}` : ""}`);
};
export const listZones = (refresh = false) =>
  get<Record<string, unknown>[]>(`/zones${refresh ? "?refresh=1" : ""}`);

export const listDomains = (refresh = false) =>
  get<CfDomain[]>(`/domains${refresh ? "?refresh=1" : ""}`);
export const searchDomains = (q: string) => send<CfCandidate[]>("/domains/search", { q });
export const checkDomains = (names: string[]) => send<CfCandidate[]>("/domains/check", { names });
export const patchDomain = (name: string, patch: { auto_renew?: boolean; privacy_mode?: string }) =>
  send<{ ok: boolean }>(`/domains/${encodeURIComponent(name)}`, patch, "PATCH");

/**
 * Buy a domain. `expected_price`/`expected_currency` MUST come from the check
 * response for this exact name — the backend re-checks and refuses on drift, so
 * passing a stale or hand-made price just gets a 409.
 */
export const registerDomain = (body: {
  domain_name: string; years: number; privacy_mode: string; auto_renew: boolean;
  contacts: Record<string, unknown>; confirm: true;
  expected_price: number; expected_currency: string;
}) => send<CfRegisterResult>("/domains/register", body);

export const fmtMoney = (v: number | null | undefined, cur = "") =>
  v === null || v === undefined ? "—" : `${v.toFixed(2)}${cur ? ` ${cur}` : ""}`;

export const fmtDay = (iso?: string | null) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString("ru-RU");
};
