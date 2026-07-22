import { useState } from "react";
import { usePanels, type PanelInfo } from "./PanelPicker";

/**
 * The account's Remnawave panel registry: list, mark one as main, add, delete.
 *
 * Extracted from `Settings.tsx` (Wave-5 Plan K) so «Установка» can show the SAME
 * registry rather than growing a second one. Two independent lists over one
 * `active_panel_id` would inevitably drift in behaviour, and «сделать главной»
 * has to mean the same thing on both screens — it is literally the same field.
 *
 * `onChange` lets the settings form reload the panel it edits after an activate
 * or delete.
 */
export function PanelRegistry({ onChange, addLabel = "+ Панель", hint, prefill }: {
  onChange?: () => void;
  addLabel?: string;
  hint?: string;
  /** Values for a new entry — «Установка» prefills the deployed panel's URL. */
  prefill?: { name?: string; panel_url?: string };
}) {
  const { panels, activeId, reload } = usePanels();
  const [busy, setBusy] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const after = () => { reload(); onChange?.(); };

  const call = async (url: string, init?: RequestInit) => {
    setBusy(true);
    try { await fetch(url, init); after(); }
    finally { setBusy(false); }
  };

  const activate = (id: string) => call(`/api/settings/remnawave/panels/${id}/activate`, { method: "POST" });
  const del = (id: string) => { setConfirmId(null); return call(`/api/settings/remnawave/panels/${id}`, { method: "DELETE" }); };
  const add = () => call("/api/settings/remnawave/panels", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: prefill?.name || "Новая панель",
      panel_url: prefill?.panel_url || "",
      api_token: "",
    }),
  });

  return (
    <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center" }}>
        <span className="micro">Панели Remnawave</span>
        <button type="button" className="btn btn-sm" style={{ marginLeft: "auto" }}
          onClick={add} disabled={busy}>{addLabel}</button>
      </div>

      {panels.length === 0 && (
        <p className="hint">Панелей пока нет. Добавьте запись и заполните URL и токен в «Настройках».</p>
      )}

      {panels.map((p: PanelInfo) => (
        <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ flex: 1, fontSize: 13, color: "var(--t-hi)" }}>{p.name || p.panel_url || "—"}</span>
          {p.id === activeId
            ? <span className="chip ok" style={{ fontSize: 10 }}>главная</span>
            : <button type="button" className="btn btn-sm" disabled={busy}
                onClick={() => activate(p.id)}>Сделать главной</button>}
          {confirmId === p.id ? (
            <button type="button" className="btn btn-sm danger" disabled={busy}
              onClick={() => del(p.id)}>Точно удалить?</button>
          ) : (
            <button type="button" className="btn btn-sm" disabled={busy || panels.length === 1}
              onClick={() => setConfirmId(p.id)}
              title={panels.length === 1 ? "Последнюю панель удалить нельзя" : undefined}>Удалить</button>
          )}
        </div>
      ))}

      {/* Deleting the ACTIVE panel leaves a dangling active_panel_id, which the
          AppSettings validator resolves to the first entry. Working, but not
          obvious — so say it out loud instead of changing the fallback. */}
      {panels.length > 1 && activeId && confirmId === activeId && (
        <p className="hint">Удаляется главная панель — главной станет «{
          (panels.find(p => p.id !== activeId)?.name) || "первая в списке"
        }».</p>
      )}

      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}
