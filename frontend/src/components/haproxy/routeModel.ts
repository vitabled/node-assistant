// Route draft ↔ record ↔ API payload. Ported (lean) from NodeFlow's routes/model.ts.
// Client-side validation is light (required name, port ranges, target present); the
// deep rules (SNI DNS, listener overlap, expert-override HAProxy safety) are enforced
// server-side by NodeFlow's route_validation.go — we surface its error message.

import type { RouteRecord } from "./contracts";

export type RouteMatchMode = "any_tcp" | "sni" | "destination_ip";
export type RouteTargetMode = "ip" | "domain" | "unix";
export type QuotaPeriod = "hourly" | "daily" | "calendar_month" | "monthly_from_creation";
export type QuotaAction = "observe" | "block_new";
export type ProxyProtocol = "none" | "v1" | "v2";

export interface RouteDraft {
  name: string;
  matchMode: RouteMatchMode;
  listenerIP: string;
  listenerPort: string;
  snis: string;              // comma/space/newline separated in the form
  targetMode: RouteTargetMode;
  targetHost: string;
  targetPort: string;
  unixSocketPath: string;
  healthCheck: boolean;
  proxyProtocol: ProxyProtocol;
  quotaEnabled: boolean;
  quotaValue: string;
  quotaUnit: "GiB" | "TiB";
  quotaPeriod: QuotaPeriod;
  quotaAction: QuotaAction;
  expertOverride: string;
}

const GIB = 1024 ** 3;
const TIB = 1024 ** 4;
const IPV4 = /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

export const QUOTA_PERIODS: { value: QuotaPeriod; label: string }[] = [
  { value: "hourly", label: "Каждый час" },
  { value: "daily", label: "Ежедневно (00:00 UTC)" },
  { value: "calendar_month", label: "Календарный месяц" },
  { value: "monthly_from_creation", label: "Месяц от создания" },
];

export function routeDisplayName(r: RouteRecord): string {
  if (r.name?.trim()) return r.name.trim();
  if (r.fallback) return `tcp-${r.listener_port}`;
  return r.snis?.[0] ?? r.hostname ?? `route-${r.listener_port}`;
}

function isIP(v: string): boolean {
  const t = v.trim();
  return IPV4.test(t) || (t.includes(":") && /^[a-f0-9:]+$/i.test(t));
}

export function emptyRouteDraft(): RouteDraft {
  return {
    name: "", matchMode: "any_tcp", listenerIP: "*", listenerPort: "443", snis: "",
    targetMode: "ip", targetHost: "", targetPort: "443", unixSocketPath: "", healthCheck: true,
    proxyProtocol: "none", quotaEnabled: false, quotaValue: "", quotaUnit: "GiB",
    quotaPeriod: "calendar_month", quotaAction: "observe", expertOverride: "",
  };
}

export function routeToDraft(r: RouteRecord): RouteDraft {
  const bytes = r.quota_bytes ?? 0;
  const useTiB = bytes >= TIB && bytes % TIB === 0;
  const host = r.target_host ?? "";
  const targetMode: RouteTargetMode = r.target_type === "unix" ? "unix" : isIP(host) ? "ip" : "domain";
  return {
    name: routeDisplayName(r),
    matchMode: (["any_tcp", "sni", "destination_ip"].includes(r.match_mode) ? r.match_mode : "any_tcp") as RouteMatchMode,
    listenerIP: r.listener_ip || "*", listenerPort: String(r.listener_port || 443),
    snis: (r.snis ?? []).join(", "), targetMode, targetHost: host, targetPort: String(r.target_port || 443),
    unixSocketPath: r.unix_socket_path ?? "", healthCheck: r.health_check ?? true,
    proxyProtocol: (["none", "v1", "v2"].includes(r.proxy_protocol) ? r.proxy_protocol : "none") as ProxyProtocol,
    quotaEnabled: bytes > 0, quotaValue: bytes > 0 ? String(bytes / (useTiB ? TIB : GIB)) : "",
    quotaUnit: useTiB ? "TiB" : "GiB",
    quotaPeriod: (["hourly", "daily", "calendar_month", "monthly_from_creation"].includes(r.quota_period)
      ? r.quota_period : "calendar_month") as QuotaPeriod,
    quotaAction: r.quota_action === "block_new" ? "block_new" : "observe",
    expertOverride: r.custom_fragment ?? "",
  };
}

function parseSnis(s: string): string[] {
  return s.split(/[\s,]+/).map(v => v.trim().replace(/\.$/, "").toLowerCase()).filter(Boolean);
}

export function quotaBytes(d: RouteDraft): number | null {
  if (!d.quotaEnabled || !d.quotaValue) return null;
  const mult = d.quotaUnit === "TiB" ? TIB : GIB;
  return Math.round(Number(d.quotaValue) * mult);
}

/** Light client-side check → human message, or "" if OK (server does the deep validation). */
export function validateDraft(d: RouteDraft): string {
  if (!d.name.trim()) return "Укажите имя маршрута.";
  const lp = Number(d.listenerPort);
  if (!Number.isInteger(lp) || lp < 1 || lp > 65535) return "Порт listener должен быть 1–65535.";
  if (d.matchMode === "sni" && parseSnis(d.snis).length === 0) return "Для режима SNI добавьте хотя бы один SNI.";
  if (d.matchMode === "any_tcp" && d.listenerIP.trim() !== "*") return "Для «Любой TCP» listener должен быть *.";
  if (d.targetMode === "unix") {
    if (!d.unixSocketPath.trim().startsWith("/")) return "Укажите абсолютный путь Unix-сокета.";
  } else {
    if (!d.targetHost.trim()) return "Укажите адрес назначения.";
    const tp = Number(d.targetPort);
    if (!Number.isInteger(tp) || tp < 1 || tp > 65535) return "Порт target должен быть 1–65535.";
  }
  if (d.quotaEnabled && (!d.quotaValue || Number(d.quotaValue) <= 0)) return "Лимит должен быть положительным числом.";
  return "";
}

export function routePayload(d: RouteDraft, enabled: boolean, expectedVersion?: number) {
  const fallback = d.matchMode !== "sni";
  const unix = d.targetMode === "unix";
  const snis = fallback ? [] : parseSnis(d.snis);
  return {
    ...(expectedVersion ? { expected_version: expectedVersion } : {}),
    name: d.name.trim(),
    hostname: snis[0] ?? "",
    listener_ip: d.listenerIP.trim() || "*",
    listener_port: Number(d.listenerPort),
    match_mode: d.matchMode,
    snis,
    fallback,
    target_type: unix ? "unix" : "tcp",
    target_host: unix ? "" : d.targetHost.trim(),
    target_port: unix ? 0 : Number(d.targetPort),
    unix_socket_path: unix ? d.unixSocketPath.trim() : "",
    health_check: d.healthCheck,
    proxy_protocol: d.proxyProtocol,
    quota_bytes: quotaBytes(d),
    quota_action: d.quotaEnabled ? d.quotaAction : "observe",
    quota_period: d.quotaPeriod,
    enabled,
    custom_fragment: d.expertOverride,
  };
}
