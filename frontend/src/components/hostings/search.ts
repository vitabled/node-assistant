// Free-text search over the hostings catalogue (Wave-7 Plan D Ф4), replacing the
// continent toggles. Pure functions on purpose: the map is hard to test, a
// matcher is not.

import type { Hosting } from "./api";
import { resolveCountryCode, RU_ALIASES } from "../../utils/countryAliases";
import { COUNTRIES } from "../CountrySelect";

const norm = (s: string) => (s || "").toLowerCase().replace(/ё/g, "е").trim();

export interface PriceQuery { op: "<" | ">" | "range"; a: number; b?: number }

/**
 * Recognise a price filter: `<20`, `>5`, `10-30` (spaces tolerated). Returns
 * null for anything else, so ordinary words never accidentally filter by price.
 */
export function parsePriceQuery(q: string): PriceQuery | null {
  const s = (q || "").replace(/\s+/g, "");
  let m = /^<(\d+(?:\.\d+)?)$/.exec(s);
  if (m) return { op: "<", a: parseFloat(m[1]) };
  m = /^>(\d+(?:\.\d+)?)$/.exec(s);
  if (m) return { op: ">", a: parseFloat(m[1]) };
  m = /^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$/.exec(s);
  if (m) return { op: "range", a: parseFloat(m[1]), b: parseFloat(m[2]) };
  return null;
}

function matchesPrice(h: Hosting, p: PriceQuery): boolean {
  return (h.tariffs || []).some(t => {
    if (!(t.price > 0)) return false;
    if (p.op === "<") return t.price < p.a;
    if (p.op === ">") return t.price > p.a;
    return t.price >= p.a && t.price <= (p.b ?? p.a);
  });
}

/** Every searchable string of a hosting, already normalised. */
function haystack(h: Hosting): string[] {
  const out: string[] = [norm(h.name), norm(h.features), norm(h.notes)];
  for (const t of h.tariffs || []) {
    out.push(norm(t.name), norm(t.specs), norm(t.bandwidth || ""));
  }
  for (const l of h.locations || []) {
    out.push(norm(l.city), norm(l.note), norm(l.country_code));
    const cc = (l.country_code || "").toUpperCase();
    // Let the operator type the country in either language.
    const en = COUNTRIES.find(c => c.code === cc);
    if (en) out.push(norm(en.name));
    for (const ru of RU_ALIASES[cc] ?? []) out.push(ru);
  }
  return out.filter(Boolean);
}

/**
 * Does this hosting match the query? Empty query matches everything.
 * A price expression filters by price; anything else is a substring match over
 * name / city / country (RU + EN + code) / tariff / channel / notes.
 */
export function matchHosting(h: Hosting, query: string): boolean {
  const q = norm(query);
  if (!q) return true;
  const price = parsePriceQuery(q);
  if (price) return matchesPrice(h, price);
  const hay = haystack(h);
  return hay.some(s => s.includes(q));
}

/** alpha-2 codes of the locations of every matching hosting — used to highlight
 *  countries on the map. */
export function matchedCountries(hostings: Hosting[], query: string): Set<string> {
  const out = new Set<string>();
  for (const h of hostings) {
    if (!matchHosting(h, query)) continue;
    for (const l of h.locations || []) {
      const cc = (l.country_code || "").toUpperCase() || resolveCountryCode(l.city);
      if (cc) out.add(cc);
    }
  }
  return out;
}
