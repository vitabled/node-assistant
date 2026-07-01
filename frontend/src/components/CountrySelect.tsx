import { useState, useRef, useEffect, useMemo } from "react";
import { ChevronDown, Search } from "lucide-react";
import { getFlagEmoji } from "../utils/format";

// ISO 3166-1 alpha-2 → { name, flag }. Mirrors the country picker in the
// Remnawave panel. "XX" is the panel's "unknown" sentinel.
export const COUNTRIES: { code: string; name: string; flag: string }[] = [
  { code: "XX", name: "Неизвестно", flag: "🏳️" },
  { code: "AL", name: "Albania", flag: "🇦🇱" },
  { code: "AM", name: "Armenia", flag: "🇦🇲" },
  { code: "AR", name: "Argentina", flag: "🇦🇷" },
  { code: "AT", name: "Austria", flag: "🇦🇹" },
  { code: "AU", name: "Australia", flag: "🇦🇺" },
  { code: "AZ", name: "Azerbaijan", flag: "🇦🇿" },
  { code: "BE", name: "Belgium", flag: "🇧🇪" },
  { code: "BG", name: "Bulgaria", flag: "🇧🇬" },
  { code: "BR", name: "Brazil", flag: "🇧🇷" },
  { code: "BY", name: "Belarus", flag: "🇧🇾" },
  { code: "CA", name: "Canada", flag: "🇨🇦" },
  { code: "CH", name: "Switzerland", flag: "🇨🇭" },
  { code: "CL", name: "Chile", flag: "🇨🇱" },
  { code: "CN", name: "China", flag: "🇨🇳" },
  { code: "CY", name: "Cyprus", flag: "🇨🇾" },
  { code: "CZ", name: "Czechia", flag: "🇨🇿" },
  { code: "DE", name: "Germany", flag: "🇩🇪" },
  { code: "DK", name: "Denmark", flag: "🇩🇰" },
  { code: "EE", name: "Estonia", flag: "🇪🇪" },
  { code: "ES", name: "Spain", flag: "🇪🇸" },
  { code: "FI", name: "Finland", flag: "🇫🇮" },
  { code: "FR", name: "France", flag: "🇫🇷" },
  { code: "GB", name: "United Kingdom", flag: "🇬🇧" },
  { code: "GE", name: "Georgia", flag: "🇬🇪" },
  { code: "GR", name: "Greece", flag: "🇬🇷" },
  { code: "HK", name: "Hong Kong", flag: "🇭🇰" },
  { code: "HR", name: "Croatia", flag: "🇭🇷" },
  { code: "HU", name: "Hungary", flag: "🇭🇺" },
  { code: "ID", name: "Indonesia", flag: "🇮🇩" },
  { code: "IE", name: "Ireland", flag: "🇮🇪" },
  { code: "IL", name: "Israel", flag: "🇮🇱" },
  { code: "IN", name: "India", flag: "🇮🇳" },
  { code: "IR", name: "Iran", flag: "🇮🇷" },
  { code: "IS", name: "Iceland", flag: "🇮🇸" },
  { code: "IT", name: "Italy", flag: "🇮🇹" },
  { code: "JP", name: "Japan", flag: "🇯🇵" },
  { code: "KZ", name: "Kazakhstan", flag: "🇰🇿" },
  { code: "LT", name: "Lithuania", flag: "🇱🇹" },
  { code: "LU", name: "Luxembourg", flag: "🇱🇺" },
  { code: "LV", name: "Latvia", flag: "🇱🇻" },
  { code: "MD", name: "Moldova", flag: "🇲🇩" },
  { code: "MX", name: "Mexico", flag: "🇲🇽" },
  { code: "MY", name: "Malaysia", flag: "🇲🇾" },
  { code: "NL", name: "Netherlands", flag: "🇳🇱" },
  { code: "NO", name: "Norway", flag: "🇳🇴" },
  { code: "NZ", name: "New Zealand", flag: "🇳🇿" },
  { code: "PL", name: "Poland", flag: "🇵🇱" },
  { code: "PT", name: "Portugal", flag: "🇵🇹" },
  { code: "RO", name: "Romania", flag: "🇷🇴" },
  { code: "RS", name: "Serbia", flag: "🇷🇸" },
  { code: "RU", name: "Russia", flag: "🇷🇺" },
  { code: "SA", name: "Saudi Arabia", flag: "🇸🇦" },
  { code: "SE", name: "Sweden", flag: "🇸🇪" },
  { code: "SG", name: "Singapore", flag: "🇸🇬" },
  { code: "SI", name: "Slovenia", flag: "🇸🇮" },
  { code: "SK", name: "Slovakia", flag: "🇸🇰" },
  { code: "TH", name: "Thailand", flag: "🇹🇭" },
  { code: "TR", name: "Turkey", flag: "🇹🇷" },
  { code: "TW", name: "Taiwan", flag: "🇹🇼" },
  { code: "UA", name: "Ukraine", flag: "🇺🇦" },
  { code: "US", name: "United States", flag: "🇺🇸" },
  { code: "UZ", name: "Uzbekistan", flag: "🇺🇿" },
  { code: "VN", name: "Vietnam", flag: "🇻🇳" },
  { code: "ZA", name: "South Africa", flag: "🇿🇦" },
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
      <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
        {label}
      </label>

      {/* Trigger */}
      <button
        type="button"
        onClick={() => !disabled && setOpen(o => !o)}
        disabled={disabled}
        className={`w-full min-h-[2.25rem] flex items-center gap-2
                    bg-gray-900/80 border rounded-md px-3 py-1.5 text-left
                    text-sm transition-colors focus:outline-none focus:ring-1
                    disabled:opacity-40 disabled:cursor-not-allowed
                    ${error
                      ? "border-red-600/70 focus:ring-red-500/20"
                      : open
                      ? "border-blue-500/70 ring-1 ring-blue-500/20"
                      : "border-gray-700/80 hover:border-gray-600"
                    }`}
      >
        {selected ? (
          <span className="flex-1 text-gray-100">
            <span className="mr-1.5">{getFlagEmoji(selected.code)}</span>
            {selected.name}
            <span className="text-gray-600 ml-1">({selected.code})</span>
          </span>
        ) : (
          <span className="flex-1 text-gray-700">{placeholder}</span>
        )}
        <ChevronDown
          size={13}
          className={`shrink-0 text-gray-600 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown with search */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full min-w-[220px]
                     bg-gray-950 border border-gray-700 rounded-lg shadow-xl"
          style={{ top: "100%" }}
        >
          <div className="flex items-center gap-2 px-2.5 py-2 border-b border-gray-800">
            <Search size={13} className="text-gray-600 shrink-0" />
            <input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Поиск страны..."
              className="w-full bg-transparent text-sm text-gray-100 placeholder:text-gray-700
                         focus:outline-none"
            />
          </div>
          <div className="max-h-52 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="px-3 py-2 text-xs text-gray-600">Ничего не найдено</p>
            ) : (
              filtered.map(c => {
                const active = c.code === value;
                return (
                  <button
                    key={c.code}
                    type="button"
                    onClick={() => { onChange(c.code); setOpen(false); }}
                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-left
                               hover:bg-gray-800 transition-colors select-none
                               ${active ? "bg-gray-800/60" : ""}`}
                  >
                    <span>{getFlagEmoji(c.code)}</span>
                    <span className={`text-sm ${active ? "text-blue-300" : "text-gray-300"}`}>
                      {c.name}
                    </span>
                    <span className="text-[11px] text-gray-600 ml-auto">{c.code}</span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}

      {error && <p className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
