import { useEffect, useState } from "react";

export interface PanelInfo { id: string; name: string; panel_url: string }

/** Load the account's Remnawave panel registry. Shared by every screen that
 *  needs to pick a panel, so the list can't drift between them. */
export function usePanels(): { panels: PanelInfo[]; activeId: string; reload: () => void } {
  const [panels, setPanels] = useState<PanelInfo[]>([]);
  const [activeId, setActiveId] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let dead = false;
    fetch("/api/settings/remnawave/panels")
      .then(r => (r.ok ? r.json() : { panels: [], active_panel_id: "" }))
      .then(d => {
        if (dead) return;
        setPanels(Array.isArray(d.panels) ? d.panels : []);
        setActiveId(d.active_panel_id || "");
      })
      .catch(() => { /* keep whatever we had */ });
    return () => { dead = true; };
  }, [tick]);

  return { panels, activeId, reload: () => setTick(t => t + 1) };
}

/**
 * Controlled panel selector. `value === ""` means "the panel marked as main",
 * which is exactly what the backend does with an empty `panel_id` — so the
 * default choice needs no special-casing on either side.
 *
 * Deliberately controlled and stateless: `PromptPresets` used to do its own
 * GET-modify-POST and raced the form it sat in over one document (Wave 6).
 *
 * Renders nothing when there is at most one panel — a selector with a single
 * option is noise.
 */
export function PanelPicker({ value, onChange, panels, activeId, label = "Панель" }: {
  value: string;
  onChange: (id: string) => void;
  panels: PanelInfo[];
  activeId: string;
  label?: string;
}) {
  if (panels.length < 2) return null;
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span className="micro">{label}</span>
      <select className="selectbox" value={value} onChange={e => onChange(e.target.value)}>
        <option value="">По умолчанию</option>
        {panels.map(p => (
          <option key={p.id} value={p.id}>
            {(p.name || p.panel_url || p.id) + (p.id === activeId ? " · главная" : "")}
          </option>
        ))}
      </select>
    </label>
  );
}
