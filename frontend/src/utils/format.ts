// Location / formatting helpers.

/**
 * Convert a 2-letter ISO 3166-1 alpha-2 country code into a Unicode
 * regional-indicator flag emoji (e.g. "US" → 🇺🇸, "de" → 🇩🇪).
 *
 * Each ASCII letter A–Z (0x41–0x5A) maps to a regional-indicator symbol
 * (0x1F1E6–0x1F1FF); the offset is 127397 (0x1F1E6 − 0x41).
 * Returns 🌐 for empty / null / non-2-letter / invalid input.
 */
export const getFlagEmoji = (countryCode?: string | null): string => {
  if (!countryCode || countryCode.length !== 2) return "🌐";
  const codePoints = countryCode
    .toUpperCase()
    .split("")
    .map(char => 127397 + char.charCodeAt(0));
  // Guard: only A–Z produce valid regional indicators.
  if (codePoints.some(cp => cp < 0x1f1e6 || cp > 0x1f1ff)) return "🌐";
  try {
    return String.fromCodePoint(...codePoints);
  } catch {
    return "🌐"; // fallback placeholder if the code is not valid
  }
};

/**
 * Best-effort flag for a free-form location string (name or code, may contain
 * an already-embedded flag emoji). Returns "" when nothing recognisable is
 * found, so callers can choose to render nothing rather than a bare 🌐.
 *
 * `names` is an optional [name → code] lookup (e.g. from CountrySelect) so a
 * country NAME like "Germany" resolves to 🇩🇪.
 */
export const flagFromLocation = (
  location?: string | null,
  names: { code: string; name: string }[] = [],
): string => {
  if (!location) return "";
  const s = location.trim();
  // Already an embedded flag (pair of regional indicators)?
  const chars = Array.from(s);
  for (let i = 0; i < chars.length - 1; i++) {
    const a = chars[i].codePointAt(0)!;
    const b = chars[i + 1].codePointAt(0)!;
    if (a >= 0x1f1e6 && a <= 0x1f1ff && b >= 0x1f1e6 && b <= 0x1f1ff) {
      return chars[i] + chars[i + 1];
    }
  }
  // Bare 2-letter code.
  if (/^[A-Za-z]{2}$/.test(s)) return getFlagEmoji(s);
  // Country name contained in the string.
  const lower = s.toLowerCase();
  const hit = names.find(c => c.code !== "XX" && lower.includes(c.name.toLowerCase()));
  return hit ? getFlagEmoji(hit.code) : "";
};
