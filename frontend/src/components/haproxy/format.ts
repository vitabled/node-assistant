// Byte/bitrate/status formatting for the HAPROXY sections.

export function fmtBytes(n: number | null | undefined): string {
  const v = Number(n ?? 0);
  if (!isFinite(v) || v <= 0) return "0 Б";
  const units = ["Б", "КиБ", "МиБ", "ГиБ", "ТиБ", "ПиБ"];
  let i = 0;
  let x = v;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
  return `${x.toLocaleString("ru-RU", { maximumFractionDigits: i === 0 ? 0 : 2 })} ${units[i]}`;
}

export function fmtBps(bitsPerSecond: number | null | undefined): string {
  const v = Number(bitsPerSecond ?? 0);
  if (!isFinite(v) || v <= 0) return "0 бит/с";
  const units = ["бит/с", "Кбит/с", "Мбит/с", "Гбит/с", "Тбит/с"];
  let i = 0;
  let x = v;
  while (x >= 1000 && i < units.length - 1) { x /= 1000; i++; }
  return `${x.toLocaleString("ru-RU", { maximumFractionDigits: i === 0 ? 0 : 2 })} ${units[i]}`;
}

export function fmtPct(v: number | null | undefined): string {
  const n = Number(v ?? 0);
  return isFinite(n) ? `${n.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} %` : "—";
}

export function fmtUptime(seconds: number | null | undefined): string {
  const s = Math.floor(Number(seconds ?? 0));
  if (!isFinite(s) || s <= 0) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}д ${h}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  return `${m}м`;
}

// Status → CSS-var token color. Green ok / amber degraded / red down / muted unknown.
export type Tone = "ok" | "warn" | "down" | "muted";

export function nodeTone(status: string | undefined, online: boolean | undefined): Tone {
  if (online) return "ok";
  const s = (status || "").toLowerCase();
  if (s === "online") return "ok";
  if (s === "pending") return "warn";
  if (s === "error") return "down";
  if (s === "offline") return "down";
  return "muted";
}

export const TONE_COLOR: Record<Tone, string> = {
  ok: "var(--ok, #22c55e)",
  warn: "var(--warn, #f59e0b)",
  down: "var(--bad, #ef4444)",
  muted: "var(--t-low)",
};

export const TONE_LABEL: Record<Tone, string> = {
  ok: "онлайн", warn: "ожидание", down: "офлайн", muted: "неизвестно",
};
