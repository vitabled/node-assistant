// Appearance tweaks — accent colour + density. Writes CSS variables on :root,
// fully self-contained (no other component needs to know). Persisted locally.

export type AccentKey = "blue" | "green" | "violet" | "amber" | "cyan";
export type Density = "comfortable" | "compact";
export type ThemeMode = "light" | "dark" | "system";

export const THEME_MODES: { key: ThemeMode; label: string }[] = [
  { key: "system", label: "Системная" },
  { key: "light",  label: "Светлая" },
  { key: "dark",   label: "Тёмная" },
];

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

function prefersLight(): boolean {
  return !!window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
}

export function resolveThemeMode(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") return prefersLight() ? "light" : "dark";
  return mode;
}

// Live OS-preference listener, active only while mode === "system". Kept at
// module scope (survives re-renders); at most ONE is ever attached. We store the
// exact MediaQueryList we subscribed to (`_mqRef`) and detach from THAT object,
// not from a freshly-fetched one — so cleanup is correct even if a browser hands
// back a different MediaQueryList instance per matchMedia() call.
let _sysListener: ((e: MediaQueryListEvent) => void) | null = null;
let _mqRef: MediaQueryList | null = null;

export function applyThemeMode(mode: ThemeMode): void {
  if (_mqRef && _sysListener) _mqRef.removeEventListener("change", _sysListener);
  _sysListener = null;
  _mqRef = null;
  // The light/dark palettes live under :root[data-theme="…"]; :root itself is dark.
  document.documentElement.dataset.theme = resolveThemeMode(mode);
  const mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  if (mode === "system" && mq) {
    _sysListener = e => { document.documentElement.dataset.theme = e.matches ? "light" : "dark"; };
    mq.addEventListener("change", _sysListener);
    _mqRef = mq;
  }
}

const ACCENT_KEY = "ni_accent";
const DENSITY_KEY = "ni_density";
// Theme MODE is per-account (the plan: different accounts remember different
// modes); accent + density stay device-global. Falls back to a device-global
// key when no account is active (e.g. the login screen).
const themeModeKey = (accountId?: string | null) =>
  accountId ? `ni_thememode_${accountId}` : "ni_thememode";

export function loadAccent(): AccentKey {
  const v = localStorage.getItem(ACCENT_KEY);
  return v && v in ACCENTS ? (v as AccentKey) : "blue";
}
export function loadDensity(): Density {
  return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
}
export function loadThemeMode(accountId?: string | null): ThemeMode {
  const v = localStorage.getItem(themeModeKey(accountId));
  return v === "light" || v === "dark" || v === "system" ? v : "system";
}
export function saveAccent(k: AccentKey): void { localStorage.setItem(ACCENT_KEY, k); }
export function saveDensity(d: Density): void { localStorage.setItem(DENSITY_KEY, d); }
export function saveThemeMode(accountId: string | null | undefined, m: ThemeMode): void {
  localStorage.setItem(themeModeKey(accountId), m);
}
