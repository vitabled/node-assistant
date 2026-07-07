import { useState, type ReactNode } from "react";
import { Settings2 } from "lucide-react";

// A gear button that toggles a small popover holding a widget's settings
// (a time-window select for the stats widgets, a checker_id select for the
// xray widgets). Reusable — the caller passes the control(s) as children.
export function WidgetSettings({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <button
        onClick={() => setOpen(v => !v)}
        title="Настройки виджета"
        style={{
          display: "grid", placeItems: "center", width: 26, height: 26,
          borderRadius: "var(--r-sm)", color: open ? "var(--accent)" : "var(--t-low)",
          background: open ? "var(--accent-dim)" : "transparent", border: "none", cursor: "pointer",
        }}
      >
        <Settings2 size={14} />
      </button>
      {open && (
        <>
          <div style={{ position: "fixed", inset: 0, zIndex: 20 }} onClick={() => setOpen(false)} />
          <div style={{
            position: "absolute", right: 0, top: "112%", zIndex: 21, minWidth: 172,
            background: "var(--bg1)", border: "1px solid var(--line-soft)",
            borderRadius: "var(--r-md)", boxShadow: "var(--shadow-pop)", padding: 10,
            display: "flex", flexDirection: "column", gap: 8,
          }}>
            {children}
          </div>
        </>
      )}
    </div>
  );
}
