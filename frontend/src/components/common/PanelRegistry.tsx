import { useEffect, useState } from "react";
import { Loader2, PanelsTopLeft, Pencil, Server, X } from "lucide-react";
import { usePanels, type PanelInfo } from "./PanelPicker";
import { MultiSelect, type SelectOption } from "../MultiSelect";

/**
 * The account's Remnawave panel registry: list, mark one as main, add, edit, delete.
 *
 * Extracted from `Settings.tsx` (Wave-5 Plan K) so «Установка» can show the SAME
 * registry rather than growing a second one. Two independent lists over one
 * `active_panel_id` would inevitably drift in behaviour, and «сделать главной»
 * has to mean the same thing on both screens — it is literally the same field.
 *
 * `onChange` lets the settings form reload the panel it edits after an activate
 * or delete.
 */

interface PanelFull {
  id: string;
  name: string;
  kind?: string;
  panel_url: string;
  api_token?: string;
  default_internal_squad_ids?: string[];
  default_external_squad_ids?: string[];
}

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
  const [editId, setEditId] = useState<string | null>(null);

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
    <div className="card card-p" style={{
      // Отдельный виджет, а не «ещё один ряд на странице»: усиленная рамка и
      // ряды-плитки на --raised (Wave-4: на светлой теме блок сливался с фоном).
      border: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ color: "var(--accent-hi)", display: "flex" }}><PanelsTopLeft size={15} /></span>
        <span className="micro" style={{ color: "var(--t-hi)" }}>Панели Remnawave</span>
        <button type="button" className="btn btn-sm" style={{ marginLeft: "auto" }}
          onClick={add} disabled={busy}>{addLabel}</button>
      </div>

      {panels.length === 0 && (
        <p className="hint">Панелей пока нет. Добавьте запись и заполните URL и токен в «Настройках».</p>
      )}

      {panels.map((p: PanelInfo) => (
        <div key={p.id} style={{
          display: "flex", alignItems: "center", gap: 8,
          background: "var(--raised)", border: "1px solid var(--line-soft)",
          borderRadius: "var(--r-md)", padding: "9px 12px",
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="trunc" style={{ fontSize: 13, color: "var(--t-hi)", fontWeight: 600 }}>
              {p.name || "—"}
            </div>
            {p.panel_url && (
              <div className="trunc" style={{ fontSize: 11, color: "var(--t-low)", marginTop: 1 }}>{p.panel_url}</div>
            )}
          </div>
          {p.id === activeId
            ? <span className="chip ok" style={{ fontSize: 10 }}>главная</span>
            : <button type="button" className="btn btn-sm" disabled={busy}
                onClick={() => activate(p.id)}>Сделать главной</button>}
          <button type="button" className="btn btn-sm" disabled={busy}
            title="Изменить название, URL, токен и сквады"
            onClick={() => setEditId(p.id)}>
            <Pencil size={12} /> Изменить
          </button>
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

      {editId && (
        <PanelEditModal id={editId} onClose={() => setEditId(null)}
          onSaved={() => { setEditId(null); after(); }} />
      )}
    </div>
  );
}

// ── Edit modal ─────────────────────────────────────────────────
// Полная запись дочитывается из GET /panels (usePanels отдаёт урезанный
// PanelInfo). api_token возвращается тем же GET'ом — показываем его в
// password-поле и отправляем обратно как есть, пока оператор его не тронул.
function PanelEditModal({ id, onClose, onSaved }: {
  id: string; onClose: () => void; onSaved: () => void;
}) {
  const [loaded, setLoaded] = useState<PanelFull | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [intSq, setIntSq] = useState<string[]>([]);
  const [extSq, setExtSq] = useState<string[]>([]);
  const [intOpts, setIntOpts] = useState<SelectOption[]>([]);
  const [extOpts, setExtOpts] = useState<SelectOption[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let dead = false;
    (async () => {
      const r = await fetch("/api/settings/remnawave/panels");
      const d = await r.json().catch(() => ({}));
      const p = ((d as { panels?: PanelFull[] }).panels || []).find(x => x.id === id);
      if (dead) return;
      if (!p) { setErr("Панель не найдена"); return; }
      setLoaded(p);
      setName(p.name || "");
      setUrl(p.panel_url || "");
      setToken(p.api_token || "");
      setIntSq(p.default_internal_squad_ids || []);
      setExtSq(p.default_external_squad_ids || []);
    })();
    const opts = (arr: unknown): SelectOption[] =>
      (Array.isArray(arr) ? arr : [])
        .map((s) => ({ value: String((s as { uuid?: string }).uuid ?? ""), label: String((s as { name?: string }).name ?? "") }))
        .filter(o => o.value);
    fetch("/api/remnawave/squads/internal").then(r => r.json()).then(a => !dead && setIntOpts(opts(a))).catch(() => {});
    fetch("/api/remnawave/squads/external").then(r => r.json()).then(a => !dead && setExtOpts(opts(a))).catch(() => {});
    return () => { dead = true; };
  }, [id]);

  const save = async () => {
    setBusy(true); setErr("");
    const res = await fetch(`/api/settings/remnawave/panels/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name.trim() || "Основная",
        kind: loaded?.kind || "custom",
        panel_url: url.trim(),
        api_token: token,
        default_internal_squad_ids: intSq,
        default_external_squad_ids: extSq,
      }),
    });
    setBusy(false);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`);
      return;
    }
    onSaved();
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg">
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <Server size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>Изменить панель</h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          {!loaded && !err && (
            <p style={{ fontSize: 12, color: "var(--t-low)" }}>
              <Loader2 size={13} className="spin" style={{ display: "inline", marginRight: 6 }} />Загрузка…
            </p>
          )}
          {loaded && (
            <>
              <div>
                <label className="label">Название</label>
                <input className="input" value={name} onChange={e => setName(e.target.value)}
                  placeholder="Основная" />
              </div>
              <div>
                <label className="label">URL панели</label>
                <input className="input" value={url} onChange={e => setUrl(e.target.value)}
                  placeholder="https://panel.example.com" />
              </div>
              <div>
                <label className="label">API-токен</label>
                <input className="input" type="password" value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder="Токен панели" autoComplete="off" />
                <p className="hint">Заполнен текущим токеном — измените, только если перевыпускаете его в панели.</p>
              </div>
              <MultiSelect label="Внутренние сквады по умолчанию" selected={intSq}
                onChange={setIntSq} options={intOpts} />
              <MultiSelect label="Внешние сквады по умолчанию" selected={extSq}
                onChange={setExtSq} options={extOpts} />
            </>
          )}
          {err && <p className="errmsg">{err}</p>}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3.5"
          style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button type="button" className="btn btn-primary" onClick={save}
            disabled={busy || !loaded}>
            {busy ? <Loader2 size={13} className="spin" /> : null} Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
