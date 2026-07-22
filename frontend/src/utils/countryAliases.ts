// Resolving a free-form location label into an ISO 3166-1 alpha-2 code.
//
// Why this exists: xray-checker hands us whatever the subscription put in
// `groupName` — a bare code ("NL"), an English name, a Russian name, a name with
// an embedded flag emoji, or a mix ("NL Нидерланды 🚀"). `COUNTRIES` only carries
// ENGLISH names (it mirrors the Remnawave panel's picker), so a Russian label
// resolved to nothing and the dashboard fell back to a globe.
//
// The alias table lives here rather than as a `nameRu` field on `COUNTRIES` so
// that list stays a faithful mirror of the panel's own picker.

import { COUNTRIES } from "../components/CountrySelect";

/** Russian (and colloquial) names per alpha-2 code. Codes beyond `COUNTRIES`
 *  are allowed — flag-icons covers the full ISO set. */
export const RU_ALIASES: Record<string, string[]> = {
  AE: ["оаэ", "эмираты", "объединенные арабские эмираты"],
  AL: ["албания"],
  AM: ["армения"],
  AR: ["аргентина"],
  AT: ["австрия"],
  AU: ["австралия"],
  AZ: ["азербайджан"],
  BE: ["бельгия"],
  BG: ["болгария"],
  BR: ["бразилия"],
  BY: ["беларусь", "белоруссия"],
  CA: ["канада"],
  CH: ["швейцария"],
  CL: ["чили"],
  CN: ["китай"],
  CY: ["кипр"],
  CZ: ["чехия"],
  DE: ["германия"],
  DK: ["дания"],
  EE: ["эстония"],
  ES: ["испания"],
  FI: ["финляндия"],
  FR: ["франция"],
  GB: ["великобритания", "британия", "англия"],
  GE: ["грузия"],
  GR: ["греция"],
  HK: ["гонконг"],
  HR: ["хорватия"],
  HU: ["венгрия"],
  ID: ["индонезия"],
  IE: ["ирландия"],
  IL: ["израиль"],
  IN: ["индия"],
  IR: ["иран"],
  IS: ["исландия"],
  IT: ["италия"],
  JP: ["япония"],
  KZ: ["казахстан"],
  LT: ["литва"],
  LU: ["люксембург"],
  LV: ["латвия"],
  MD: ["молдова", "молдавия"],
  MX: ["мексика"],
  MY: ["малайзия"],
  NL: ["нидерланды", "голландия"],
  NO: ["норвегия"],
  NZ: ["новая зеландия"],
  PL: ["польша"],
  PT: ["португалия"],
  RO: ["румыния"],
  RS: ["сербия"],
  RU: ["россия"],
  SA: ["саудовская аравия", "саудовская"],
  SE: ["швеция"],
  SG: ["сингапур"],
  SI: ["словения"],
  SK: ["словакия"],
  TH: ["таиланд"],
  TR: ["турция"],
  TW: ["тайвань"],
  UA: ["украина"],
  US: ["сша", "соединенные штаты", "америка"],
  UZ: ["узбекистан"],
  VN: ["вьетнам"],
  ZA: ["юар", "южная африка"],
};

/** Codes we are willing to accept from a bare 2-letter token. Restricting to a
 *  known set stops random 2-letter words from becoming flags. */
const KNOWN_CODES = new Set<string>([
  ...COUNTRIES.map(c => c.code.toUpperCase()),
  ...Object.keys(RU_ALIASES),
]);

const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е").trim();

const RI_LO = 0x1f1e6;  // 🇦
const RI_HI = 0x1f1ff;  // 🇿

/**
 * Find an embedded regional-indicator flag emoji and return its alpha-2 code.
 * Returns `["", input]` when there is none; otherwise the code plus the input
 * with that pair (and the whitespace it leaves behind) removed.
 */
export function splitFlagEmoji(s: string): { code: string; rest: string } {
  const arr = Array.from(s);
  for (let i = 0; i < arr.length - 1; i++) {
    const a = arr[i].codePointAt(0)!, b = arr[i + 1].codePointAt(0)!;
    if (a >= RI_LO && a <= RI_HI && b >= RI_LO && b <= RI_HI) {
      const code = String.fromCharCode(a - RI_LO + 65, b - RI_LO + 65);
      const rest = [...arr.slice(0, i), ...arr.slice(i + 2)].join("").replace(/\s+/g, " ").trim();
      return { code, rest };
    }
  }
  return { code: "", rest: s };
}

/**
 * Best-effort alpha-2 for a free-form label. Returns "" when nothing matches —
 * callers render a Globe rather than guessing.
 *
 * Order is deliberate: an explicit flag or code beats a name match, because a
 * label like "NL Amsterdam" carries its own answer and name matching is fuzzy.
 */
export function resolveCountryCode(group?: string | null): string {
  const raw = (group || "").trim();
  if (!raw) return "";

  // 1. an emoji flag anywhere in the string
  const { code: emoji } = splitFlagEmoji(raw);
  if (emoji) return emoji;

  // 2. the whole label, or its first token, is a known 2-letter code
  for (const token of [raw, ...raw.split(/[^A-Za-z]+/)]) {
    if (/^[A-Za-z]{2}$/.test(token)) {
      const up = token.toUpperCase();
      if (KNOWN_CODES.has(up)) return up;
    }
  }

  const low = norm(raw);

  // 3. English name from COUNTRIES (exact, then substring)
  const en = COUNTRIES.filter(c => c.code !== "XX");
  const exactEn = en.find(c => norm(c.name) === low);
  if (exactEn) return exactEn.code;

  // 4. Russian alias (exact, then substring)
  for (const [code, names] of Object.entries(RU_ALIASES)) {
    if (names.some(n => n === low)) return code;
  }

  const subEn = en.find(c => low.includes(norm(c.name)));
  if (subEn) return subEn.code;

  for (const [code, names] of Object.entries(RU_ALIASES)) {
    if (names.some(n => low.includes(n))) return code;
  }

  return "";
}
