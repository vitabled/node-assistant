// Typed fetch wrapper for /api/hostings. Errors throw with the backend `detail`
// (shown as toasts). The account bearer token is attached globally by the auth
// fetch interceptor (src/auth/apiClient.ts) — no per-call auth here.

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const res = await fetch(`/api/hostings${path}`, { headers, ...init });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    throw Object.assign(new Error(msg), { status: res.status });
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface Tariff {
  name: string; specs: string; bandwidth: string;
  price: number; currency: string; period: string;
}
export interface HostingLocation {
  city: string; country_code: string; lat: number; lng: number; note: string;
}
export interface AsnRef {
  number: number; name: string; website: string;
}
export type MetricKey = "price" | "quality" | "loyalty" | "fairuse" | "panel" | "ru_access";
/** Субъективные оценки 1.0..100.0. `null`/отсутствие ключа = «не оценено» —
 *  это состояние обязано отличаться от низкой оценки, поэтому не 0. */
export interface HostingMetrics {
  price?: number | null; quality?: number | null; loyalty?: number | null;
  fairuse?: number | null; panel?: number | null; ru_access?: number | null;
  // Fair-use политики есть не у всех — тогда метрика прячется, а не занижается.
  fairuse_hidden?: boolean;
}
export interface Hosting {
  id: string; name: string; website: string; notes: string; features: string;
  tags: string[];
  // Ids in the shared media store (see components/common/MediaDrop.tsx). Optional:
  // records saved before the field existed come back without the key — read it as
  // `(h.media || [])` everywhere, same as tags/asns.
  media?: string[];
  tariffs: Tariff[]; locations: HostingLocation[]; asns: AsnRef[];
  // Как и media/tags: старые записи приходят без ключа — читать `(h.metrics || {})`.
  metrics?: HostingMetrics;
  provider_ref?: string | null; created_at: number;
}

// The POST/PUT body (server assigns id/created_at).
export type HostingBody = Omit<Hosting, "id" | "created_at">;

export const hostingsApi = {
  list: () => req<Hosting[]>(""),
  tags: () => req<string[]>("/tags"),
  create: (b: HostingBody) => req<Hosting>("", { method: "POST", body: JSON.stringify(b) }),
  update: (id: string, b: HostingBody) => req<Hosting>(`/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  remove: (id: string) => req<void>(`/${id}`, { method: "DELETE" }),
};

export const CURRENCIES = ["USD", "EUR", "RUB", "GBP", "UAH"];
export const PERIODS: { v: string; l: string }[] = [
  { v: "mo", l: "/мес" }, { v: "yr", l: "/год" }, { v: "hr", l: "/час" }, { v: "once", l: "разово" },
];
export const periodLabel = (p: string) => PERIODS.find(x => x.v === p)?.l ?? `/${p}`;

/** Minimum price across tariffs (with its currency), or null when none priced. */
export function minTariff(h: Hosting): { price: number; currency: string; period: string } | null {
  const priced = (h.tariffs || []).filter(t => t.price > 0);
  if (!priced.length) return null;
  return priced.reduce((a, b) => (b.price < a.price ? b : a));
}
