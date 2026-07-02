// SVG country flags + country list — from the Node Installer redesign.
// Replaces emoji flags (which render as two letters on some Windows builds).
import type { ReactNode } from "react";
import { Globe } from "lucide-react";

export interface Country { code: string; name: string }

// Kept RU-first names (the app UI is Russian).
export const COUNTRIES: Country[] = [
  { code: "NL", name: "Нидерланды" },
  { code: "DE", name: "Германия" },
  { code: "FI", name: "Финляндия" },
  { code: "US", name: "США" },
  { code: "TR", name: "Турция" },
  { code: "FR", name: "Франция" },
  { code: "GB", name: "Великобритания" },
  { code: "PL", name: "Польша" },
  { code: "SE", name: "Швеция" },
  { code: "JP", name: "Япония" },
  { code: "IT", name: "Италия" },
  { code: "ES", name: "Испания" },
  { code: "CH", name: "Швейцария" },
  { code: "AT", name: "Австрия" },
  { code: "CZ", name: "Чехия" },
  { code: "EE", name: "Эстония" },
];

export const countryName = (cc: string): string =>
  COUNTRIES.find(c => c.code === cc)?.name ?? cc;

// Minimal, recognisable flag geometry per ISO code (viewBox 0 0 60 45).
const FLAG_DEFS: Record<string, ReactNode> = {
  NL: <><rect width="60" height="15" fill="#AE1C28"/><rect y="15" width="60" height="15" fill="#FFF"/><rect y="30" width="60" height="15" fill="#21468B"/></>,
  DE: <><rect width="60" height="15" fill="#1A1A1A"/><rect y="15" width="60" height="15" fill="#DD0000"/><rect y="30" width="60" height="15" fill="#FFCE00"/></>,
  FI: <><rect width="60" height="45" fill="#FFF"/><rect x="15" width="12" height="45" fill="#003580"/><rect y="16.5" width="60" height="12" fill="#003580"/></>,
  US: <><rect width="60" height="45" fill="#FFF"/>{[0,2,4,6].map(i => <rect key={i} y={i*6.43} width="60" height="6.43" fill="#B22234"/>)}<rect width="26" height="19.3" fill="#3C3B6E"/></>,
  TR: <><rect width="60" height="45" fill="#E30A17"/><circle cx="21" cy="22.5" r="9.5" fill="#FFF"/><circle cx="23.6" cy="22.5" r="7.6" fill="#E30A17"/><path d="M33.5 22.5l6.2 2-3.8-5.3v6.6l3.8-5.3z" fill="#FFF"/></>,
  FR: <><rect width="20" height="45" fill="#0055A4"/><rect x="20" width="20" height="45" fill="#FFF"/><rect x="40" width="20" height="45" fill="#EF4135"/></>,
  GB: <><rect width="60" height="45" fill="#012169"/><path d="M0 0L60 45M60 0L0 45" stroke="#FFF" strokeWidth="9"/><path d="M0 0L60 45M60 0L0 45" stroke="#C8102E" strokeWidth="4"/><rect x="22.5" width="15" height="45" fill="#FFF"/><rect y="15" width="60" height="15" fill="#FFF"/><rect x="25.5" width="9" height="45" fill="#C8102E"/><rect y="18" width="60" height="9" fill="#C8102E"/></>,
  PL: <><rect width="60" height="22.5" fill="#FFF"/><rect y="22.5" width="60" height="22.5" fill="#DC143C"/></>,
  SE: <><rect width="60" height="45" fill="#006AA7"/><rect x="15" width="10" height="45" fill="#FECC00"/><rect y="17.5" width="60" height="10" fill="#FECC00"/></>,
  JP: <><rect width="60" height="45" fill="#FFF"/><circle cx="30" cy="22.5" r="12.5" fill="#BC002D"/></>,
  IT: <><rect width="20" height="45" fill="#009246"/><rect x="20" width="20" height="45" fill="#FFF"/><rect x="40" width="20" height="45" fill="#CE2B37"/></>,
  ES: <><rect width="60" height="45" fill="#AA151B"/><rect y="11.25" width="60" height="22.5" fill="#F1BF00"/></>,
  CH: <><rect width="60" height="45" fill="#D52B1E"/><rect x="26" y="9" width="8" height="27" fill="#FFF"/><rect x="16.5" y="18.5" width="27" height="8" fill="#FFF"/></>,
  AT: <><rect width="60" height="15" fill="#ED2939"/><rect y="15" width="60" height="15" fill="#FFF"/><rect y="30" width="60" height="15" fill="#ED2939"/></>,
  CZ: <><rect width="60" height="22.5" fill="#FFF"/><rect y="22.5" width="60" height="22.5" fill="#D7141A"/><path d="M0 0L30 22.5L0 45Z" fill="#11457E"/></>,
  EE: <><rect width="60" height="15" fill="#0072CE"/><rect y="15" width="60" height="15" fill="#1A1A1A"/><rect y="30" width="60" height="15" fill="#FFF"/></>,
};

// Render a flag from an ISO code, or a code/globe fallback for unknown ones.
export function Flag({ cc, w = 20, title }: { cc?: string | null; w?: number; title?: string }) {
  const code = (cc || "").toUpperCase();
  const def = FLAG_DEFS[code];
  const h = Math.round(w * 0.72);
  if (!def) {
    return (
      <span className="avatar" title={title || code || "—"}
        style={{ width: w, height: h, fontSize: 8.5, background: "var(--bg3)", color: "var(--t-low)", borderRadius: 3 }}>
        {code || <Globe size={11} />}
      </span>
    );
  }
  return (
    <span className="flagbox" title={title || countryName(code)} style={{ width: w, height: h }}>
      <svg viewBox="0 0 60 45" width={w} height={h} style={{ display: "block" }} preserveAspectRatio="xMidYMid slice">{def}</svg>
    </span>
  );
}

// Resolve a flag code from a free-form location group (code, name, or embedded).
export function flagCodeFor(group: string): string | null {
  const g = (group || "").trim();
  if (/^[A-Za-z]{2}$/.test(g)) return g.toUpperCase();
  const gl = g.toLowerCase();
  const m = COUNTRIES.find(c => c.name.toLowerCase() === gl || gl.includes(c.name.toLowerCase()));
  return m ? m.code : null;
}
