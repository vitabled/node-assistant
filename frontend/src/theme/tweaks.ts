// Appearance tweaks — accent colour + density. Writes CSS variables on :root,
// fully self-contained (no other component needs to know). Persisted locally.

export type AccentKey = "blue" | "green" | "violet" | "amber" | "cyan";
export type Density = "comfortable" | "compact";

export const ACCENTS: Record<AccentKey, { base: string; hi: string; ink: string }> = {
  blue:   { base: "#4C8DFF", hi: "#82AEFF", ink: "#0A0E16" },
  green:  { base: "#3ECF8E", hi: "#63E0A7", ink: "#04140D" },
  violet: { base: "#9D7BFF", hi: "#B9A0FF", ink: "#0E0A1A" },
  amber:  { base: "#F0B054", hi: "#F7C77E", ink: "#1A1204" },
  cyan:   { base: "#38C3D2", hi: "#6FD8E4", ink: "#041416" },
};

function hexA(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

export function applyAccent(key: AccentKey): void {
  const a = ACCENTS[key] || ACCENTS.blue;
  const r = document.documentElement.style;
  r.setProperty("--accent", a.base);
  r.setProperty("--accent-hi", a.hi);
  r.setProperty("--accent-ink", a.ink);
  r.setProperty("--accent-dim", hexA(a.base, 0.13));
  r.setProperty("--accent-line", hexA(a.base, 0.4));
}

export function applyDensity(d: Density): void {
  document.body.dataset.density = d;
}

const ACCENT_KEY = "ni_accent";
const DENSITY_KEY = "ni_density";

export function loadAccent(): AccentKey {
  const v = localStorage.getItem(ACCENT_KEY);
  return v && v in ACCENTS ? (v as AccentKey) : "blue";
}
export function loadDensity(): Density {
  return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
}
export function saveAccent(k: AccentKey): void { localStorage.setItem(ACCENT_KEY, k); }
export function saveDensity(d: Density): void { localStorage.setItem(DENSITY_KEY, d); }
