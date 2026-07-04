// Typed fetch wrapper for /api/infra-billing. Errors throw with the backend
// `detail` (shown as toasts). The account bearer token is attached globally by
// the auth fetch interceptor (src/auth/apiClient.ts) — no per-call auth here.

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const res = await fetch(`/api/infra-billing${path}`, { headers, ...init });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    throw Object.assign(new Error(msg), { status: res.status });
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Types ─────────────────────────────────────────────────────
export interface Provider {
  uuid: string; name: string; faviconLink: string; loginUrl: string; nodeCount: number;
  balance: number; currency: string; lowBalanceThreshold: number;
  status: string; apiTokenId: string; apiTokenName: string;
  countryCode?: string;   // optional geo-location (rendered as a flag when present)
}
export interface Project {
  id: string; name: string; description: string; node_uuids: string[];
  nodeCount: number; monthlyCost: number; created_at: number;
}
export interface Service {
  id: string; name: string; kind: string; node_uuid: string; provider_uuid: string;
  project_id: string; billing_type: string; cost: number; next_billing_at: string; created_at: number;
}
export interface Payment {
  id: string; ts: number; provider_uuid: string; project_id: string;
  type: string; amount: number; currency: string; status: string; note: string;
}
export interface ApiToken { id: string; name: string; providerKind: string; masked: string; createdAt: number }
export interface BillingSettings {
  baseCurrency: string; fxRates: Record<string, number>;
  lowBalanceThreshold: number; refreshInterval: string;
}
export interface DashboardSummary {
  baseCurrency: string; totalBalance: number;
  burnRate: { hourly: number; daily: number; monthly: number; daysLeft: number | null; critical: boolean };
  spendByProvider: { provider: string; total: number }[];
  spendByMonth: { month: string; total: number }[];
  alertsCount: number;
}

// ── Endpoints ─────────────────────────────────────────────────
export const infraApi = {
  dashboard: () => req<DashboardSummary>("/dashboard/summary"),

  listProviders: () => req<Provider[]>("/providers"),
  createProvider: (b: unknown) => req("/providers", { method: "POST", body: JSON.stringify(b) }),
  updateProvider: (uuid: string, b: unknown) => req(`/providers/${uuid}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteProvider: (uuid: string, force = false) => req(`/providers/${uuid}?force=${force}`, { method: "DELETE" }),

  listProjects: () => req<Project[]>("/projects"),
  createProject: (b: unknown) => req("/projects", { method: "POST", body: JSON.stringify(b) }),
  updateProject: (id: string, b: unknown) => req(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteProject: (id: string) => req(`/projects/${id}`, { method: "DELETE" }),

  listServices: () => req<Service[]>("/services"),
  createService: (b: unknown) => req("/services", { method: "POST", body: JSON.stringify(b) }),
  updateService: (id: string, b: unknown) => req(`/services/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteService: (id: string) => req(`/services/${id}`, { method: "DELETE" }),

  listPayments: () => req<Payment[]>("/payments"),
  createPayment: (b: unknown) => req("/payments", { method: "POST", body: JSON.stringify(b) }),
  deletePayment: (id: string) => req(`/payments/${id}`, { method: "DELETE" }),

  getSettings: () => req<BillingSettings>("/settings"),
  putSettings: (b: unknown) => req("/settings", { method: "PUT", body: JSON.stringify(b) }),

  listTokens: () => req<ApiToken[]>("/api-tokens"),
  createToken: (b: unknown) => req("/api-tokens", { method: "POST", body: JSON.stringify(b) }),
  deleteToken: (id: string) => req(`/api-tokens/${id}`, { method: "DELETE" }),
  verifyToken: (id: string) => req<{ ok: boolean; detail: string; verifiedAgainstProvider: boolean }>(`/api-tokens/${id}/verify`, { method: "POST" }),
};
