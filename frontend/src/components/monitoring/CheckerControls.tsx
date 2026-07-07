import { useState, useEffect } from "react";
import {
  SlidersHorizontal, ChevronDown, CheckCircle2, Loader2, Save, Download, XCircle,
} from "lucide-react";

// Local xray-checker container config + lifecycle controls. Lives in
// Settings → Мониторинг (Ф2 — moved here from the Dashboard).

interface XrayCheckerCfg {
  enabled: boolean;
  subscription_url: string;
  check_interval: number;
  check_method: string;
  metrics_port: number;
  image: string;
  poll_interval: number;
}
const XC_INIT: XrayCheckerCfg = {
  enabled: false, subscription_url: "", check_interval: 300, check_method: "ip",
  metrics_port: 2112, image: "kutovoys/xray-checker:latest", poll_interval: 60,
};

export function CheckerControls() {
  const [open,   setOpen]   = useState(true);
  const [cfg,    setCfg]    = useState<XrayCheckerCfg>(XC_INIT);
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);
  const [busy,   setBusy]   = useState<null | "update" | "stop">(null);
  const [msg,    setMsg]    = useState<{ ok: boolean; text: string; warn?: boolean } | null>(null);

  useEffect(() => {
    fetch("/api/settings").then(r => r.json())
      .then(d => { if (d.xray_checker) setCfg({ ...XC_INIT, ...d.xray_checker }); })
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const res = await fetch("/api/settings/xray-checker", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg({ ok: false, text: String(d.detail ?? res.statusText) });
      } else if (d.warning) {
        // Settings saved, but the container couldn't start (e.g. no Docker).
        setMsg({ ok: false, warn: true, text: String(d.warning) });
        setSaved(true); setTimeout(() => setSaved(false), 2000);
      } else {
        setMsg(null);
        setSaved(true); setTimeout(() => setSaved(false), 2000);
      }
    } catch (e) { setMsg({ ok: false, text: String(e) }); }
    setSaving(false);
  };

  const action = async (kind: "update" | "stop") => {
    setBusy(kind); setMsg(null);
    const path = kind === "update" ? "/api/checker/update" : "/api/checker/stop";
    try {
      const res = await fetch(path, { method: "POST" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) setMsg({ ok: false, text: String(d.detail ?? res.statusText) });
      else setMsg({ ok: true, text:
        kind === "update" ? "Xray-Checker обновлён и перезапущен." : "Контейнер остановлен." });
    } catch (e) { setMsg({ ok: false, text: String(e) }); }
    setBusy(null);
  };

  return (
    <div className="card card-p mb-4">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-2 bg-transparent border-none cursor-pointer p-0">
        <span className="micro flex items-center gap-2"><SlidersHorizontal size={13} /> Локальный чекер</span>
        <ChevronDown size={14} className="text-[var(--t-faint)] transition-transform"
          style={{ transform: open ? "rotate(180deg)" : "none" }} />
      </button>

      {open && (
        <div className="mt-4 flex flex-col gap-4">
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <button type="button" role="switch" aria-checked={cfg.enabled}
              onClick={() => setCfg(c => ({ ...c, enabled: !c.enabled }))}
              className={`switch ${cfg.enabled ? "on" : ""}`} />
            <span className="text-sm text-[var(--t-mid)]">Включить мониторинг</span>
          </label>

          <div className="flex flex-col gap-1">
            <label className="label">Прямая подписка (без агрегатора)</label>
            <input className="input" value={cfg.subscription_url}
              onChange={e => setCfg(c => ({ ...c, subscription_url: e.target.value }))}
              placeholder="https://panel.example.com/sub/…" />
            <p className="hint">Для одиночной подписки; при использовании списка подписок оставьте пустым</p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="flex flex-col gap-1">
              <label className="label">Интервал проверки (с)</label>
              <input className="input" type="number" value={cfg.check_interval}
                onChange={e => setCfg(c => ({ ...c, check_interval: parseInt(e.target.value) || 300 }))} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="label">Метод</label>
              <select className="selectbox" value={cfg.check_method}
                onChange={e => setCfg(c => ({ ...c, check_method: e.target.value }))}>
                <option value="ip">ip</option>
                <option value="status">status</option>
                <option value="download">download</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="label">Порт метрик</label>
              <input className="input" type="number" value={cfg.metrics_port}
                onChange={e => setCfg(c => ({ ...c, metrics_port: parseInt(e.target.value) || 2112 }))} />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button onClick={save} disabled={saving} className="btn btn-primary">
              {saved ? <><CheckCircle2 size={13} /> Сохранено</>
                : saving ? <><Loader2 size={13} className="animate-spin" /> Сохранение…</>
                : <><Save size={13} /> Сохранить</>}
            </button>
            <button onClick={() => action("update")} disabled={busy !== null} className="btn btn-soft">
              {busy === "update" ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              Обновить
            </button>
            <button onClick={() => action("stop")} disabled={busy !== null} className="btn btn-ghost">
              Остановить
            </button>
          </div>

          {msg && (
            <span className="text-xs flex items-center gap-1.5"
              style={{ color: msg.ok ? "var(--ok)" : msg.warn ? "var(--warn)" : "var(--err)" }}>
              {msg.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {msg.text}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
