// Typed client for the HAPROXY sections. Two layers:
//   /api/haproxy/config|test  → our backend (register/enable the NodeFlow panel)
//   /api/haproxy/proxy/*      → forwarded to NodeFlow /api/v1/* with the admin token
//                               injected server-side.
// The account bearer is attached globally by the auth fetch interceptor. Errors throw
// with a human message parsed from BOTH our `{detail}` and NodeFlow's `{error}` shapes.

import type {
  HaproxyConnState, DashboardOverview, NodeRecord, NodeOperational, RouteRecord,
  NodeTraffic, TrafficHistory, NodeFirewallPolicy, AgentRelease, HostKeyResult,
  BootstrapNodeRequest, BootstrapJobResponse, HAProxyControlState,
} from "./contracts";

function messageOf(body: any, status: number): string {
  if (body && typeof body === "object") {
    if (typeof body.detail === "string") return body.detail;
    if (body.error && typeof body.error === "object" && body.error.message) return body.error.message;
    if (typeof body.error === "string") return body.error;
    if (typeof body.detail !== "undefined") return JSON.stringify(body.detail);
  }
  return `HTTP ${status}`;
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw Object.assign(new Error(messageOf(body, res.status)), { status: res.status });
  return body as T;
}

// Our backend (config/test).
async function be<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  return parse<T>(await fetch(`/api/haproxy${path}`, { headers, ...init }));
}

// NodeFlow panel via the proxy.
async function nf<T>(subpath: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> =
    init?.body ? { "Content-Type": "application/json" } : {};
  return parse<T>(await fetch(`/api/haproxy/proxy/${subpath}`, { headers, ...init }));
}

const j = (b: unknown) => JSON.stringify(b);

export interface HaproxyConfigInput { enabled: boolean; base_url: string; admin_token: string }
export interface HaproxyTestResult { reachable: boolean; authenticated: boolean; version?: string; detail: string }

export const haproxyApi = {
  // ── connection (our backend) ──
  getConfig: () => be<HaproxyConnState>("/config"),
  saveConfig: (b: HaproxyConfigInput) => be<HaproxyConnState & { ok: boolean }>("/config", { method: "POST", body: j(b) }),
  test: () => be<HaproxyTestResult>("/test", { method: "POST" }),

  // ── dashboard ──
  overview: (range = "24h") => nf<DashboardOverview>(`overview?range=${encodeURIComponent(range)}`),

  // ── nodes ──
  nodes: () => nf<{ nodes: NodeRecord[] } | NodeRecord[]>("nodes"),
  node: (id: string) => nf<NodeRecord>(`nodes/${id}`),
  operational: (id: string) => nf<NodeOperational>(`nodes/${id}/operational`),
  deleteNode: (id: string) => nf<void>(`nodes/${id}`, { method: "DELETE" }),
  reinstallNode: (id: string) => nf<unknown>(`nodes/${id}/reinstall`, { method: "POST" }),
  rotateCreds: (id: string) => nf<unknown>(`nodes/${id}/rotate-credentials`, { method: "POST" }),

  // ── bootstrap (add node) ──
  hostKey: (b: Partial<BootstrapNodeRequest>) => nf<HostKeyResult>("bootstrap/host-key", { method: "POST", body: j(b) }),
  bootstrap: (b: BootstrapNodeRequest) => nf<BootstrapJobResponse>("bootstrap", { method: "POST", body: j(b) }),
  bootstrapJob: (jobId: string) => nf<BootstrapJobResponse>(`bootstrap/${jobId}`),

  // ── haproxy control ──
  haproxyControl: (id: string) => nf<HAProxyControlState>(`nodes/${id}/haproxy`),
  setHaproxy: (id: string, enabled: boolean) => nf<HAProxyControlState>(`nodes/${id}/haproxy`, { method: "PATCH", body: j({ enabled }) }),

  // ── routes ──
  routes: (nodeId: string) => nf<{ routes: RouteRecord[] } | RouteRecord[]>(`nodes/${nodeId}/routes`),
  createRoute: (nodeId: string, b: unknown) => nf<RouteRecord>(`nodes/${nodeId}/routes`, { method: "POST", body: j(b) }),
  route: (nodeId: string, rid: string) => nf<RouteRecord>(`nodes/${nodeId}/routes/${rid}`),
  updateRoute: (nodeId: string, rid: string, b: unknown) => nf<RouteRecord>(`nodes/${nodeId}/routes/${rid}`, { method: "PATCH", body: j(b) }),
  deleteRoute: (nodeId: string, rid: string) => nf<void>(`nodes/${nodeId}/routes/${rid}`, { method: "DELETE" }),

  // ── traffic ──
  traffic: (nodeId: string) => nf<NodeTraffic>(`nodes/${nodeId}/traffic`),
  trafficHistory: (nodeId: string, range = "24h") => nf<TrafficHistory>(`nodes/${nodeId}/traffic/history?range=${encodeURIComponent(range)}`),

  // ── firewall ──
  firewall: (nodeId: string) => nf<NodeFirewallPolicy>(`nodes/${nodeId}/firewall`),
  setFirewall: (nodeId: string, b: { mode: string; tcp_ports: number[] }) => nf<NodeFirewallPolicy>(`nodes/${nodeId}/firewall`, { method: "PATCH", body: j(b) }),

  // ── agent releases ──
  releases: () => nf<{ releases: AgentRelease[] } | AgentRelease[]>("agent-releases"),
  deleteRelease: (id: string) => nf<void>(`agent-releases/${id}`, { method: "DELETE" }),
};

// NodeFlow wraps list responses as {nodes:[…]} / {routes:[…]} / {releases:[…]} in some
// versions and returns a bare array in others — normalize both.
export function asList<T>(v: { [k: string]: T[] } | T[] | null | undefined, key: string): T[] {
  if (Array.isArray(v)) return v;
  if (v && Array.isArray((v as any)[key])) return (v as any)[key];
  return [];
}
