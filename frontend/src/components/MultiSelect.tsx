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
      <label className="label">{label}</label>

      {/* Trigger */}
      <button
        type="button"
        onClick={() => !isDisabled && setOpen(o => !o)}
        disabled={isDisabled}
        className={`input ${error ? "err" : ""}`}
        style={{
          display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
          minHeight: "2.25rem", textAlign: "left", cursor: isDisabled ? "not-allowed" : "pointer",
        }}
      >
        {selectedLabels.length > 0 ? (
          <span className="flex flex-wrap gap-1 flex-1 py-0.5">
            {selectedLabels.map((lbl, i) => (
              <span key={selected[i]} className="chip accent" style={{ padding: "1px 8px" }}>
                {lbl}
                <button
                  type="button"
                  onMouseDown={e => remove(selected[i], e)}
                  disabled={isDisabled}
                  style={{ display: "inline-flex", opacity: 0.75 }}
                >
                  <X size={9} />
                </button>
              </span>
            ))}
          </span>
        ) : (
          <span className="flex-1" style={{ color: "var(--t-faint)" }}>
            {loading ? "Загрузка..." : placeholder}
          </span>
        )}
        <ChevronDown
          size={13}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          style={{ color: "var(--t-low)", marginLeft: "auto" }}
        />
      </button>

      {/* Dropdown */}
      {open && (
        <div
          className="absolute z-50 mt-1 w-full min-w-[200px] max-h-52 overflow-y-auto py-1"
          style={{
            top: "100%", background: "var(--bg1)", border: "1px solid var(--line)",
            borderRadius: "var(--r-md)", boxShadow: "var(--shadow-pop)", maxWidth: "100%",
          }}
        >
          {options.length === 0 ? (
            <p className="px-3 py-2 text-xs" style={{ color: "var(--t-faint)" }}>Нет доступных опций</p>
          ) : (
            options.map(opt => {
              const checked = selected.includes(opt.value);
              return (
                <label
                  key={opt.value}
                  className="navitem select-none"
                  style={{ cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(opt.value)}
                    className="w-3.5 h-3.5 rounded"
                    style={{ accentColor: "var(--accent)", flex: "none" }}
                  />
                  <span className="text-sm trunc" style={{ color: checked ? "var(--accent-hi)" : "var(--t-mid)" }}>
                    {opt.label}
                  </span>
                </label>
              );
            })
          )}
        </div>
      )}

      {error && <p className="errmsg">{error}</p>}
    </div>
  );
}
