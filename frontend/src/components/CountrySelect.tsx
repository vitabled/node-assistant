import { useState, useRef, useEffect, useMemo } from "react";
import { ChevronDown, Search } from "lucide-react";
import { FlagChip } from "./common/FlagChip";

// ISO 3166-1 alpha-2 → { name }. Mirrors the country picker in the Remnawave
// panel. "XX" is the panel's "unknown" sentinel. Flags render via the
// `flag-icons` SVG set (`fi fi-<cc>`), not emoji.
export const COUNTRIES: { code: string; name: string }[] = [
  { code: "XX", name: "Неизвестно" },
  { code: "AL", name: "Albania" },
  { code: "AM", name: "Armenia" },
  { code: "AR", name: "Argentina" },
  { code: "AT", name: "Austria" },
  { code: "AU", name: "Australia" },
  { code: "AZ", name: "Azerbaijan" },
  { code: "BE", name: "Belgium" },
  { code: "BG", name: "Bulgaria" },
  { code: "BR", name: "Brazil" },
  { code: "BY", name: "Belarus" },
  { code: "CA", name: "Canada" },
  { code: "CH", name: "Switzerland" },
  { code: "CL", name: "Chile" },
  { code: "CN", name: "China" },
  { code: "CY", name: "Cyprus" },
  { code: "CZ", name: "Czechia" },
  { code: "DE", name: "Germany" },
  { code: "DK", name: "Denmark" },
  { code: "EE", name: "Estonia" },
  { code: "ES", name: "Spain" },
  { code: "FI", name: "Finland" },
  { code: "FR", name: "France" },
  { code: "GB", name: "United Kingdom" },
  { code: "GE", name: "Georgia" },
  { code: "GR", name: "Greece" },
  { code: "HK", name: "Hong Kong" },
  { code: "HR", name: "Croatia" },
  { code: "HU", name: "Hungary" },
  { code: "ID", name: "Indonesia" },
  { code: "IE", name: "Ireland" },
  { code: "IL", name: "Israel" },
  { code: "IN", name: "India" },
  { code: "IR", name: "Iran" },
  { code: "IS", name: "Iceland" },
  { code: "IT", name: "Italy" },
  { code: "JP", name: "Japan" },
  { code: "KZ", name: "Kazakhstan" },
  { code: "LT", name: "Lithuania" },
  { code: "LU", name: "Luxembourg" },
  { code: "LV", name: "Latvia" },
  { code: "MD", name: "Moldova" },
  { code: "MX", name: "Mexico" },
  { code: "MY", name: "Malaysia" },
  { code: "NL", name: "Netherlands" },
  { code: "NO", name: "Norway" },
  { code: "NZ", name: "New Zealand" },
  { code: "PL", name: "Poland" },
  { code: "PT", name: "Portugal" },
  { code: "RO", name: "Romania" },
  { code: "RS", name: "Serbia" },
  { code: "RU", name: "Russia" },
  { code: "SA", name: "Saudi Arabia" },
  { code: "SE", name: "Sweden" },
  { code: "SG", name: "Singapore" },
  { code: "SI", name: "Slovenia" },
  { code: "SK", name: "Slovakia" },
  { code: "TH", name: "Thailand" },
  { code: "TR", name: "Turkey" },
  { code: "TW", name: "Taiwan" },
  { code: "UA", name: "Ukraine" },
  { code: "US", name: "United States" },
  { code: "UZ", name: "Uzbekistan" },
  { code: "VN", name: "Vietnam" },
  { code: "ZA", name: "South Africa" },
];

interface Props {
  label:        string;
  value:        string;            // ISO alpha-2 code
  onChange:     (code: string) => void;
  placeholder?: string;
  disabled?:    boolean;
  error?:       string;
}

export function CountrySelect({
  label, value, onChange,
  placeholder = "— выберите страну —",
  disabled, error,
}: Props) {
  const [open, setOpen]     = useState(false);
  const [query, setQuery]   = useState("");
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Reset search each time the dropdown opens
  useEffect(() => { if (open) setQuery(""); }, [open]);

  const selected = COUNTRIES.find(c => c.code === value);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COUNTRIES;
    return COUNTRIES.filter(
      c => c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q)
    );
  }, [query]);

  return (
    <div className="flex flex-col gap-1 relative" ref={ref}>
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
        {label}
      </label>

      {/* Trigger */}
      <button
        type="button"
        onClick={() => !disabled && setOpen(o => !o)}
        disabled={disabled}
        className="input flex items-center gap-2 text-left transition-colors
                   disabled:opacity-40 disabled:cursor-not-allowed"
        style={error ? { borderColor: "var(--err-line)" } : undefined}
      >
        {selected ? (
          <span className="flex-1 flex items-center gap-2" style={{ color: "var(--t-hi)" }}>
            <FlagChip code={selected.code} />
            {selected.name}
            <span style={{ color: "var(--t-faint)" }}>({selected.code})</span>
          </span>
        ) : (
          <span className="flex-1" style={{ color: "var(--t-faint)" }}>{placeholder}</span>
        )}
        <ChevronDown
          size={13}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          style={{ color: "var(--t-faint)" }}
        />
      </button>

      {/* Dropdown with search */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full min-w-[220px] max-w-full rounded-lg"
          style={{ top: "100%", background: "var(--bg1)", border: "1px solid var(--line)", boxShadow: "var(--shadow-pop)" }}
        >
          <div className="flex items-center gap-2 px-2.5 py-2" style={{ borderBottom: "1px solid var(--line-soft)" }}>
            <Search size={13} className="shrink-0" style={{ color: "var(--t-faint)" }} />
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Поиск страны..."
              className="w-full bg-transparent text-sm focus:outline-none"
              style={{ color: "var(--t-hi)" }}
            />
          </div>
          <div className="max-h-52 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="px-3 py-2 text-xs" style={{ color: "var(--t-faint)" }}>Ничего не найдено</p>
            ) : (
              filtered.map(c => {
                const active = c.code === value;
                return (
                  <button
                    key={c.code}
                    type="button"
                    onClick={() => { onChange(c.code); setOpen(false); }}
                    className="w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors select-none hover:bg-[var(--bg3)]"
                    style={active ? { background: "var(--accent-dim)" } : undefined}
                  >
                    <FlagChip code={c.code} />
                    <span className="text-sm" style={{ color: active ? "var(--accent-hi)" : "var(--t-mid)" }}>
                      {c.name}
                    </span>
                    <span className="text-[11px] ml-auto" style={{ color: "var(--t-faint)" }}>{c.code}</span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}

      {error && <p className="errmsg">{error}</p>}
    </div>
  );
}
