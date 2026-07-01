// Thin fetch wrapper for the /api/infra-billing endpoints. On error it throws an
// Error whose message is the backend `detail` (surfaced as a toast by callers).

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/infra-billing${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Types ─────────────────────────────────────────────────────
export interface Provider {
  uuid: string; name: string; faviconLink: string; loginUrl: string;
  nodeCount: number; balance: number; currency: string; lowBalanceThreshold: number;
}
export interface BillingNode {
  uuid: string; nodeUuid: string; name: string; providerUuid: string;
  provider?: { name?: string }; nextBillingAt: string; monthlyCost: number;
}
export interface AvailableNode { uuid: string; name: string; countryCode: string }
export interface NodesResp {
  billingNodes: BillingNode[]; availableBillingNodes: AvailableNode[];
  stats: { upcomingNodesCount?: number; currentMonthPayments?: number; totalSpent?: number };
}
export interface HistoryRecord {
  uuid: string; providerUuid: string; amount: number; billedAt: string;
  provider?: { name?: string };
}
export interface Analytics {
  spendByProvider: { provider: string; total: number }[];
  spendByMonth: { month: string; total: number }[];
  burnRate: {
    perProvider: { provider: string; balance: number; currency: string; monthlyCost: number; daysLeft: number | null; critical: boolean }[];
    global: { totalBalance: number; totalMonthlyCost: number; daysLeft: number | null; critical: boolean };
  };
  stats: { upcomingNodesCount?: number; currentMonthPayments?: number; totalSpent?: number };
  alertsCount: number;
}

// ── Endpoints ─────────────────────────────────────────────────
export const infraApi = {
  listProviders: () => req<Provider[]>("/providers"),
  createProvider: (b: unknown) => req("/providers", { method: "POST", body: JSON.stringify(b) }),
  updateProvider: (uuid: string, b: unknown) => req(`/providers/${uuid}`, { method: "PATCH", body: JSON.stringify(b) }),
  deleteProvider: (uuid: string, force = false) => req(`/providers/${uuid}?force=${force}`, { method: "DELETE" }),

  listNodes: () => req<NodesResp>("/nodes"),
  createNode: (b: unknown) => req("/nodes", { method: "POST", body: JSON.stringify(b) }),
  updateNodes: (b: unknown) => req("/nodes", { method: "PATCH", body: JSON.stringify(b) }),
  deleteNode: (uuid: string) => req(`/nodes/${uuid}`, { method: "DELETE" }),

  listHistory: () => req<HistoryRecord[]>("/history"),
  createHistory: (b: unknown) => req("/history", { method: "POST", body: JSON.stringify(b) }),
  deleteHistory: (uuid: string) => req(`/history/${uuid}`, { method: "DELETE" }),

  analytics: () => req<Analytics>("/analytics"),
};
