import { useState, useCallback, useEffect } from "react";
import {
  X, Square, Server, CheckCircle2, XCircle, Loader2,
  Terminal as TermIcon, Clock, Pencil, RotateCcw, ShieldCheck,
  Network, ArrowDownToLine, ArrowUpFromLine, Sigma,
} from "lucide-react";
import { StepProgress, DEPLOY_STEPS } from "./StepProgress";
import { TerminalOutput } from "./TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../hooks/useTaskStream";
import type { DeployJobSummary } from "./DeployDashboard";

const INITIAL_STATUS: StatusFrame = {
  status:       "pending",
  current_step: 0,
  total_steps:  DEPLOY_STEPS.length,
};

interface SecurityStats {
  fail2banActive: number;
  fail2banTotal: number;
  trafficGuardActive: number;
}

interface TrafficBucket { rx: number; tx: number; total: number }
interface TrafficStats { today: TrafficBucket; week: TrafficBucket; month: TrafficBucket }
type TrafficPeriod = "today" | "week" | "month";

interface Props {
  job:            DeployJobSummary;
  onRemove:       (taskId: string) => void;
  onEdit:         (job: DeployJobSummary) => void;
  onRetry:        (job: DeployJobSummary) => Promise<void>;
  onStatusChange: (taskId: string, status: "success" | "failed") => void;
}

export function DeployCard({ job, onRemove, onEdit, onRetry, onStatusChange }: Props) {
  const [logs,       setLogs]       = useState<string[]>([]);
  const [stepStatus, setStepStatus] = useState<StatusFrame>(
    job.finalStatus
      ? { status: job.finalStatus, current_step: 0, total_steps: DEPLOY_STEPS.length }
      : INITIAL_STATUS
  );
  const [showDetail, setShowDetail] = useState(false);
  const [retrying,   setRetrying]   = useState(false);

  const addLog   = useCallback((line: string) => setLogs(l => [...l, line]), []);
  const onStatus = useCallback((frame: StatusFrame) =>
    setStepStatus(prev => ({
      status:       frame.status,
      current_step: frame.current_step === -1 ? prev.current_step : frame.current_step,
      total_steps:  frame.total_steps  === -1 ? prev.total_steps  : frame.total_steps,
    })), []);

  useTaskStream({ taskId: job.taskId, onLog: addLog, onStatus });

  // Persist final status upward when it changes
  useEffect(() => {
    if (stepStatus.status === "success" || stepStatus.status === "failed") {
      onStatusChange(job.taskId, stepStatus.status);
    }
  }, [stepStatus.status, job.taskId, onStatusChange]);

  // ── Node stats poll (SUCCESS nodes only) ──────────────────
  // One endpoint returns both security + traffic. Polled every 5 min (vnstat
  // updates its DB discretely, so more frequent polling adds no value). Uses the
  // node's own SSH creds from savedForm — per-request, never stored server-side.
  const [security, setSecurity] = useState<SecurityStats | null>(null);
  const [traffic,  setTraffic]  = useState<TrafficStats | null>(null);
  useEffect(() => {
    if (stepStatus.status !== "success") return;
    const f = job.savedForm;
    const sshPort = parseInt(
      f.change_ssh_port ? f.new_ssh_port : f.current_ssh_port, 10,
    ) || 22;
    let alive = true;
    const fetchStats = async () => {
      try {
        const res = await fetch("/api/stats/node", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ip: f.ip, ssh_port: sshPort, ssh_user: f.ssh_user, ssh_password: f.ssh_password,
          }),
        });
        const d = await res.json();
        if (!alive || !d.online) return;
        if (d.securityStats) setSecurity(d.securityStats);
        if (d.trafficStats)  setTraffic(d.trafficStats);
      } catch { /* keep last */ }
    };
    fetchStats();
    const id = setInterval(fetchStats, 300_000);   // 5 min
    return () => { alive = false; clearInterval(id); };
  }, [stepStatus.status, job.savedForm]);

  const isRunning = stepStatus.status === "running" ||
    (stepStatus.status === "pending" && logs.length === 0 && !job.finalStatus);
  const isFailed  = stepStatus.status === "failed";
  const isDone    = stepStatus.status === "success" || stepStatus.status === "failed";

  const stopDeploy = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    await fetch("/api/deploy/stop", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ task_id: job.taskId }),
    }).catch(() => {});
  };

  const handleRetry = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRetrying(true);
    try {
      await onRetry(job);
    } finally {
      setRetrying(false);
    }
  };

  const handleEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    onEdit(job);
  };

  const doneCount = stepStatus.status === "success"
    ? stepStatus.total_steps
    : Math.max(0, stepStatus.current_step - 1);
  const pct = stepStatus.total_steps > 0
    ? Math.round((doneCount / stepStatus.total_steps) * 100)
    : 0;
  const stepLabel = DEPLOY_STEPS[stepStatus.current_step - 1] ?? "";

  const startFmt = new Date(job.startedAt).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });

  return (
    <>
      <div
        onClick={() => setShowDetail(true)}
        className="cursor-pointer rounded-xl border border-gray-700/60 bg-gray-900/60
                   hover:border-gray-600 hover:bg-gray-900/80 transition-all flex flex-col"
      >
        {/* Header */}
        <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <StatusIcon status={stepStatus.status} isRunning={isRunning} />
            <div className="min-w-0">
              <p className="text-sm font-medium text-white truncate">{job.domain}</p>
              <p className="text-xs text-gray-500">{job.ip}:{job.newSshPort}</p>
            </div>
          </div>
          <StatusBadge status={stepStatus.status} isRunning={isRunning} />
        </div>

        {/* Progress bar */}
        <div className="px-4">
          <div className="h-1 bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                stepStatus.status === "success" ? "bg-green-500"
                : stepStatus.status === "failed"  ? "bg-red-500"
                : "bg-blue-500"
              }`}
              style={{ width: `${stepStatus.status === "success" ? 100 : pct}%` }}
            />
          </div>
        </div>

        {/* Step label */}
        <div className="px-4 py-3 min-h-[2.25rem]">
          {isRunning && stepStatus.current_step > 0 && (
            <p className="text-xs text-gray-400 truncate">
              [{stepStatus.current_step}/{stepStatus.total_steps}]&nbsp;{stepLabel}
            </p>
          )}
          {isRunning && stepStatus.current_step === 0 && (
            <p className="text-xs text-gray-600">Инициализация...</p>
          )}
          {stepStatus.status === "success" && (
            <p className="text-xs text-green-400">Деплой завершён успешно</p>
          )}
          {stepStatus.status === "failed" && (
            <p className="text-xs text-red-400">Ошибка выполнения</p>
          )}
        </div>

        {/* Security + traffic blocks — only for SUCCESS nodes */}
        {stepStatus.status === "success" && (
          <>
            <SecurityBlock stats={security} />
            <TrafficBlock stats={traffic} />
          </>
        )}

        {/* Footer */}
        <div
          className="px-4 py-2.5 border-t border-gray-800/60 flex items-center gap-2"
          onClick={e => e.stopPropagation()}
        >
          <Clock size={11} className="text-gray-700 shrink-0" />
          <span className="text-[10px] text-gray-700 flex-1 tabular-nums">{startFmt}</span>

          <span className="text-[10px] text-gray-700 flex items-center gap-1 mr-1">
            <TermIcon size={10} /> лог
          </span>

          {/* Edit button — always visible */}
          <button
            onClick={handleEdit}
            title="Редактировать конфигурацию"
            className="p-1.5 rounded text-gray-600 hover:text-blue-400 hover:bg-gray-800
                       transition-colors"
          >
            <Pencil size={12} />
          </button>

          {/* Running: Stop button */}
          {isRunning && (
            <button onClick={stopDeploy}
              className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium
                         bg-red-950/50 hover:bg-red-900/60 text-red-400 border border-red-900/40
                         transition-colors">
              <Square size={10} fill="currentColor" /> Стоп
            </button>
          )}

          {/* Failed: Retry button */}
          {isFailed && (
            <button onClick={handleRetry} disabled={retrying}
              title="Перезапустить деплой с теми же параметрами"
              className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium
                         bg-amber-950/50 hover:bg-amber-900/60 text-amber-400 border border-amber-900/40
                         transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
              {retrying
                ? <Loader2 size={10} className="animate-spin" />
                : <RotateCcw size={10} />
              }
              Повторить
            </button>
          )}

          {/* Done: Remove button */}
          {isDone && (
            <button onClick={() => onRemove(job.taskId)}
              className="p-1.5 rounded text-gray-700 hover:text-gray-400 hover:bg-gray-800
                         transition-colors" title="Удалить задачу">
              <X size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Detail modal */}
      {showDetail && (
        <DeployDetailModal
          job={job}
          logs={logs}
          stepStatus={stepStatus}
          isRunning={isRunning}
          isFailed={isFailed}
          retrying={retrying}
          onStop={stopDeploy}
          onRetry={handleRetry}
          onEdit={handleEdit}
          onClose={() => setShowDetail(false)}
        />
      )}
    </>
  );
}

// ── Status helpers ────────────────────────────────────────────

function StatusIcon({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  const base = "rounded-full p-1.5 shrink-0";
  if (isRunning)            return <div className={`${base} bg-blue-950/60 text-blue-400`}><Loader2 size={14} className="animate-spin" /></div>;
  if (status === "success") return <div className={`${base} bg-green-950/50 text-green-400`}><CheckCircle2 size={14} /></div>;
  if (status === "failed")  return <div className={`${base} bg-red-950/40 text-red-400`}><XCircle size={14} /></div>;
  return <div className={`${base} bg-gray-800 text-gray-500`}><Server size={14} /></div>;
}

function StatusBadge({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  if (isRunning) return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-blue-950/60 border border-blue-800/50 text-blue-400 shrink-0">
      <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" /> Работает
    </span>
  );
  if (status === "success") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-green-950/50 border border-green-800/40 text-green-400 shrink-0">
      <CheckCircle2 size={10} /> Готово
    </span>
  );
  if (status === "failed") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-red-950/40 border border-red-900/40 text-red-400 shrink-0">
      <XCircle size={10} /> Ошибка
    </span>
  );
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-gray-800 border border-gray-700/50 text-gray-500 shrink-0">
      Ожидание
    </span>
  );
}

// ── Security block (Fail2Ban / TrafficGuard) — SUCCESS nodes ──
function SecurityBlock({ stats }: { stats: SecurityStats | null }) {
  // Active bans get a soft amber highlight to signal repelled attacks.
  const f2bActiveCls = stats && stats.fail2banActive > 0
    ? "text-amber-300 bg-amber-950/40 border-amber-900/40"
    : "text-gray-300 bg-gray-800/60 border-gray-700/40";
  const tgActiveCls = stats && stats.trafficGuardActive > 0
    ? "text-amber-300 bg-amber-950/40 border-amber-900/40"
    : "text-gray-300 bg-gray-800/60 border-gray-700/40";

  return (
    <div className="mx-4 mb-3 rounded-lg border border-gray-800/70 bg-gray-950/40 px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <ShieldCheck size={12} className="text-gray-500" />
        <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">
          Безопасность сервера
        </span>
      </div>
      {stats === null ? (
        <p className="text-[11px] text-gray-600 flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Сбор метрик по SSH…
        </p>
      ) : (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-gray-500">Fail2Ban (SSH)</span>
            <span className="tabular-nums">
              <span className={`px-1.5 py-0.5 rounded border ${f2bActiveCls}`}>
                {stats.fail2banActive} активных
              </span>
              <span className="text-gray-600"> / {stats.fail2banTotal} всего</span>
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-500">TrafficGuard (CDN)</span>
            <span className={`px-1.5 py-0.5 rounded border tabular-nums ${tgActiveCls}`}>
              {stats.trafficGuardActive} заблокировано
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Network-traffic block (vnstat) — SUCCESS nodes ────────────
function fmtBytes(b: number): string {
  const gb = b / 1073741824;
  if (gb >= 1) return `${gb.toFixed(2)} ГБ`;
  return `${(b / 1048576).toFixed(2)} МБ`;
}

const PERIOD_LABEL: Record<TrafficPeriod, string> = {
  today: "За сегодня", week: "За неделю", month: "За месяц",
};

function TrafficBlock({ stats }: { stats: TrafficStats | null }) {
  const [period, setPeriod] = useState<TrafficPeriod>("today");
  const b = stats ? stats[period] : null;

  return (
    <div className="mx-4 mb-3 rounded-lg border border-gray-800/70 bg-gray-950/40 px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <Network size={12} className="text-gray-500" />
        <span className="text-[10px] font-semibold text-gray-500 uppercase tracking-widest">
          Сетевой трафик
        </span>
        <select
          value={period}
          onChange={e => setPeriod(e.target.value as TrafficPeriod)}
          onClick={e => e.stopPropagation()}
          className="ml-auto bg-gray-900/80 border border-gray-700/60 rounded px-1.5 py-0.5
                     text-[10px] text-gray-300 focus:outline-none focus:ring-1 focus:ring-blue-500/30"
        >
          {(["today", "week", "month"] as TrafficPeriod[]).map(p => (
            <option key={p} value={p}>{PERIOD_LABEL[p]}</option>
          ))}
        </select>
      </div>
      {b === null ? (
        <p className="text-[11px] text-gray-600 flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Чтение vnstat…
        </p>
      ) : (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-gray-500 flex items-center gap-1.5">
              <ArrowDownToLine size={11} className="text-blue-400" /> Входящий (RX)
            </span>
            <span className="text-gray-200 tabular-nums">{fmtBytes(b.rx)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-500 flex items-center gap-1.5">
              <ArrowUpFromLine size={11} className="text-green-400" /> Исходящий (TX)
            </span>
            <span className="text-gray-200 tabular-nums">{fmtBytes(b.tx)}</span>
          </div>
          <div className="flex items-center justify-between pt-1 border-t border-gray-800/50">
            <span className="text-gray-400 flex items-center gap-1.5">
              <Sigma size={11} className="text-gray-500" /> Всего (Total)
            </span>
            <span className="text-white font-medium tabular-nums">{fmtBytes(b.total)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Detail modal ──────────────────────────────────────────────

function DeployDetailModal({
  job, logs, stepStatus, isRunning, isFailed, retrying,
  onStop, onRetry, onEdit, onClose,
}: {
  job:        DeployJobSummary;
  logs:       string[];
  stepStatus: StatusFrame;
  isRunning:  boolean;
  isFailed:   boolean;
  retrying:   boolean;
  onStop:     (e?: React.MouseEvent) => Promise<void>;
  onRetry:    (e: React.MouseEvent)  => Promise<void>;
  onEdit:     (e: React.MouseEvent)  => void;
  onClose:    () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-gray-950 border border-gray-800 rounded-xl w-full max-w-5xl h-[85vh]
                      flex flex-col overflow-hidden shadow-2xl">

        {/* Header */}
        <div className="shrink-0 flex items-center gap-3 px-5 py-3.5 border-b border-gray-800">
          <Server size={15} className="text-gray-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-white truncate">{job.domain}</p>
            <p className="text-xs text-gray-500">{job.ip}:{job.newSshPort}</p>
          </div>

          <div className="flex items-center gap-2">
            {/* Edit */}
            <button onClick={onEdit}
              title="Редактировать конфигурацию"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                         bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700
                         transition-colors">
              <Pencil size={11} /> Редактировать
            </button>

            {/* Stop (running) */}
            {isRunning && (
              <button onClick={onStop}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                           bg-red-900/50 hover:bg-red-800/70 text-red-400 border border-red-900/50
                           transition-colors">
                <Square size={11} fill="currentColor" /> Остановить
              </button>
            )}

            {/* Retry (failed) */}
            {isFailed && (
              <button onClick={onRetry} disabled={retrying}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                           bg-amber-950/60 hover:bg-amber-900/70 text-amber-400 border border-amber-900/50
                           transition-colors disabled:opacity-50">
                {retrying
                  ? <Loader2 size={11} className="animate-spin" />
                  : <RotateCcw size={11} />
                }
                Повторить
              </button>
            )}
          </div>

          <StatusBadge status={stepStatus.status} isRunning={isRunning} />

          <button onClick={onClose}
            title="Закрыть (деплой продолжится в фоне)"
            className="ml-1 p-1.5 rounded text-gray-600 hover:text-gray-200 hover:bg-gray-800 transition-colors">
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 grid grid-cols-[260px_1fr] min-h-0">

          <div className="border-r border-gray-800 p-4 overflow-y-auto">
            <StepProgress
              currentStep={stepStatus.current_step}
              totalSteps={stepStatus.total_steps}
              status={stepStatus.status}
              steps={DEPLOY_STEPS}
            />
          </div>

          <div className="flex flex-col min-h-0">
            <div className="shrink-0 px-4 py-2 border-b border-gray-800/60 flex items-center gap-2">
              <TermIcon size={12} className="text-gray-600" />
              <span className="text-[11px] text-gray-600 uppercase tracking-widest font-medium">
                Вывод терминала
              </span>
              {logs.length > 0 && (
                <span className="ml-auto text-[11px] text-gray-700 tabular-nums">
                  {logs.length} строк
                </span>
              )}
            </div>
            <div className="flex-1 p-3 min-h-0">
              {logs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center gap-2
                                text-gray-700 text-sm border border-gray-800/50 rounded-lg">
                  <TermIcon size={24} className="opacity-30" />
                  <span>Ожидание вывода...</span>
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
