import { useState, useCallback, useEffect } from "react";
import {
  X, Server, LayoutTemplate, CheckCircle2, XCircle, Loader2, RotateCcw,
  Clock, Terminal as TermIcon, Globe, Database, ChevronRight,
} from "lucide-react";
import { StepProgress } from "../StepProgress";
import { TerminalOutput } from "../TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";
import { toast } from "../infra/Toast";
import type { PanelJobSummary } from "./PanelDashboard";

// Ф6 — panel/subscription deploy widget: a frame with two subframes (Панель +
// Подписка). Streams the install (useTaskStream) while running; derives each
// subframe's status from finalStatus + an optional SSH-reachability poll for
// SUCCESS jobs. Clicking a subframe calls onManage (wired in Ф7).

// Mirrors backend panel_pipeline.PANEL_STEP_LABELS (8 steps).
const PANEL_STEP_LABELS = [
  "Подключение к серверу",
  "Установка Docker",
  "Тест-инструменты",
  "Генерация секретов и .env",
  "docker-compose панели",
  "Reverse-proxy и SSL",
  "Запуск панели",
  "Установка страницы подписок",
];

const INITIAL_STATUS: StatusFrame = {
  status: "pending", current_step: 0, total_steps: PANEL_STEP_LABELS.length,
};

const TARGET_LABEL: Record<PanelJobSummary["target"], string> = {
  panel: "Панель", subpage: "Страница подписок", both: "Панель + подписка",
};

type SubStatus = "installing" | "online" | "offline" | "failed" | "absent";

interface Props {
  job:      PanelJobSummary;
  onRemove: (taskId: string) => void;
  onRetry:  (job: PanelJobSummary) => Promise<void>;
  onStatusChange: (taskId: string, status: "success" | "failed") => void;
  onManage?: (job: PanelJobSummary) => void;   // Ф7 opens the manage modal
}

export function PanelWidget({ job, onRemove, onRetry, onStatusChange, onManage }: Props) {
  const p = job.savedForm;
  const wantPanel = p.target === "panel" || p.target === "both";
  const wantSub   = p.target === "subpage" || p.target === "both";
  const subIp = p.sub_server?.ip ?? p.ip;
  const subPort = p.sub_server?.ssh_port ?? p.ssh_port;

  const [logs,       setLogs]       = useState<string[]>([]);
  const [stepStatus, setStepStatus] = useState<StatusFrame>(
    job.finalStatus
      ? { status: job.finalStatus, current_step: 0, total_steps: PANEL_STEP_LABELS.length }
      : INITIAL_STATUS
  );
  const [showLog,  setShowLog]  = useState(false);
  const [retrying, setRetrying] = useState(false);

  const addLog   = useCallback((line: string) => setLogs(l => [...l, line]), []);
  const onStatus = useCallback((frame: StatusFrame) =>
    setStepStatus(prev => ({
      status:       frame.status,
      current_step: frame.current_step === -1 ? prev.current_step : frame.current_step,
      total_steps:  frame.total_steps  === -1 ? prev.total_steps  : frame.total_steps,
    })), []);
  useTaskStream({ taskId: job.taskId, onLog: addLog, onStatus });

  // Persist final status upward.
  useEffect(() => {
    if (stepStatus.status === "success" || stepStatus.status === "failed")
      onStatusChange(job.taskId, stepStatus.status);
  }, [stepStatus.status, job.taskId, onStatusChange]);

  // ── Reachability poll (SUCCESS only) — light SSH probe per distinct server so
  //    a panel that later goes down flips online→offline. Creds per-request from
  //    savedForm, never stored server-side (same rule as DeployCard). ──
  const [reach, setReach] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (stepStatus.status !== "success") return;
    const targets: { ip: string; port: number; user: string; pw: string; domain: string }[] = [];
    if (wantPanel) targets.push({ ip: p.ip, port: p.ssh_port, user: p.ssh_user, pw: p.ssh_password, domain: p.panel_domain });
    if (wantSub) {
      const s = p.sub_server;
      targets.push(s
        ? { ip: s.ip, port: s.ssh_port, user: s.ssh_user, pw: s.ssh_password, domain: p.sub_domain }
        : { ip: p.ip, port: p.ssh_port, user: p.ssh_user, pw: p.ssh_password, domain: p.sub_domain });
    }
    const distinct = [...new Map(targets.map(t => [`${t.ip}:${t.port}`, t])).values()];
    let alive = true;
    const probe = () => distinct.forEach(async t => {
      try {
        const res = await fetch("/api/stats/node", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ip: t.ip, ssh_port: t.port, ssh_user: t.user, ssh_password: t.pw, domain: t.domain }),
        });
        const d = await res.json();
        // Key by ip:port, not ip — two boxes behind one IP on different SSH
        // ports would otherwise overwrite each other's reachability.
        if (alive) setReach(r => ({ ...r, [`${t.ip}:${t.port}`]: !!d.online }));
      } catch { /* keep last */ }
    });
    probe();
    const id = setInterval(probe, 300_000);   // 5 min
    return () => { alive = false; clearInterval(id); };
  }, [stepStatus.status, job.savedForm]);   // eslint-disable-line react-hooks/exhaustive-deps

  const isRunning = stepStatus.status === "running" ||
    (stepStatus.status === "pending" && logs.length === 0 && !job.finalStatus);
  const isFailed  = stepStatus.status === "failed";
  const isDone    = stepStatus.status === "success" || stepStatus.status === "failed";
  // Manage only after a SUCCESSFUL install — opening the modal mid-run would let
  // a /api/panel/step op race the still-running run_panel_pipeline on the same
  // /opt/remnawave (concurrent docker compose / .env writes). Mirrors DeployCard.
  const canManage = stepStatus.status === "success";

  const subStatusOf = (want: boolean, ip: string, port: number): SubStatus => {
    if (!want) return "absent";
    if (isRunning || stepStatus.status === "pending") return "installing";
    if (stepStatus.status === "failed") return "failed";
    // success: reachability may downgrade to offline; null (not yet probed) = online.
    const r = reach[`${ip}:${port}`];
    return r === false ? "offline" : "online";
  };

  const handleRetry = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRetrying(true);
    try {
      await onRetry(job);
    } catch (err) {
      toast(err instanceof Error ? err.message : "Не удалось перезапустить установку", "error");
    } finally {
      setRetrying(false);
    }
  };

  const doneCount = stepStatus.status === "success"
    ? stepStatus.total_steps
    : Math.max(0, stepStatus.current_step - 1);
  const pct = stepStatus.total_steps > 0 ? Math.round((doneCount / stepStatus.total_steps) * 100) : 0;
  const stepLabel = PANEL_STEP_LABELS[stepStatus.current_step - 1] ?? "";

  const startFmt = new Date(job.createdAt).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });

  return (
    <>
      <div className="rounded-xl border border-[var(--line)] bg-[var(--bg2)] flex flex-col">
        {/* Header */}
        <div className="px-4 pt-4 pb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <WidgetIcon status={stepStatus.status} isRunning={isRunning} />
            <div className="min-w-0">
              <p className="text-sm font-medium text-[var(--t-hi)] truncate">{TARGET_LABEL[p.target]}</p>
              <p className="text-xs text-[var(--t-low)] truncate">
                {wantPanel ? p.panel_domain : p.sub_domain}
              </p>
            </div>
          </div>
          <OverallBadge status={stepStatus.status} isRunning={isRunning} />
        </div>

        {/* Running / failed: progress */}
        {(isRunning || isFailed) && (
          <div className="px-4 pb-2" onClick={e => e.stopPropagation()}>
            <div className="h-1 bg-[var(--bg3)] rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all duration-500 ${
                isFailed ? "bg-[var(--err)]" : "bg-[var(--accent)]"}`}
                style={{ width: `${pct}%` }} />
            </div>
            <div className="flex items-center gap-2 mt-2">
              <p className="text-xs text-[var(--t-low)] truncate flex-1">
                {isRunning && stepStatus.current_step > 0
                  ? `[${stepStatus.current_step}/${stepStatus.total_steps}] ${stepLabel}`
                  : isRunning ? "Инициализация…"
                  : "Ошибка выполнения"}
              </p>
              <button type="button" onClick={() => setShowLog(true)}
                className="flex items-center gap-1 text-[11px] text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
                <TermIcon size={11} /> лог
              </button>
            </div>
          </div>
        )}

        {/* Two subframes */}
        <div className="px-4 pb-3 grid grid-cols-1 md:grid-cols-2 gap-3">
          <SubFrame
            title="Панель" icon={<Server size={13} />}
            status={subStatusOf(wantPanel, p.ip, p.ssh_port)}
            ip={p.ip} domain={p.panel_domain}
            onClick={onManage && wantPanel && canManage ? () => onManage(job) : undefined}
            extra={wantPanel ? (
              <Row icon={<Database size={11} />} label="Резервное копирование" value="не настроено" muted />
            ) : undefined}
          />
          <SubFrame
            title="Подписка" icon={<LayoutTemplate size={13} />}
            status={subStatusOf(wantSub, subIp, subPort)}
            ip={wantSub ? subIp : ""} domain={p.sub_domain}
            onClick={onManage && wantSub && canManage ? () => onManage(job) : undefined}
          />
        </div>

        {/* Footer */}
        <div className="px-4 py-2.5 border-t border-[var(--line-soft)] flex items-center gap-2">
          <Clock size={11} className="text-[var(--t-faint)] shrink-0" />
          <span className="text-[10px] text-[var(--t-faint)] flex-1 tabular-nums">{startFmt}</span>

          {isFailed && (
            <button onClick={handleRetry} disabled={retrying}
              title="Перезапустить установку с теми же параметрами"
              className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium border btn-warn transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
              {retrying ? <Loader2 size={10} className="animate-spin" /> : <RotateCcw size={10} />} Повторить
            </button>
          )}
          {isDone && (
            <button onClick={() => onRemove(job.taskId)}
              className="p-1.5 rounded text-[var(--t-faint)] hover:text-[var(--t-low)] hover:bg-[var(--bg3)] transition-colors"
              title="Удалить виджет">
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Log modal (install stream) */}
      {showLog && (
        <LogModal
          title={TARGET_LABEL[p.target]}
          stepStatus={stepStatus}
          logs={logs}
          onClose={() => setShowLog(false)}
        />
      )}
    </>
  );
}

// ── Subframe ──────────────────────────────────────────────────

const SUB_BADGE: Record<SubStatus, { text: string; cls: string; dot: string }> = {
  installing: { text: "устанавливается", cls: "text-[var(--accent-hi)] bg-[var(--accent-dim)] border-[var(--accent-line)]", dot: "var(--accent-hi)" },
  online:     { text: "онлайн",          cls: "text-[var(--ok)] bg-[var(--ok-dim)] border-[var(--ok-line)]",              dot: "var(--ok)" },
  offline:    { text: "оффлайн",         cls: "text-[var(--err)] bg-[var(--err-dim)] border-[var(--err-line)]",           dot: "var(--err)" },
  failed:     { text: "ошибка",          cls: "text-[var(--err)] bg-[var(--err-dim)] border-[var(--err-line)]",           dot: "var(--err)" },
  absent:     { text: "не установлено",  cls: "text-[var(--t-low)] bg-[var(--bg3)] border-[var(--line)]",                 dot: "var(--t-faint)" },
};

function SubFrame({ title, icon, status, ip, domain, extra, onClick }: {
  title: string; icon: React.ReactNode; status: SubStatus;
  ip: string; domain: string; extra?: React.ReactNode; onClick?: () => void;
}) {
  const b = SUB_BADGE[status];
  const muted = status === "absent";
  const clickable = !!onClick && !muted;
  return (
    <div
      onClick={clickable ? onClick : undefined}
      className={`rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5 flex flex-col gap-2
                  ${clickable ? "cursor-pointer hover:border-[var(--line)] hover:bg-[var(--bg2)] transition-colors" : ""}
                  ${muted ? "opacity-60" : ""}`}
    >
      <div className="flex items-center gap-1.5">
        <span style={{ color: "var(--t-low)" }}>{icon}</span>
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest flex-1">{title}</span>
        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium border ${b.cls}`}>
          <span className="w-1.5 h-1.5 rounded-full" style={{ background: b.dot }} /> {b.text}
        </span>
        {clickable && <ChevronRight size={12} className="text-[var(--t-faint)]" />}
      </div>
      {muted ? (
        <p className="text-[11px] text-[var(--t-faint)]">Не входит в эту установку.</p>
      ) : (
        <div className="flex flex-col gap-1 text-[11px]">
          <Row icon={<Server size={11} />} label="IP" value={ip || "—"} />
          <Row icon={<Globe size={11} />} label="Домен" value={domain || "—"} />
          {extra}
        </div>
      )}
    </div>
  );
}

function Row({ icon, label, value, muted }: {
  icon: React.ReactNode; label: string; value: string; muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[var(--t-low)] flex items-center gap-1.5 shrink-0">{icon} {label}</span>
      <span className={`tabular-nums text-right truncate ${muted ? "text-[var(--t-faint)]" : "text-[var(--t-hi)]"}`}>{value}</span>
    </div>
  );
}

// ── Header icon / badge ───────────────────────────────────────

function WidgetIcon({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  const base = "rounded-full p-1.5 shrink-0";
  if (isRunning)            return <div className={`${base} bg-[var(--accent-dim)] text-[var(--accent-hi)]`}><Loader2 size={14} className="animate-spin" /></div>;
  if (status === "success") return <div className={`${base} bg-[var(--ok-dim)] text-[var(--ok)]`}><CheckCircle2 size={14} /></div>;
  if (status === "failed")  return <div className={`${base} bg-[var(--err-dim)] text-[var(--err)]`}><XCircle size={14} /></div>;
  return <div className={`${base} bg-[var(--bg3)] text-[var(--t-low)]`}><Server size={14} /></div>;
}

function OverallBadge({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  if (isRunning) return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--accent-dim)] border border-[var(--accent-line)] text-[var(--accent-hi)] shrink-0">
      <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-hi)] animate-pulse" /> Установка
    </span>
  );
  if (status === "success") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--ok-dim)] border border-[var(--ok-line)] text-[var(--ok)] shrink-0">
      <CheckCircle2 size={10} /> Готово
    </span>
  );
  if (status === "failed") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--err-dim)] border border-[var(--err-line)] text-[var(--err)] shrink-0">
      <XCircle size={10} /> Ошибка
    </span>
  );
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-[var(--bg3)] border border-[var(--line)] text-[var(--t-low)] shrink-0">
      Ожидание
    </span>
  );
}

// ── Log modal (install stream: StepProgress + terminal) ───────

function LogModal({ title, stepStatus, logs, onClose }: {
  title: string; stepStatus: StatusFrame; logs: string[]; onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="bg-[var(--bg1)] border border-[var(--line-soft)] rounded-xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden shadow-2xl">
        <div className="shrink-0 flex items-center gap-3 px-5 py-3.5 border-b border-[var(--line-soft)]">
          <Server size={15} className="text-[var(--t-low)] shrink-0" />
          <p className="text-sm font-semibold text-[var(--t-hi)] truncate flex-1">Установка: {title}</p>
          <button onClick={onClose} className="p-1.5 rounded text-[var(--t-faint)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors">
            <X size={15} />
          </button>
        </div>
        <div className="flex-1 grid grid-cols-1 sm:grid-cols-[260px_1fr] min-h-0">
          <div className="border-r border-[var(--line-soft)] p-4 overflow-y-auto">
            <StepProgress
              currentStep={stepStatus.current_step}
              totalSteps={stepStatus.total_steps}
              status={stepStatus.status}
              steps={PANEL_STEP_LABELS}
            />
          </div>
          <div className="flex flex-col min-h-0">
            <div className="shrink-0 px-4 py-2 border-b border-[var(--line-soft)] flex items-center gap-2">
              <TermIcon size={12} className="text-[var(--t-faint)]" />
              <span className="text-[11px] text-[var(--t-faint)] uppercase tracking-widest font-medium">Вывод терминала</span>
              {logs.length > 0 && <span className="ml-auto text-[11px] text-[var(--t-faint)] tabular-nums">{logs.length} строк</span>}
            </div>
            <div className="flex-1 p-3 min-h-0">
              {logs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center gap-2 text-[var(--t-faint)] text-sm border border-[var(--line-soft)] rounded-lg">
                  <TermIcon size={24} className="opacity-30" />
                  <span>Ожидание вывода…</span>
                </div>
              ) : (
                <TerminalOutput lines={logs} />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
