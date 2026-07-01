import { useState, useRef, useEffect } from "react";
import { ChevronDown, X } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
}

interface Props {
  label:       string;
  selected:    string[];
  onChange:    (v: string[]) => void;
  options:     SelectOption[];
  placeholder?: string;
  disabled?:   boolean;
  loading?:    boolean;
  error?:      string;
}

export function MultiSelect({
  label, selected, onChange, options,
  placeholder = "— не выбрано —",
  disabled, loading, error,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const toggle = (value: string) => {
    onChange(
      selected.includes(value)
        ? selected.filter(v => v !== value)
        : [...selected, value]
    );
  };

  const remove = (value: string, e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(selected.filter(v => v !== value));
  };

  const isDisabled = disabled || loading;
  const selectedLabels = selected
    .map(v => options.find(o => o.value === v)?.label ?? v)
    .filter(Boolean);

  return (
    <div className="flex flex-col gap-1 relative" ref={ref}>
      <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">
        {label}
      </label>

      {/* Trigger */}
      <button
        type="button"
        onClick={() => !isDisabled && setOpen(o => !o)}
        disabled={isDisabled}
        className={`w-full min-h-[2.25rem] flex items-center gap-1.5 flex-wrap
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
        {selectedLabels.length > 0 ? (
          <span className="flex flex-wrap gap-1 flex-1 py-0.5">
            {selectedLabels.map((lbl, i) => (
              <span
                key={selected[i]}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded
                           bg-blue-950/70 border border-blue-800/50 text-blue-300 text-[11px]"
              >
                {lbl}
                <button
                  type="button"
                  onMouseDown={e => remove(selected[i], e)}
                  disabled={isDisabled}
                  className="text-blue-400 hover:text-white transition-colors"
                >
                  <X size={9} />
                </button>
              </span>
            ))}
          </span>
        ) : (
          <span className="flex-1 text-gray-700">
            {loading ? "Загрузка..." : placeholder}
          </span>
        )}
        <ChevronDown
          size={13}
          className={`shrink-0 text-gray-600 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full min-w-[200px] max-h-52 overflow-y-auto
                     bg-gray-950 border border-gray-700 rounded-lg shadow-xl py-1"
          style={{ top: "100%" }}
        >
          {options.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-600">Нет доступных опций</p>
          ) : (
            options.map(opt => {
              const checked = selected.includes(opt.value);
              return (
                <label
                  key={opt.value}
                  className="flex items-center gap-2.5 px-3 py-2 cursor-pointer
                             hover:bg-gray-800 transition-colors select-none"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(opt.value)}
                    className="w-3.5 h-3.5 rounded border-gray-600 bg-gray-800
                               accent-blue-500"
                  />
                  <span className={`text-sm ${checked ? "text-blue-300" : "text-gray-300"}`}>
                    {opt.label}
                  </span>
                </label>
              );
            })
          )}
        </div>
      )}

      {error && <p className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
