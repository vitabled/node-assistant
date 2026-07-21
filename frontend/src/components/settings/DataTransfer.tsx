import { useState } from "react";
import { Download, Upload, Loader2, AlertTriangle } from "lucide-react";
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
    </div>
  );
}
