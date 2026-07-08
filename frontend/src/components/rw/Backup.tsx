import { useState, useCallback, useEffect, useRef } from "react";
import {
  Database, Server, RefreshCw, Loader2, X, CheckCircle2, XCircle, Save,
  Play, RotateCcw, ShieldAlert, AlertTriangle, Clock, HardDrive, Eye, EyeOff,
} from "lucide-react";
import { TerminalOutput } from "../TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";
import { toast } from "../infra/Toast";
import { panelJobsKey } from "../../auth/store";
import type { PanelJobSummary } from "./PanelDashboard";

// Ф9 — Remnawave «Резервное копирование»: a distillium-wrapper dashboard. Pick a
// deployed PANEL (from panel_jobs_<id>), probe its backup state, configure the
// upload method + schedule + retention, and run backup / DESTRUCTIVE restore
// (double-confirm) — all as streamed ops (useTaskStream, like OpStreamModal).
// Upload secrets go into /opt/rw-backup-restore/config.env on the TARGET server
// (chmod 600) — never persisted here. Creds are per-request from the saved form.

type UploadMethod = "telegram" | "s3" | "google_drive" | "local";

interface BackupItem { name: string; size: number; mtime: number }
interface BackupState {
  installed: boolean;
  cronConfigured: boolean;
  configured: boolean;
  backups: BackupItem[];
  lastBackup: BackupItem | null;
}

interface SetupForm {
  upload_method: UploadMethod;
  bot_token: string;
  chat_id: string;
  s3_access_key: string;
  s3_secret_key: string;
  s3_bucket: string;
  s3_endpoint: string;
  s3_region: string;
  gd_token: string;
  gd_folder_id: string;
  cron_preset: string;   // one of CRON_PRESETS keys, or "custom"
  cron_custom: string;
  retain_days: string;
}

const FORM_DEFAULT: SetupForm = {
  upload_method: "local",
  bot_token: "", chat_id: "",
  s3_access_key: "", s3_secret_key: "", s3_bucket: "", s3_endpoint: "", s3_region: "",
  gd_token: "", gd_folder_id: "",
  cron_preset: "0 3 * * *", cron_custom: "", retain_days: "7",
};

const CRON_PRESETS: { id: string; label: string }[] = [
  { id: "0 3 * * *", label: "Ежедневно в 03:00" },
  { id: "0 3 * * 0", label: "Еженедельно (Вс 03:00)" },
  { id: "0 3 1 * *", label: "Ежемесячно (1-го, 03:00)" },
  { id: "", label: "Без расписания (только вручную)" },
  { id: "custom", label: "Своё расписание (cron)" },
];

function loadPanels(): PanelJobSummary[] {
  try {
    const all: PanelJobSummary[] = JSON.parse(localStorage.getItem(panelJobsKey()) ?? "[]");
    // Backup targets the Remnawave PANEL server (Postgres + /opt/remnawave). A
    // subpage-only install has no panel DB → exclude. Only successful installs.
    return all.filter(j => j.target !== "subpage" && j.finalStatus === "success");
  } catch { return []; }
}

function fmtBytes(b: number): string {
  const mb = b / 1048576;
  if (mb >= 1024) return `${(mb / 1024).toFixed(2)} ГБ`;
  return `${mb.toFixed(1)} МБ`;
}
function fmtWhen(mtime: number): string {
  if (!mtime) return "—";
  return new Date(mtime * 1000).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function Backup() {
  const [panels] = useState<PanelJobSummary[]>(loadPanels);
  const [selId, setSelId] = useState<string>(() => loadPanels()[0]?.id ?? "");
  const selected = panels.find(p => p.id === selId) ?? null;

  if (panels.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-6 py-6 ni-pagebody">
          <Head />
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <Database size={38} className="mb-4" style={{ color: "var(--t-faint)" }} />
            <p className="text-sm mb-1" style={{ color: "var(--t-low)" }}>Нет установленных панелей</p>
            <p className="text-xs" style={{ color: "var(--t-faint)" }}>
              Сначала установите панель Remnawave в разделе «Установка» — резервное копирование настраивается для неё.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6 ni-pagebody flex flex-col gap-5">
        <Head />

        {/* Panel selector */}
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>Панель</label>
          <select value={selId} onChange={e => setSelId(e.target.value)} className="selectbox transition-colors max-w-md">
            {panels.map(p => (
              <option key={p.id} value={p.id}>{p.savedForm.panel_domain || p.savedForm.ip} · {p.savedForm.ip}</option>
            ))}
          </select>
        </div>

        {selected && <PanelBackup key={selected.id} job={selected} />}
      </div>
    </div>
  );
}

function Head() {
  return (
    <div className="ni-pagehead">
      <h1 className="text-base font-semibold text-[var(--t-hi)]">Резервное копирование</h1>
      <p className="text-xs text-[var(--t-low)] mt-0.5">
        Дампы PostgreSQL + /opt/remnawave, аплоад в Telegram/S3/Google Drive, расписание по cron
      </p>
    </div>
  );
}

// ── Per-panel backup panel ─────────────────────────────────────
function PanelBackup({ job }: { job: PanelJobSummary }) {
  const p = job.savedForm;
  const creds = { ip: p.ip, ssh_user: p.ssh_user, ssh_password: p.ssh_password, ssh_port: p.ssh_port };

  const [status, setStatus] = useState<BackupState | null>(null);
  const [probe, setProbe] = useState<"loading" | "ok" | "offline">("loading");
  const [form, setForm] = useState<SetupForm>(FORM_DEFAULT);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);

  // ── Op stream (setup / run / restore) — one at a time ──
  const [opTaskId, setOpTaskId] = useState<string | null>(null);
  const [opLogs, setOpLogs] = useState<string[]>([]);
  const [opStatus, setOpStatus] = useState<TaskStatus>("pending");
  const [opTitle, setOpTitle] = useState("");
  const [opSubmitting, setOpSubmitting] = useState(false);
  const opAddLog = useCallback((line: string) => setOpLogs(l => [...l, line]), []);
  const opOnStatus = useCallback((f: StatusFrame) => setOpStatus(f.status), []);
  useTaskStream({ taskId: opTaskId, onLog: opAddLog, onStatus: opOnStatus });
  const opBusy = opSubmitting || (opStatus === "running" && !!opTaskId);

  // Refetch status when an op finishes successfully.
  const prevOp = useRef<TaskStatus>("pending");
  useEffect(() => {
    if (prevOp.current === "running" && opStatus === "success") fetchStatus();
    prevOp.current = opStatus;
  }, [opStatus]); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchStatus = useCallback(async () => {
    setProbe("loading");
    try {
      const res = await fetch("/api/backup/status", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(creds),
      });
      if (!aliveRef.current) return;
      if (!res.ok) { setProbe("offline"); return; }
      setStatus(await res.json());
      setProbe("ok");
    } catch {
      if (aliveRef.current) setProbe("offline");
    }
  }, [p.ip, p.ssh_port, p.ssh_user, p.ssh_password]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { fetchStatus(); }, [fetchStatus]);

  const cronTimes = form.cron_preset === "custom" ? form.cron_custom.trim() : form.cron_preset;

  const validate = (): string | null => {
    if (form.upload_method === "telegram" && (!form.bot_token.trim() || !form.chat_id.trim()))
      return "Для Telegram укажите Bot Token и Chat ID";
    if (form.upload_method === "s3" && (!form.s3_access_key.trim() || !form.s3_secret_key.trim() || !form.s3_bucket.trim()))
      return "Для S3 укажите Access Key, Secret Key и Bucket";
    if (form.cron_preset === "custom" && form.cron_custom.trim() && !/^[0-9*,/ \t-]+$/.test(form.cron_custom.trim()))
      return "Недопустимое расписание cron";
    const rd = parseInt(form.retain_days, 10);
    if (isNaN(rd) || rd < 1 || rd > 365) return "Хранить: 1–365 дней";
    return null;
  };

  const startOp = async (url: string, body: object, title: string) => {
    setOpLogs([]); setOpStatus("running"); setOpTitle(title); setOpTaskId(null); setOpSubmitting(true);
    try {
      const res = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setOpLogs([`\x1b[31m[HTTP ${res.status}] ${typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail)}\x1b[0m`]);
        setOpStatus("failed");
        return;
      }
      const { task_id } = await res.json();
      setOpTaskId(task_id);
    } catch (e) {
      setOpLogs([`\x1b[31m${(e as Error).message}\x1b[0m`]);
      setOpStatus("failed");
    } finally {
      setOpSubmitting(false);
    }
  };

  const doSetup = () => {
    const err = validate();
    if (err) { setFormErr(err); return; }
    setFormErr(null);
    startOp("/api/backup/setup", {
      ...creds,
      upload_method: form.upload_method,
      bot_token: form.bot_token, chat_id: form.chat_id,
      s3_access_key: form.s3_access_key, s3_secret_key: form.s3_secret_key,
      s3_bucket: form.s3_bucket, s3_endpoint: form.s3_endpoint, s3_region: form.s3_region,
      gd_token: form.gd_token, gd_folder_id: form.gd_folder_id,
      cron_times: cronTimes, retain_days: parseInt(form.retain_days, 10) || 7,
    }, "Настройка резервного копирования");
  };

  const doRun = () => startOp("/api/backup/run", creds, "Резервное копирование");
  const doRestore = () => {
    setConfirmRestore(false);
    startOp("/api/backup/restore", { ...creds, confirm: true }, "Восстановление из бэкапа");
  };

  const set = <K extends keyof SetupForm>(k: K, v: SetupForm[K]) => setForm(f => ({ ...f, [k]: v }));

  if (probe === "offline") {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-center rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)]">
        <XCircle size={30} className="text-[var(--err)]" />
        <p className="text-sm text-[var(--t-low)]">Сервер {p.ip} недоступен по SSH</p>
        <button type="button" onClick={fetchStatus}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors">
          <RefreshCw size={12} /> Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {/* TLS warning */}
      <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border text-[12px]"
        style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
        <AlertTriangle size={14} className="shrink-0 mt-0.5" />
        <span>Бэкап включает базу данных и <code>/opt/remnawave</code>, но <b>НЕ содержит TLS-сертификаты</b> — их придётся выпустить заново при восстановлении на новом сервере.</span>
      </div>

      {/* Status card */}
      <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] px-4 py-3.5">
        <div className="flex items-center gap-2 mb-3">
          <Server size={13} className="text-[var(--t-low)]" />
          <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest flex-1">Состояние</span>
          <button type="button" onClick={fetchStatus} disabled={probe === "loading"}
            className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium border border-[var(--line)] bg-[var(--bg1)] text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-50">
            {probe === "loading" ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Обновить
          </button>
        </div>
        {probe === "loading" && !status ? (
          <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5"><Loader2 size={10} className="animate-spin" /> Проверка по SSH…</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <StatePill ok={!!status?.installed} label={status?.installed ? "Установлено" : "Не установлено"} sub="backup-restore.sh" />
            <StatePill ok={!!status?.cronConfigured} label={status?.cronConfigured ? "Расписание активно" : "Без расписания"} sub="host cron" />
            <StatePill ok={!!status?.configured} label={status?.configured ? "config.env есть" : "Не настроено"} sub="метод аплоада" />
          </div>
        )}

        {/* Backups list */}
        {status && status.backups.length > 0 && (
          <div className="mt-3 border-t border-[var(--line-soft)] pt-3">
            <div className="flex items-center gap-1.5 mb-2">
              <HardDrive size={12} className="text-[var(--t-low)]" />
              <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">Последние бэкапы ({status.backups.length})</span>
            </div>
            <div className="flex flex-col gap-1">
              {status.backups.slice(0, 8).map(b => (
                <div key={b.name} className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="text-[var(--t-mid)] truncate flex items-center gap-1.5"><Clock size={10} className="text-[var(--t-faint)] shrink-0" /> {fmtWhen(b.mtime)}</span>
                  <span className="text-[var(--t-faint)] tabular-nums shrink-0">{fmtBytes(b.size)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={doRun} disabled={opBusy || !status?.installed}
          title={status?.installed ? "" : "Сначала выполните «Сохранить и настроить»"}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
          <Play size={13} /> Забэкапить сейчас
        </button>
        {confirmRestore ? (
          <button type="button" onClick={doRestore} disabled={opBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium btn-danger border transition-colors disabled:opacity-40">
            <ShieldAlert size={13} /> Точно восстановить? (ДЕСТРУКТИВНО)
          </button>
        ) : (
          <button type="button" onClick={() => setConfirmRestore(true)} disabled={opBusy || !status?.installed || !status?.backups.length}
            title={!status?.backups.length ? "Нет доступных бэкапов" : ""}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            <RotateCcw size={13} /> Восстановить
          </button>
        )}
        {confirmRestore && (
          <button type="button" onClick={() => setConfirmRestore(false)} className="text-[11px] text-[var(--t-faint)] hover:text-[var(--t-low)] transition-colors">Отмена</button>
        )}
      </div>
      {confirmRestore && (
        <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border text-[12px] -mt-2"
          style={{ background: "var(--err-dim)", borderColor: "var(--err-line)", color: "var(--err)" }}>
          <ShieldAlert size={14} className="shrink-0 mt-0.5" />
          <span>Восстановление <b>ДЕСТРУКТИВНО</b>: том <code>remnawave-db-data</code> будет очищен и заменён данными из последнего бэкапа. Нажмите «Точно восстановить» для подтверждения.</span>
        </div>
      )}

      {/* Setup form */}
      <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] px-4 py-4 flex flex-col gap-3.5">
        <div className="flex items-center gap-2">
          <Save size={13} className="text-[var(--t-low)]" />
          <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">Настройка</span>
        </div>

        {/* Upload method */}
        <div className="seg accent">
          {([
            { id: "local" as UploadMethod, label: "Локально" },
            { id: "telegram" as UploadMethod, label: "Telegram" },
            { id: "s3" as UploadMethod, label: "S3" },
            { id: "google_drive" as UploadMethod, label: "Google Drive" },
          ]).map(m => (
            <button key={m.id} type="button" onClick={() => set("upload_method", m.id)}
              className={`flex-1 text-xs font-medium focus:outline-none ${form.upload_method === m.id ? "on" : ""}`}>{m.label}</button>
          ))}
        </div>

        {form.upload_method === "telegram" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Bot Token" value={form.bot_token} onChange={v => set("bot_token", v)} secret placeholder="123456:ABC-DEF..." />
            <Field label="Chat ID" value={form.chat_id} onChange={v => set("chat_id", v)} placeholder="-100123456789" />
          </div>
        )}
        {form.upload_method === "s3" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Access Key" value={form.s3_access_key} onChange={v => set("s3_access_key", v)} secret />
            <Field label="Secret Key" value={form.s3_secret_key} onChange={v => set("s3_secret_key", v)} secret />
            <Field label="Bucket" value={form.s3_bucket} onChange={v => set("s3_bucket", v)} placeholder="my-backups" />
            <Field label="Endpoint" value={form.s3_endpoint} onChange={v => set("s3_endpoint", v)} placeholder="https://s3.example.com" />
            <Field label="Region" value={form.s3_region} onChange={v => set("s3_region", v)} placeholder="us-east-1" />
          </div>
        )}
        {form.upload_method === "google_drive" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="rclone token" value={form.gd_token} onChange={v => set("gd_token", v)} secret hint="JSON токена rclone" />
            <Field label="Folder ID" value={form.gd_folder_id} onChange={v => set("gd_folder_id", v)} />
          </div>
        )}
        {form.upload_method === "local" && (
          <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>Бэкапы хранятся только на сервере панели (без выгрузки во внешнее хранилище).</p>
        )}

        {/* Schedule + retention */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>Расписание</label>
            <select value={form.cron_preset} onChange={e => set("cron_preset", e.target.value)} className="selectbox transition-colors">
              {CRON_PRESETS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
          <Field label="Хранить, дней" value={form.retain_days} onChange={v => set("retain_days", v)} placeholder="7" />
        </div>
        {form.cron_preset === "custom" && (
          <Field label="cron-строка" value={form.cron_custom} onChange={v => set("cron_custom", v)} placeholder="0 3 * * *"
            hint="5 полей: минута час день месяц день_недели" />
        )}

        {formErr && <p className="errmsg">{formErr}</p>}

        <div className="flex items-center gap-2">
          <button type="button" onClick={doSetup} disabled={opBusy}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-md text-sm font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            {opBusy ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} Сохранить и настроить
          </button>
          <p className="text-[10px]" style={{ color: "var(--t-faint)" }}>Секреты пишутся в config.env на сервере (chmod 600), у нас не хранятся.</p>
        </div>
      </div>

      {/* Op-stream overlay */}
      {opTitle && (opSubmitting || opTaskId || opStatus === "failed") && (
        <OpStreamModal title={opTitle} logs={opLogs} status={opStatus}
          onClose={() => { setOpTitle(""); setOpTaskId(null); }} />
      )}
    </div>
  );
}

function StatePill({ ok, label, sub }: { ok: boolean; label: string; sub: string }) {
  return (
    <div className="rounded-lg border px-3 py-2.5 flex flex-col gap-0.5"
      style={{
        borderColor: ok ? "var(--ok-line)" : "var(--line)",
        background: ok ? "var(--ok-dim)" : "var(--bg1)",
      }}>
      <div className="flex items-center gap-1.5">
        {ok ? <CheckCircle2 size={13} style={{ color: "var(--ok)" }} /> : <XCircle size={13} style={{ color: "var(--t-faint)" }} />}
        <span className="text-xs font-medium" style={{ color: ok ? "var(--ok)" : "var(--t-mid)" }}>{label}</span>
      </div>
      <span className="text-[10px] pl-[19px]" style={{ color: "var(--t-faint)" }}>{sub}</span>
    </div>
  );
}

// ── local Field (mirrors PanelDeployForm's) ────────────────────
function Field({ label, value, onChange, secret, placeholder, hint }: {
  label: string; value: string; onChange: (v: string) => void; secret?: boolean; placeholder?: string; hint?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <div className={secret ? "relative" : undefined}>
        <input type={secret ? (show ? "text" : "password") : "text"} value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} autoComplete="off" spellCheck={false} className={`input transition-colors ${secret ? "pr-9" : ""}`} />
        {secret && (
          <button type="button" tabIndex={-1} onClick={() => setShow(v => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2.5 text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
            {show ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        )}
      </div>
      {hint && <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>{hint}</p>}
    </div>
  );
}

// ── Op-stream overlay (setup / run / restore progress) ─────────
function OpStreamModal({ title, logs, status, onClose }: {
  title: string; logs: string[]; status: TaskStatus; onClose: () => void;
}) {
  const done = status === "success" || status === "failed";
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget && done) onClose(); }}>
      <div className="w-full max-w-2xl rounded-xl overflow-hidden flex flex-col max-h-[85vh]"
        style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="sticky top-0 flex items-center gap-2 px-5 py-3.5" style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          {status === "running" ? <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent-hi)" }} />
            : status === "success" ? <CheckCircle2 size={14} style={{ color: "var(--ok)" }} />
            : <XCircle size={14} style={{ color: "var(--err)" }} />}
          <h2 className="text-sm font-semibold flex-1" style={{ color: "var(--t-hi)" }}>{title}</h2>
          <button onClick={onClose} disabled={!done} className="iconbtn disabled:opacity-40" title="Закрыть"><X size={15} /></button>
        </div>
        <div className="p-4 flex-1 min-h-0" style={{ minHeight: 240 }}>
          <TerminalOutput lines={logs} />
        </div>
      </div>
    </div>
  );
}
