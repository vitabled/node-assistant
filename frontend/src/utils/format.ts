// Shared data-formatting helpers.

/**
 * Convert a 2-letter ISO country code (e.g. "US", "de") into a Unicode
 * regional-indicator flag emoji (🇺🇸, 🇩🇪).
 *
 * Each ASCII letter A–Z is offset by 127397 to its regional-indicator symbol,
 * and a pair of them renders as a flag. Falls back to the neutral globe 🌐 for
 * empty / null / malformed input.
 *
 * NOTE (Windows): some Windows builds don't render regional-indicator pairs as
 * flags and show the two letters instead — this is an OS font limitation, not a
 * bug here. If pixel-perfect flags on Windows are required, swap this for an SVG
 * icon set (e.g. flag-icons) at the call sites.
 */
export const getFlagEmoji = (countryCode?: string | null): string => {
  if (!countryCode || countryCode.length !== 2) return "🌐";
  const cc = countryCode.toUpperCase();
  // Only A–Z are valid regional indicators.
  if (!/^[A-Z]{2}$/.test(cc)) return "🌐";
  try {
    const codePoints = cc.split("").map(ch => 127397 + ch.charCodeAt(0));
    return String.fromCodePoint(...codePoints);
  } catch {
    return "🌐";
  }
};
