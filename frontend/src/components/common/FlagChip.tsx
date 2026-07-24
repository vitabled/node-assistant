import { Globe } from "lucide-react";

/**
 * Fixed-size SVG flag chip from the `flag-icons` set (`fi fi-<cc>`), with a
 * Globe fallback for XX / empty / unknown codes.
 *
 * Deliberately NOT emoji: `getFlagEmoji` builds a regional-indicator pair, and
 * several Windows builds have no flag glyph for those and render the two bare
 * letters instead (see the note in `utils/format.ts`). Every place that shows a
 * country must use this component.
 *
 * Extracted from `CountrySelect` so the country picker, the dashboard and the
 * hostings map share one implementation — three copies would drift in size.
 */
export function FlagChip({ code, size = 18 }: { code?: string | null; size?: number }) {
  const cc = (code || "").toLowerCase();
  if (!cc || cc === "xx") return <Globe size={size - 4} style={{ color: "var(--t-low)", flex: "none" }} />;
  return (
    <span
      className={`fi fi-${cc}`}
      style={{
        width: size, height: Math.round(size * 0.72), borderRadius: 2, flex: "none",
        backgroundSize: "cover", backgroundPosition: "center",
        boxShadow: "0 0 0 1px rgba(0,0,0,.12)",
      }}
    />
  );
}
