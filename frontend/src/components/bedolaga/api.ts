// Minimal fetch wrapper for the /api/bedolaga endpoints. Auth header is
// attached globally by auth/apiClient.ts, same as infra billing's api.ts.

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/bedolaga${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || j.error || msg; } catch { /* noop */ }
    throw new Error(msg);
  }
  return res.json();
}

export interface BedolagaConfig {
  base_url: string;
  has_token: boolean;
  token_hint: string;
  auth_header: string;
  ai_enabled: boolean;
  shadow_mode: boolean;
  ai_provider_base_url: string;
  has_ai_provider_key: boolean;
  ai_model: string;
  telegram_topic_chat_id: string;
  telegram_topic_thread_id: string;
  max_ai_replies_per_ticket: number;
  allowed_domains: string[];
}

export interface Ticket {
  id: number;
  subject?: string;
  status?: string;
  priority?: string;
  user_id?: number;
  telegram_id?: number;
  username?: string;
  created_at?: string;
  updated_at?: string;
  last_message?: string;
  [k: string]: unknown;
}

export interface TicketList {
  items: Ticket[];
  total: number;
  not_configured?: boolean;
  error?: string;
}

export interface DashboardData {
  open_tickets: number | null;
  total_tickets: number | null;
  total_users: number | null;
  bot_health: { status?: string; api_version?: string; bot_version?: string } | null;
  errors: string[];
  not_configured?: boolean;
}

export const bedolagaApi = {
  getConfig: () => req<BedolagaConfig>("/config"),
  saveConfig: (base_url: string, token: string | undefined, auth_header = "X-API-Key") =>
    req<{ ok: boolean }>("/config", { method: "POST", body: JSON.stringify({ base_url, token, auth_header }) }),
  testConnection: () => req<{ ok: boolean; error?: string; health?: unknown }>("/config/test", { method: "POST" }),

  getAiConfig: () => req<BedolagaConfig>("/ai-config"),
  saveAiConfig: (body: Partial<BedolagaConfig> & { ai_provider_key?: string }) =>
    req<{ ok: boolean }>("/ai-config", { method: "POST", body: JSON.stringify(body) }),

  listTickets: (status?: string, limit = 50, offset = 0) => {
    const p = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) p.set("status", status);
    return req<TicketList>(`/tickets?${p}`);
  },
  getTicket: (id: number) => req<Ticket & { messages?: unknown[] }>(`/tickets/${id}`),
  replyTicket: (id: number, message: string) =>
    req<{ ok: boolean; error?: string }>(`/tickets/${id}/reply`, { method: "POST", body: JSON.stringify({ message }) }),
  setPriority: (id: number, priority: string) =>
    req<{ ok: boolean; error?: string }>(`/tickets/${id}/priority`, { method: "POST", body: JSON.stringify({ priority }) }),

  dashboard: () => req<DashboardData>("/dashboard"),
};
