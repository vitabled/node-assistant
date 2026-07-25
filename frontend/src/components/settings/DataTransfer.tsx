import { useState, useEffect } from "react";
import { Download, Upload, Loader2, AlertTriangle, Send, Save } from "lucide-react";
import { toast } from "../infra/Toast";

// Wave-5 Plan L (slice 1) — export/import the account's node-assistant data.
export function DataTransfer() {
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [report, setReport] = useState<{ applied?: Record<string, number>; skipped?: string[] } | null>(null);

  const doExport = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.ok) throw new Error();
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "node-assistant-export.tar.gz"; a.click();
      URL.revokeObjectURL(url);
      toast("Экспорт скачан", "success");
    } catch { toast("Не удалось экспортировать", "error"); }
    finally { setBusy(false); }
  };

  const doImport = async (file: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("confirm", "true");
      const r = await fetch("/api/import", { method: "POST", body: fd });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "Ошибка импорта");
      setReport(data);
      toast("Импорт выполнен", "success");
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка импорта", "error"); }
    finally { setBusy(false); }
  };

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="card card-p flex flex-col gap-3">
        <span className="text-sm font-semibold text-[var(--t-hi)]">Экспорт данных node-assistant</span>
        <p className="hint">Архив (.tar.gz) с настройками, шаблонами, правилами, хостами, подписками и т.д. Секреты (токены/ключи) исключаются.</p>
        <button className="btn btn-primary" disabled={busy} onClick={doExport} style={{ alignSelf: "flex-start" }}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Экспортировать
        </button>
      </div>

      <div className="card card-p flex flex-col gap-3">
        <span className="text-sm font-semibold text-[var(--t-hi)]">Импорт</span>
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertTriangle size={14} className="shrink-0" /> Импорт перезаписывает соответствующие данные аккаунта. Учётные секции (токены панелей/ключи) не затрагиваются.
        </div>
        <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
          <input type="checkbox" checked={confirm} onChange={e => setConfirm(e.target.checked)} />
          Понимаю — перезаписать данные
        </label>
        <label className="btn" style={{ alignSelf: "flex-start", opacity: confirm && !busy ? 1 : 0.5, cursor: confirm && !busy ? "pointer" : "not-allowed" }}>
          <Upload size={14} /> Выбрать архив…
          <input type="file" accept=".gz,.tar.gz,application/gzip" style={{ display: "none" }} disabled={!confirm || busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) doImport(f); e.currentTarget.value = ""; }} />
        </label>
        {report && (
          <p className="hint">
            Применено: {Object.keys(report.applied || {}).join(", ") || "—"}
            {report.skipped?.length ? ` · пропущено: ${report.skipped.join(", ")}` : ""}
          </p>
        )}
      </div>

      <AutoBackupCard />
    </div>
  );
}

// ── Wave-8 §4 — scheduled auto-backup → Telegram ───────────────
interface AbCfg {
  enabled: boolean;
  interval_hours: number;
  include_secrets: boolean;
  chat_id: string;
  has_token: boolean;
  last_run: number;
  last_error: string;
}

function AutoBackupCard() {
  const [cfg, setCfg] = useState<AbCfg>({
    enabled: false, interval_hours: 24, include_secrets: false,
    chat_id: "", has_token: false, last_run: 0, last_error: "",
  });
  const [token, setToken] = useState("");   // write-only; blank = keep existing
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);

  const load = async () => {
    try {
      const d = await fetch("/api/settings/auto-backup").then(r => r.json());
      setCfg(c => ({ ...c, ...d }));
    } catch { /* keep defaults */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const r = await fetch("/api/settings/auto-backup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: cfg.enabled, interval_hours: cfg.interval_hours,
          include_secrets: cfg.include_secrets, chat_id: cfg.chat_id,
          bot_token: token,   // "" → backend keeps the stored token
        }),
      });
      if (!r.ok) throw new Error();
      setToken("");
      await load();
      toast("Настройки автобэкапа сохранены", "success");
    } catch { toast("Не удалось сохранить", "error"); }
    finally { setSaving(false); }
  };

  const sendNow = async () => {
    setSending(true);
    try {
      const r = await fetch("/api/settings/auto-backup/run", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "Ошибка отправки");
      toast("Бэкап отправлен в Telegram", "success");
      await load();
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка отправки", "error"); }
    finally { setSending(false); }
  };

  return (
    <div className="card card-p flex flex-col gap-3">
      <span className="text-sm font-semibold text-[var(--t-hi)]">Автобэкап → Telegram</span>
      <p className="hint">Периодическая отправка полного экспорта аккаунта в Telegram-чат через вашего бота.</p>

      <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
        <input type="checkbox" checked={cfg.enabled} onChange={e => setCfg(c => ({ ...c, enabled: e.target.checked }))} />
        Включить автобэкап
      </label>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1">
          <label className="label">Интервал (часов)</label>
          <input type="number" min={1} max={8760} value={cfg.interval_hours}
            onChange={e => setCfg(c => ({ ...c, interval_hours: parseInt(e.target.value) || 24 }))}
            className="input" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="label">Chat ID</label>
          <input value={cfg.chat_id} onChange={e => setCfg(c => ({ ...c, chat_id: e.target.value }))}
            placeholder="напр. 123456789" spellCheck={false} className="input" />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="label">Bot token</label>
        <input type="password" value={token} onChange={e => setToken(e.target.value)}
          placeholder={cfg.has_token ? "•••••••• (токен сохранён — оставьте пустым)" : "123456:AA…"}
          autoComplete="off" spellCheck={false} className="input" />
      </div>

      <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
        <input type="checkbox" checked={cfg.include_secrets}
          onChange={e => setCfg(c => ({ ...c, include_secrets: e.target.checked }))} />
        Включать секреты (токены/ключи)
      </label>
      {cfg.include_secrets && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertTriangle size={14} className="shrink-0" /> Секреты попадут в Telegram-чат в открытом виде — используйте только приватный чат.
        </div>
      )}

      {cfg.last_error
        ? <p className="hint text-[var(--err)]">Последняя ошибка: {cfg.last_error}</p>
        : cfg.last_run > 0 && <p className="hint">Последний бэкап: {new Date(cfg.last_run * 1000).toLocaleString()}</p>}

      <div className="flex items-center gap-2">
        <button className="btn btn-primary" disabled={saving} onClick={save}>
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Сохранить
        </button>
        <button className="btn" disabled={sending} onClick={sendNow} title="Отправить бэкап прямо сейчас">
          {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Отправить сейчас
        </button>
      </div>
    </div>
  );
}
