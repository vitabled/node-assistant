import { useState, useCallback, useEffect, useRef } from "react";
import {
  X, RefreshCw, Trash2, ShieldAlert, Wrench, Server, Loader2,
  CheckCircle2, XCircle, Save, AlertTriangle, BarChart3, Boxes,
  ShieldCheck, Network, ArrowDownToLine, ArrowUpFromLine, Sigma,
} from "lucide-react";
import { TerminalOutput } from "../TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";
import { toast } from "../infra/Toast";
import type { PanelJobSummary } from "./PanelDashboard";
import type { PanelDeployPayload } from "./PanelDeployForm";

// Ф7 — panel/subscription management modal, opened by clicking a PanelWidget
// subframe. Two tabs mirroring the DeployCard reference:
//   • «Компоненты» — reinstall / uninstall each panel component (streamed op via a
//     second useTaskStream, like DeployCard.ManageBlock/OpStreamModal) + a
//     «Данные сервера» editor that patches ONLY the local panel_jobs_<id> record.
//   • «Статистика» — traffic (vnstat) + fail2ban bans, reusing POST /api/stats/node
//     with the record's own SSH creds (per-request, never persisted).

type PanelAction = "reinstall" | "uninstall";

// Manageable components for a panel install, derived from its saved form (mirrors
// panel_deploy.py's Component set). `docker` is reinstall-only — tearing down the
// engine under a running panel is destructive (the backend rejects it).
export function panelManageableComponents(
  p: PanelDeployPayload,
): { id: string; label: string; removable: boolean }[] {
  const wantPanel = p.target !== "subpage";
  const wantSub = p.target !== "panel";
  return [
    ...(wantPanel ? [{ id: "panel", label: "Панель Remnawave", removable: true }] : []),
    ...(wantSub ? [{ id: "subpage", label: "Страница подписок", removable: true }] : []),
    { id: "docker", label: "Docker", removable: false },
    ...(p.install_test_tools !== false
      ? [{ id: "test_tools", label: "Тест-инструменты", removable: true }]
      : []),
    { id: "reverse_proxy", label: "Reverse-proxy", removable: true },
  ];
}

const IPv4 = /^(\d{1,3}\.){3}\d{1,3}$/;
const DOMAIN =
  /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;
const validIp = (v: string) => IPv4.test(v) && v.split(".").every(o => parseInt(o, 10) <= 255);

// ── Security / traffic stats types (mirror /api/stats/node) ──
interface SecurityStats { fail2banActive: number; fail2banTotal: number; trafficGuardActive: number }
interface TrafficBucket { rx: number; tx: number; total: number }
interface TrafficStats { today: TrafficBucket; week: TrafficBucket; month: TrafficBucket }
type TrafficPeriod = "today" | "week" | "month";

interface Props {
  job:        PanelJobSummary;
  onClose:    () => void;
  onEditJob:  (job: PanelJobSummary) => void;   // patches panel_jobs_<id> (client-only)
}

type Tab = "components" | "stats";

export function PanelManageModal({ job, onClose, onEditJob }: Props) {
  const p = job.savedForm;
  const [tab, setTab] = useState<Tab>("components");

  return (
    <>
      <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="modal" style={{ maxWidth: "44rem" }}>
          {/* Header + tabs */}
          <div className="shrink-0 flex items-center gap-3 px-5 py-3.5 border-b border-[var(--line-soft)]">
            <Wrench size={15} className="text-[var(--t-low)] shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-[var(--t-hi)] truncate">Управление установкой</p>
              <p className="text-xs text-[var(--t-low)] truncate">{p.panel_domain || p.sub_domain} · {p.ip}</p>
            </div>
            <button onClick={onClose} className="iconbtn" title="Закрыть"><X size={15} /></button>
          </div>

          <div className="shrink-0 px-5 pt-3">
            <div className="seg accent">
              <button type="button" onClick={() => setTab("components")}
                className={`flex-1 text-sm font-medium focus:outline-none flex items-center justify-center gap-1.5 ${tab === "components" ? "on" : ""}`}>
                <Boxes size={13} /> Компоненты
              </button>
              <button type="button" onClick={() => setTab("stats")}
                className={`flex-1 text-sm font-medium focus:outline-none flex items-center justify-center gap-1.5 ${tab === "stats" ? "on" : ""}`}>
                <BarChart3 size={13} /> Статистика
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
            {tab === "components"
              ? <ComponentsTab job={job} onEditJob={onEditJob} />
              : <StatsTab job={job} />}
          </div>
        </div>
      </div>
    </>
  );
}

// ── «Компоненты» tab ──────────────────────────────────────────
function ComponentsTab({ job, onEditJob }: { job: PanelJobSummary; onEditJob: Props["onEditJob"] }) {
  const p = job.savedForm;
  const comps = panelManageableComponents(p);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  // ── Op stream (reinstall / uninstall) — one op at a time ──
  const [opTaskId,     setOpTaskId]     = useState<string | null>(null);
  const [opLogs,       setOpLogs]       = useState<string[]>([]);
  const [opStatus,     setOpStatus]     = useState<TaskStatus>("pending");
  const [opTitle,      setOpTitle]      = useState("");
  const [opSubmitting, setOpSubmitting] = useState(false);
  const opAddLog   = useCallback((line: string) => setOpLogs(l => [...l, line]), []);
  const opOnStatus = useCallback((frame: StatusFrame) => setOpStatus(frame.status), []);
  useTaskStream({ taskId: opTaskId, onLog: opAddLog, onStatus: opOnStatus });

  const opBusy = opSubmitting || (opStatus === "running" && !!opTaskId);

  const runOp = useCallback(async (component: string, action: PanelAction, title: string) => {
    setOpLogs([]); setOpStatus("running"); setOpTitle(title); setOpTaskId(null);
    setOpSubmitting(true);
    try {
      const res = await fetch("/api/panel/step", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...p, component, action }),
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
  }, [p]);

  return (
    <div className="flex flex-col gap-4">
      {/* Component list */}
      <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <Wrench size={12} className="text-[var(--t-low)]" />
          <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
            Управление компонентами
          </span>
        </div>
        <div className="flex flex-col gap-1">
          {comps.map(c => (
            <div key={c.id} className="flex items-center gap-2 py-0.5">
              <span className="text-xs text-[var(--t-mid)] flex-1 truncate">{c.label}</span>
              <button title="Переустановить" disabled={opBusy}
                onClick={() => runOp(c.id, "reinstall", `Переустановка: ${c.label}`)}
                className="p-1 rounded text-[var(--t-faint)] hover:text-[var(--accent-hi)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-40">
                <RefreshCw size={12} />
              </button>
              {!c.removable ? (
                <span className="w-[26px]" title="Удаление недоступно (деструктивно)" />
              ) : confirmDel === c.id ? (
                <button title="Подтвердить удаление" disabled={opBusy}
                  onClick={() => { setConfirmDel(null); runOp(c.id, "uninstall", `Удаление: ${c.label}`); }}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium btn-danger border disabled:opacity-40">
                  <ShieldAlert size={10} /> Точно?
                </button>
              ) : (
                <button title="Удалить" disabled={opBusy}
                  onClick={() => setConfirmDel(c.id)}
                  className="p-1 rounded text-[var(--t-faint)] hover:text-[var(--err)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-40">
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Server-data editor (localStorage only) */}
      <ServerDataBlock job={job} onEditJob={onEditJob} />

      {/* Op-stream overlay */}
      {opTitle && (opSubmitting || opTaskId || opStatus === "failed") && (
        <OpStreamModal
          title={opTitle}
          logs={opLogs}
          status={opStatus}
          onClose={() => { setOpTitle(""); setOpTaskId(null); }}
        />
      )}
    </div>
  );
}

// ── «Данные сервера» editor (patches panel_jobs_<id> only) ────
function ServerDataBlock({ job, onEditJob }: { job: PanelJobSummary; onEditJob: Props["onEditJob"] }) {
  const p = job.savedForm;
  const wantPanel = p.target !== "subpage";
  const wantSub = p.target !== "panel";

  const [ip,       setIp]       = useState(p.ip);
  const [sshUser,  setSshUser]  = useState(p.ssh_user);
  const [sshPass,  setSshPass]  = useState(p.ssh_password);
  const [sshPort,  setSshPort]  = useState(String(p.ssh_port));
  const [panelDom, setPanelDom] = useState(p.panel_domain);
  const [subDom,   setSubDom]   = useState(p.sub_domain);
  const [err, setErr] = useState<string | null>(null);

  const dirty =
    ip !== p.ip || sshUser !== p.ssh_user || sshPass !== p.ssh_password ||
    sshPort !== String(p.ssh_port) ||
    (wantPanel && panelDom !== p.panel_domain) || (wantSub && subDom !== p.sub_domain);

  // Either domain change needs an SSL re-issue: both go through acme.sh /
  // reverse-proxy, so reinstalling «Reverse-proxy» is required after a change.
  const domainChanged =
    (wantPanel && panelDom.trim() !== p.panel_domain) ||
    (wantSub && subDom.trim() !== p.sub_domain);

  const save = () => {
    if (!validIp(ip.trim())) { setErr("Неверный IPv4"); return; }
    const port = parseInt(sshPort, 10);
    if (isNaN(port) || port < 1 || port > 65535) { setErr("SSH порт: 1–65535"); return; }
    if (!sshPass) { setErr("SSH пароль обязателен"); return; }
    if (wantPanel && (!panelDom.trim() || !DOMAIN.test(panelDom.trim()))) { setErr("Неверный домен панели"); return; }
    if (wantSub && (!subDom.trim() || !DOMAIN.test(subDom.trim()))) { setErr("Неверный домен подписки"); return; }
    setErr(null);
    const updated: PanelJobSummary = {
      ...job,
      savedForm: {
        ...p,
        ip: ip.trim(),
        ssh_user: sshUser.trim() || "root",
        ssh_password: sshPass,
        ssh_port: port,
        panel_domain: wantPanel ? panelDom.trim() : p.panel_domain,
        sub_domain: wantSub ? subDom.trim() : p.sub_domain,
      },
    };
    onEditJob(updated);
    toast("Данные сервера обновлены (только на этом устройстве)", "info");
  };

  return (
    <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-3">
      <div className="flex items-center gap-1.5 mb-3">
        <Server size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Данные сервера
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        <MiniField label="IP-адрес" value={ip} onChange={setIp} placeholder="1.2.3.4" />
        <MiniField label="SSH логин" value={sshUser} onChange={setSshUser} placeholder="root" />
        <MiniField label="SSH пароль" value={sshPass} onChange={setSshPass} secret />
        <MiniField label="SSH порт" value={sshPort} onChange={setSshPort} placeholder="22" />
        {wantPanel && (
          <div className="col-span-2">
            <MiniField label="Домен панели" value={panelDom} onChange={setPanelDom} placeholder="panel.example.com" />
          </div>
        )}
        {wantSub && (
          <div className="col-span-2">
            <MiniField label="Домен подписки" value={subDom} onChange={setSubDom} placeholder="sub.example.com" />
          </div>
        )}
      </div>

      {domainChanged && (
        <div className="mt-3 flex items-start gap-1.5 px-2.5 py-2 rounded-md border text-[11px]"
          style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
          <AlertTriangle size={12} className="shrink-0 mt-0.5" />
          <span>Смена домена требует пере-выпуска SSL — после сохранения переустановите «Reverse-proxy».</span>
        </div>
      )}

      {err && <p className="errmsg mt-2">{err}</p>}

      <div className="mt-3 flex items-center gap-2">
        <button type="button" onClick={save} disabled={!dirty}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border transition-colors
                     bg-[var(--bg2)] hover:bg-[var(--bg3)] text-[var(--t-mid)] border-[var(--line)]
                     disabled:opacity-40 disabled:cursor-not-allowed">
          <Save size={12} /> Сохранить
        </button>
        <p className="text-[10px] text-[var(--t-faint)]">Изменения хранятся только в этом браузере.</p>
      </div>
    </div>
  );
}

function MiniField({ label, value, onChange, placeholder, secret }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; secret?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <input type={secret ? "password" : "text"} value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} autoComplete="off" spellCheck={false}
        className="input transition-colors" />
    </div>
  );
}

// ── «Статистика» tab — traffic (vnstat) + fail2ban (POST /api/stats/node) ──
function StatsTab({ job }: { job: PanelJobSummary }) {
  const p = job.savedForm;
  const [security, setSecurity] = useState<SecurityStats | null>(null);
  const [traffic,  setTraffic]  = useState<TrafficStats | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "offline">("loading");
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);

  const fetchStats = useCallback(async () => {
    setState("loading");
    try {
      const res = await fetch("/api/stats/node", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: p.ip, ssh_port: p.ssh_port, ssh_user: p.ssh_user, ssh_password: p.ssh_password,
          domain: p.panel_domain || p.sub_domain || "",
        }),
      });
      const d = await res.json();
      if (!aliveRef.current) return;
      if (!d.online) { setState("offline"); return; }
      setSecurity(d.securityStats ?? null);
      setTraffic(d.trafficStats ?? null);
      setState("ok");
    } catch {
      if (aliveRef.current) setState("offline");
    }
  }, [p.ip, p.ssh_port, p.ssh_user, p.ssh_password, p.panel_domain, p.sub_domain]);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  if (state === "offline") {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-14 text-center">
        <XCircle size={30} className="text-[var(--err)]" />
        <p className="text-sm text-[var(--t-low)]">Сервер недоступен по SSH</p>
        <button type="button" onClick={fetchStats}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors">
          <RefreshCw size={12} /> Повторить
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <button type="button" onClick={fetchStats} disabled={state === "loading"}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)] transition-colors disabled:opacity-50">
          {state === "loading" ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Обновить
        </button>
      </div>
      <SecurityBlock stats={security} loading={state === "loading"} />
      <TrafficBlock stats={traffic} loading={state === "loading"} />
    </div>
  );
}

function SecurityBlock({ stats, loading }: { stats: SecurityStats | null; loading: boolean }) {
  // loaded but null = the probe returned online without fail2ban data (vnstat/
  // fail2ban absent) → "нет данных", NOT a spinner (which would hang forever).
  const noData = stats === null || (stats.fail2banTotal === 0 && stats.fail2banActive === 0 && stats.trafficGuardActive === 0);
  const f2bCls = stats && stats.fail2banActive > 0
    ? "text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn-line)]"
    : "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]";
  return (
    <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <ShieldCheck size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Безопасность сервера
        </span>
      </div>
      {loading ? (
        <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Сбор метрик по SSH…
        </p>
      ) : noData || stats === null ? (
        <p className="text-[11px] text-[var(--t-faint)]">Нет данных (fail2ban не установлен на сервере).</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)]">Fail2Ban (SSH)</span>
            <span className="tabular-nums">
              <span className={`px-1.5 py-0.5 rounded border ${f2bCls}`}>{stats.fail2banActive} активных</span>
              <span className="text-[var(--t-faint)]"> / {stats.fail2banTotal} всего</span>
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)]">TrafficGuard (CDN)</span>
            <span className="px-1.5 py-0.5 rounded border tabular-nums text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]">
              {stats.trafficGuardActive} заблокировано
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function fmtBytes(b: number): string {
  const gb = b / 1073741824;
  if (gb >= 1) return `${gb.toFixed(2)} ГБ`;
  return `${(b / 1048576).toFixed(2)} МБ`;
}

const PERIOD_LABEL: Record<TrafficPeriod, string> = {
  today: "За сегодня", week: "За неделю", month: "За месяц",
};

function TrafficBlock({ stats, loading }: { stats: TrafficStats | null; loading: boolean }) {
  const [period, setPeriod] = useState<TrafficPeriod>("today");
  // loaded but null = vnstat probe failed on an online box → "нет данных", not a
  // perpetual spinner.
  const noData = stats === null ||
    (["today", "week", "month"] as TrafficPeriod[]).every(pd => stats[pd].total === 0);
  const b = stats ? stats[period] : null;
  return (
    <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <Network size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Сетевой трафик
        </span>
        {!noData && stats && (
          <select value={period} onChange={e => setPeriod(e.target.value as TrafficPeriod)}
            className="ml-auto bg-[var(--bg2)] border border-[var(--line)] rounded px-1.5 py-0.5 text-[10px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]">
            {(["today", "week", "month"] as TrafficPeriod[]).map(pd => (
              <option key={pd} value={pd}>{PERIOD_LABEL[pd]}</option>
            ))}
          </select>
        )}
      </div>
      {loading ? (
        <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Чтение vnstat…
        </p>
      ) : noData || b === null ? (
        <p className="text-[11px] text-[var(--t-faint)]">Нет данных (vnstat не установлен на сервере).</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)] flex items-center gap-1.5">
              <ArrowDownToLine size={11} className="text-[var(--accent-hi)]" /> Входящий (RX)
            </span>
            <span className="text-[var(--t-hi)] tabular-nums">{fmtBytes(b.rx)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)] flex items-center gap-1.5">
              <ArrowUpFromLine size={11} className="text-[var(--ok)]" /> Исходящий (TX)
            </span>
            <span className="text-[var(--t-hi)] tabular-nums">{fmtBytes(b.tx)}</span>
          </div>
          <div className="flex items-center justify-between pt-1 border-t border-[var(--line-soft)]">
            <span className="text-[var(--t-low)] flex items-center gap-1.5">
              <Sigma size={11} className="text-[var(--t-low)]" /> Всего (Total)
            </span>
            <span className="text-[var(--t-hi)] font-medium tabular-nums">{fmtBytes(b.total)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Op-stream overlay (component reinstall/uninstall progress) ──
function OpStreamModal({ title, logs, status, onClose }: {
  title: string; logs: string[]; status: TaskStatus; onClose: () => void;
}) {
  const done = status === "success" || status === "failed";
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget && done) onClose(); }}>
      <div className="w-full max-w-2xl rounded-xl overflow-hidden flex flex-col max-h-[85vh]"
        style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="sticky top-0 flex items-center gap-2 px-5 py-3.5"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          {status === "running" ? <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent-hi)" }} />
            : status === "success" ? <CheckCircle2 size={14} style={{ color: "var(--ok)" }} />
            : <XCircle size={14} style={{ color: "var(--err)" }} />}
          <h2 className="text-sm font-semibold flex-1" style={{ color: "var(--t-hi)" }}>{title}</h2>
          <button onClick={onClose} disabled={!done} className="iconbtn disabled:opacity-40" title="Закрыть">
            <X size={15} />
          </button>
        </div>
        <div className="p-4 flex-1 min-h-0" style={{ minHeight: 240 }}>
          <TerminalOutput lines={logs} />
        </div>
      </div>
    </div>
  );
}
