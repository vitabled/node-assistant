import { useState, useCallback, useEffect, useRef, memo } from "react";
import {
  X, Square, Server, CheckCircle2, XCircle, Loader2,
  Terminal as TermIcon, Clock, Pencil, RotateCcw, ShieldCheck, Youtube,
  Network, ArrowDownToLine, ArrowUpFromLine, Sigma,
  ShieldAlert, RefreshCw, Trash2, Wrench, Gauge, Play, ArrowLeftRight, Palette,
  ChevronDown, ChevronUp, Boxes, Lock,
} from "lucide-react";
import { motion } from "motion/react";
import { NODE_COLOR_PRESETS, colorHex, cardTint, setJobColor } from "../utils/nodeColors";
import { StepProgress, DEPLOY_STEPS } from "./StepProgress";
import { TerminalOutput } from "./TerminalOutput";
import { ReplaceDomainModal } from "./rw/ReplaceDomainModal";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../hooks/useTaskStream";
import { toast } from "./infra/Toast";
import type { DeployJobSummary } from "./DeployDashboard";
import type { FormData } from "./DeployForm";

interface CertInfo { daysLeft: number; notAfter: string }

type NodeAction = "reinstall" | "reconfigure" | "uninstall";

// Coerce a saved deploy form into the /api/node/step payload (mirrors
// DeployDashboard.submitDeploy — NodeOpRequest extends DeployRequest, so the
// port ints / nullable tokens must match the deploy contract).
export function opPayload(data: FormData) {
  return {
    ...data,
    current_ssh_port: parseInt(data.current_ssh_port, 10),
    new_ssh_port:     parseInt(data.new_ssh_port,     10),
    remnanode_port:   parseInt(data.remnanode_port,   10),
    remnanode_token:  data.remnanode_token || null,
    template_id:      data.template_id     || null,
    plugin_uuid:      data.plugin_uuid     || null,
    haproxy_source_port: parseInt(data.haproxy_source_port, 10),
    haproxy_dest_port:   parseInt(data.haproxy_dest_port,   10),
    haproxy_maxconn:     parseInt(data.haproxy_maxconn,     10),
  };
}

// Manageable components for a SUCCESS node, derived from its saved form. Steps
// «Подключение»/«Обновление системы» and the SSH-port network steps are NOT
// manageable (excluded by design — see node_ops.py).
export function manageableComponents(f: FormData): { id: string; label: string }[] {
  if (f.mode === "haproxy") {
    return [
      ...(f.optimize ? [{ id: "node_accelerator", label: "Node Accelerator" }] : []),
      ...(f.install_trafficguard !== false ? [{ id: "trafficguard", label: "TrafficGuard" }] : []),
      ...(f.install_test_tools !== false ? [{ id: "test_tools", label: "Тест-инструменты" }] : []),
      { id: "haproxy", label: "HAProxy" },
    ];
  }
  return [
    ...(f.optimize ? [{ id: "node_accelerator", label: "Node Accelerator" }] : []),
    ...(f.install_trafficguard !== false ? [{ id: "trafficguard", label: "TrafficGuard" }] : []),
    ...(f.install_test_tools !== false ? [{ id: "test_tools", label: "Тест-инструменты" }] : []),
    { id: "remnanode", label: "Remnanode" },
    { id: "masking",   label: "Маскировочный сайт" },
    ...(f.install_warp ? [{ id: "warp", label: "WARP Native" }] : []),
    { id: "ssl",       label: "SSL-сертификат" },
    { id: "hysteria2", label: "Hysteria2" },
  ];
}

// ── Переиспользуемая кнопка действия: motion (hover/press) + иконка+подпись + состояния ──
interface ActionButtonProps {
  label:     string;
  onClick:   (e: React.MouseEvent<HTMLButtonElement>) => void;
  icon?:     React.ReactNode;
  variant?:  "default" | "primary" | "danger" | "warn" | "ghost";
  disabled?: boolean;
  loading?:  boolean;
  title?:    string;
  ariaLabel?: string;
  size?:     "xs" | "sm";
  className?: string;
}

function ActionButton({
  label, onClick, icon, variant = "default", disabled, loading,
  title, ariaLabel, size = "sm", className = "",
}: ActionButtonProps) {
  const base = "inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed select-none";
  const sizes = { xs: "px-1.5 py-0.5 text-[10px]", sm: "px-2 py-1 text-[11px]" };
  const variants: Record<NonNullable<ActionButtonProps["variant"]>, string> = {
    default: "border border-[var(--line)] bg-[var(--bg2)] text-[var(--t-mid)] hover:bg-[var(--bg3)]",
    primary: "bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]",
    danger:  "border btn-danger",
    warn:    "border btn-warn",
    ghost:   "text-[var(--t-faint)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)]",
  };
  const noAnim = disabled || loading;
  return (
    <motion.button
      type="button"
      whileHover={noAnim ? undefined : { scale: 1.04 }}
      whileTap={noAnim ? undefined : { scale: 0.95 }}
      transition={{ type: "spring", stiffness: 500, damping: 25, mass: 0.6 }}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      aria-label={ariaLabel ?? title ?? label}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
    >
      {loading ? <Loader2 size={size === "xs" ? 10 : 11} className="animate-spin" /> : icon}
      <span>{label}</span>
    </motion.button>
  );
}

const INITIAL_STATUS: StatusFrame = {
  status:       "pending",
  current_step: 0,
  total_steps:  DEPLOY_STEPS.length,
};

interface SecurityStats {
  fail2banActive: number;
  fail2banTotal: number;
  trafficGuardActive: number;
  nginxUpdater?: string;
  ytRegion?: string;
  xrayVersion?: string;
}

interface TrafficBucket { rx: number; tx: number; total: number }
interface TrafficStats { today: TrafficBucket; week: TrafficBucket; month: TrafficBucket }
type TrafficPeriod = "today" | "week" | "month";

interface Props {
  job:            DeployJobSummary;
  onRemove:       (taskId: string) => void;
  onEdit:         (job: DeployJobSummary) => void;
  onRetry:        (job: DeployJobSummary) => Promise<void>;
  onRestart:      (job: DeployJobSummary) => Promise<void>;
  onStatusChange: (taskId: string, status: "success" | "failed") => void;
  /** Цветовая маркировка виджета (null — сброс); родитель обновляет jobs. */
  onColorChange?: (taskId: string, colorKey: string | null) => void;
}

// memo: карточка перерисовывается только когда меняется её собственный job
// (статус/цвет) — апдейт соседней карточки (stream шагов) не перерисовывает
// всю сетку «Деплой нод» (раньше каждый onStatusChange дёргал все карточки).
function DeployCardImpl({ job, onRemove, onEdit, onRetry, onRestart, onStatusChange, onColorChange }: Props) {
  const [logs,       setLogs]       = useState<string[]>([]);
  const [stepStatus, setStepStatus] = useState<StatusFrame>(
    job.finalStatus
      ? { status: job.finalStatus, current_step: 0, total_steps: DEPLOY_STEPS.length }
      : INITIAL_STATUS
  );
  const [showDetail, setShowDetail] = useState(false);
  const [retrying,   setRetrying]   = useState(false);
  const [showReplace, setShowReplace] = useState(false);  // «Сменить домен» wizard (Plan E)
  // Свёрнутое представление успешной карточки (мало DOM → плавный скролл).
  const [expanded,      setExpanded]      = useState(false);
  const [confirmStop,   setConfirmStop]   = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

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
  const [cert,     setCert]     = useState<CertInfo | null>(null);
  // Первая попытка сбора завершилась (успех/офлайн/ошибка). До этого момента
  // карточка показывает компактный скелетон вместо пачки пустых блоков — так
  // она не «торчит» раздутой, пока по SSH ещё едут название/безопасность.
  const [statsReady, setStatsReady] = useState(false);
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
            domain: f.domain,
          }),
        });
        const d = await res.json();
        if (!alive || !d.online) return;
        if (d.securityStats) setSecurity(d.securityStats);
        if (d.trafficStats)  setTraffic(d.trafficStats);
        setCert(d.certInfo ?? null);
      } catch { /* keep last */ }
      finally { if (alive) setStatsReady(true); }
    };
    fetchStats();
    const id = setInterval(fetchStats, 300_000);   // 5 min
    return () => { alive = false; clearInterval(id); };
  }, [stepStatus.status, job.savedForm]);

  // ── Component management op stream (reinstall/reconfigure/uninstall) ──
  const [opTaskId,     setOpTaskId]     = useState<string | null>(null);
  const [opLogs,       setOpLogs]       = useState<string[]>([]);
  const [opStatus,     setOpStatus]     = useState<TaskStatus>("pending");
  const [opTitle,      setOpTitle]      = useState("");
  const [opSubmitting, setOpSubmitting] = useState(false);  // POST in-flight (before task_id)
  const opAddLog   = useCallback((line: string) => setOpLogs(l => [...l, line]), []);
  const opOnStatus = useCallback((frame: StatusFrame) => setOpStatus(frame.status), []);
  useTaskStream({ taskId: opTaskId, onLog: opAddLog, onStatus: opOnStatus });

  // Busy across the whole op (from click, through the in-flight POST, until the
  // streamed task finishes) — so buttons can't fire a second op and the modal
  // doesn't blink closed between submit and task_id arrival.
  const opBusy = opSubmitting || (opStatus === "running" && !!opTaskId);

  const runOp = useCallback(async (
    component: string, action: NodeAction, title: string, version?: string,
  ) => {
    setOpLogs([]); setOpStatus("running"); setOpTitle(title); setOpTaskId(null);
    setOpSubmitting(true);
    try {
      const res = await fetch("/api/node/step", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...opPayload(job.savedForm), component, action,
          ...(version ? { version } : {}),
        }),
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
  }, [job.savedForm]);

  // Замена образа remnanode на выбранную версию — тот же stream, что и runOp
  // (POST /api/node/step с component=remnanode, action=reinstall, version=<…>).
  const replaceRemnanodeImage = useCallback((version: string) => {
    runOp("remnanode", "reinstall", `Замена образа remnanode → ${version}`, version);
  }, [runOp]);

  // Standalone Xray-core version update — same task-stream modal as runOp,
  // but its own endpoint (/api/node/xray-version) since it's not a managed
  // deploy component (no detect/uninstall, just an in-place binary swap).
  const runXrayUpdate = useCallback(async (version: string) => {
    setOpLogs([]); setOpStatus("running"); setOpTitle(`Xray-core → ${version}`); setOpTaskId(null);
    setOpSubmitting(true);
    try {
      const f = job.savedForm;
      const sshPort = parseInt(f.change_ssh_port ? f.new_ssh_port : f.current_ssh_port, 10) || 22;
      const res = await fetch("/api/node/xray-version", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: f.ip, ssh_port: sshPort, ssh_user: f.ssh_user, ssh_password: f.ssh_password,
          version,
        }),
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
  }, [job.savedForm]);

  const isRunning = stepStatus.status === "running" ||
    (stepStatus.status === "pending" && logs.length === 0 && !job.finalStatus);
  const isFailed  = stepStatus.status === "failed";
  const isDone    = stepStatus.status === "success" || stepStatus.status === "failed";
  const isSuccess = stepStatus.status === "success";
  const collapsed = isSuccess && !expanded;

  const stopDeploy = async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    await fetch("/api/deploy/stop", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ task_id: job.taskId }),
    }).catch(() => {});
  };

  // Danger-подтверждение: первый клик вооружает, второй — выполняет.
  const handleStopConfirm   = async (e: React.MouseEvent) => { setConfirmStop(false);   await stopDeploy(e); };
  const handleRemoveConfirm = () => { setConfirmRemove(false); onRemove(job.taskId); };

  const handleRetry = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRetrying(true);
    try {
      await onRetry(job);
    } finally {
      setRetrying(false);
    }
  };

  const handleRestartWaiting = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setRetrying(true);
    try {
      await onRestart(job);
    } catch (error) {
      toast(error instanceof Error ? error.message : "Не удалось перезапустить деплой", "error");
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

  // Цветовая маркировка (Wave-4 PR-9): кромка + тинт, хранение в deploy_jobs.
  const markHex = colorHex((job as { color?: string }).color);
  const [colorOpen, setColorOpen] = useState(false);
  const pickColor = (key: string | null) => {
    // Родитель обновляет jobs (стейт + localStorage); без него — сами в стор.
    if (onColorChange) onColorChange(job.taskId, key);
    else setJobColor(job.taskId, key);
    setColorOpen(false);
  };

  return (
    <>
      {collapsed ? (
        <CollapsedCard
          job={job}
          security={security}
          cert={cert}
          statsReady={statsReady}
          markHex={markHex}
          onExpand={() => setExpanded(true)}
        />
      ) : (
      <div
        onClick={() => setShowDetail(true)}
        style={markHex
          ? { borderLeft: `3px solid ${markHex}`, background: cardTint(markHex) }
          : undefined}
        className="cursor-pointer rounded-xl border border-[var(--line)] bg-[var(--bg2)]
                   hover:border-[var(--line)] hover:bg-[var(--bg3)] transition-all flex flex-col"
      >
        {/* Header */}
        <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <StatusIcon status={stepStatus.status} isRunning={isRunning} />
            <div className="min-w-0">
              <p className="text-sm font-medium text-[var(--t-hi)] truncate">{job.domain}</p>
              <p className="text-xs text-[var(--t-low)]">{job.ip}:{job.newSshPort}</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {isSuccess && (
              <button type="button" title="Свернуть" aria-label="Свернуть"
                onClick={e => { e.stopPropagation(); setExpanded(false); }}
                className="iconbtn" style={{ width: 26, height: 26 }}>
                <ChevronUp size={13} />
              </button>
            )}
            <div className="relative">
              <button type="button" title="Цвет виджета ноды" data-testid="node-color-btn"
                onClick={e => { e.stopPropagation(); setColorOpen(o => !o); }}
                className="iconbtn" style={{ width: 26, height: 26 }}>
                <Palette size={13} style={markHex ? { color: markHex } : undefined} />
              </button>
              {colorOpen && (
                <div className="absolute right-0 top-8 z-30 rounded-lg border p-2 flex gap-1.5"
                  style={{ background: "var(--bg1)", borderColor: "var(--line)", boxShadow: "var(--shadow-pop)" }}
                  onClick={e => e.stopPropagation()}>
                  {NODE_COLOR_PRESETS.map(p => (
                    <button key={p.key} type="button" title={p.label}
                      data-testid={`node-color-${p.key}`}
                      onClick={() => pickColor(p.key)}
                      style={{
                        width: 18, height: 18, borderRadius: 5, background: p.hex,
                        border: p.key === (job as { color?: string }).color
                          ? "2px solid var(--t-hi)" : "2px solid transparent",
                        cursor: "pointer",
                      }} />
                  ))}
                  <button type="button" title="Сбросить цвет" data-testid="node-color-reset"
                    onClick={() => pickColor(null)}
                    style={{
                      width: 18, height: 18, borderRadius: 5, cursor: "pointer",
                      border: "1px dashed var(--t-low)", background: "transparent",
                      color: "var(--t-low)", fontSize: 10, lineHeight: 1,
                    }}>✕</button>
                </div>
              )}
            </div>
            <StatusBadge status={stepStatus.status} isRunning={isRunning} />
          </div>
        </div>

        {/* Progress bar */}
        <div className="px-4">
          <div className="h-1 bg-[var(--bg3)] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                stepStatus.status === "success" ? "bg-[var(--ok)]"
                : stepStatus.status === "failed"  ? "bg-[var(--err)]"
                : "bg-[var(--accent)]"
              }`}
              style={{ width: `${stepStatus.status === "success" ? 100 : pct}%` }}
            />
          </div>
        </div>

        {/* Step label */}
        <div className="px-4 py-3 min-h-[2.25rem]">
          {isRunning && stepStatus.current_step > 0 && (
            <p className="text-xs text-[var(--t-low)] truncate">
              [{stepStatus.current_step}/{stepStatus.total_steps}]&nbsp;{stepLabel}
            </p>
          )}
          {isRunning && stepStatus.current_step === 0 && (
            <p className="text-xs text-[var(--t-faint)]">Инициализация...</p>
          )}
          {stepStatus.status === "success" && (
            <p className="text-xs text-[var(--ok)]">Деплой завершён успешно</p>
          )}
          {stepStatus.status === "failed" && (
            <p className="text-xs text-[var(--err)]">Ошибка выполнения</p>
          )}
        </div>

        {/* Security + traffic blocks — only for SUCCESS nodes. The traffic block
            is hidden when the node was deployed without vnstat (install_vnstat
            defaults to true, so pre-existing cards keep showing it). Пока SSH-сбор
            (название/безопасность/статус) не завершился — вместо стопки пустых
            блоков показываем один компактный скелетон; пустые секции не рисуем. */}
        {stepStatus.status === "success" && (
          <>
            {!statsReady ? (
              <ServerDataSkeleton />
            ) : (
              <>
                {security && <SecurityBlock stats={security} onUpdateXray={runXrayUpdate} xrayUpdateBusy={opBusy} />}
                {job.savedForm.install_vnstat !== false && traffic && <TrafficBlock stats={traffic} />}
                {job.savedForm.mode !== "haproxy" && cert && <CertBlock cert={cert} />}
                {!security && !traffic && (
                  <p className="mx-4 mb-3 text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
                    <ShieldAlert size={11} /> Сервер недоступен — данные безопасности и метрики не собраны.
                  </p>
                )}
              </>
            )}
            {job.savedForm.mode !== "haproxy" && (
              <ActionButton
                label="Сменить домен"
                icon={<ArrowLeftRight size={11} />}
                variant="warn"
                title="Сменить домен ноды"
                onClick={e => { e.stopPropagation(); setShowReplace(true); }}
                className="self-start"
              />
            )}
            <SpeedtestBlock form={job.savedForm} />
            <ManageBlock form={job.savedForm} onOp={runOp} busy={opBusy} />
            {job.savedForm.mode !== "haproxy" && (
              <RemnanodeVersionBlock onReplace={replaceRemnanodeImage} busy={opBusy} />
            )}
          </>
        )}

        {/* Footer */}
        <div
          className="px-4 py-2.5 border-t border-[var(--line-soft)] flex items-center gap-2 flex-wrap"
          onClick={e => e.stopPropagation()}
        >
          <Clock size={11} className="text-[var(--t-faint)] shrink-0" />
          <span className="text-[10px] text-[var(--t-faint)] flex-1 tabular-nums">{startFmt}</span>

          <span className="text-[10px] text-[var(--t-faint)] flex items-center gap-1 mr-1">
            <TermIcon size={10} /> лог
          </span>

          {/* Edit — always visible */}
          <ActionButton
            label="Изменить"
            icon={<Pencil size={11} />}
            variant="ghost"
            title="Редактировать конфигурацию"
            onClick={handleEdit}
          />

          {/* Running: Stop (danger-подтверждение) */}
          {isRunning && (confirmStop ? (
            <ActionButton
              label="Остановить?"
              icon={<ShieldAlert size={11} />}
              variant="danger"
              title="Подтвердить остановку деплоя"
              onClick={handleStopConfirm}
            />
          ) : (
            <ActionButton
              label="Стоп"
              icon={<Square size={10} fill="currentColor" />}
              variant="danger"
              title="Остановить деплой"
              onClick={() => setConfirmStop(true)}
            />
          ))}

          {/* Pending: idempotent restart (worker hasn't claimed it yet) */}
          {stepStatus.status === "pending" && !job.finalStatus && (
            <ActionButton
              label="Перезапустить"
              icon={<RotateCcw size={10} />}
              loading={retrying}
              variant="warn"
              title="Перезапустить ожидающий деплой без дублирования задачи"
              ariaLabel="Перезапустить ожидающий деплой"
              onClick={handleRestartWaiting}
            />
          )}

          {/* Failed: Retry */}
          {isFailed && (
            <ActionButton
              label="Повторить"
              icon={<RotateCcw size={10} />}
              loading={retrying}
              variant="warn"
              title="Перезапустить деплой с теми же параметрами"
              onClick={handleRetry}
            />
          )}

          {/* Done: Remove (danger-подтверждение) */}
          {isDone && (confirmRemove ? (
            <ActionButton
              label="Удалить?"
              icon={<ShieldAlert size={11} />}
              variant="danger"
              title="Подтвердить удаление задачи"
              onClick={handleRemoveConfirm}
            />
          ) : (
            <ActionButton
              label="Удалить"
              icon={<Trash2 size={11} />}
              variant="ghost"
              title="Удалить задачу"
              onClick={() => setConfirmRemove(true)}
            />
          ))}
        </div>
      </div>
      )}

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

      {/* Component-op stream overlay — visible from submit (opSubmitting) through
          the streamed task, so it doesn't blink between POST and task_id. */}
      {opTitle && (opSubmitting || opTaskId || opStatus === "failed") && (
        <OpStreamModal
          title={opTitle}
          logs={opLogs}
          status={opStatus}
          onClose={() => { setOpTitle(""); setOpTaskId(null); }}
        />
      )}

      {showReplace && (
        <ReplaceDomainModal
          mode="node"
          creds={{
            ip: job.savedForm.ip, ssh_user: job.savedForm.ssh_user,
            ssh_password: job.savedForm.ssh_password,
            ssh_port: parseInt(job.savedForm.change_ssh_port ? job.savedForm.new_ssh_port : job.savedForm.current_ssh_port, 10) || 22,
          }}
          currentDomain={job.savedForm.domain}
          onClose={() => setShowReplace(false)}
        />
      )}
    </>
  );
}

export const DeployCard = memo(DeployCardImpl);

// ── Certificate expiry block — SUCCESS remnanode nodes ────────
// Компактный скелетон карточки: пока по SSH не приехали название/безопасность
// сервера, вместо стопки пустых боксов рисуем одну тонкую строку-заглушку.
function ServerDataSkeleton() {
  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5 flex items-center gap-2">
      <Loader2 size={12} className="animate-spin" style={{ color: "var(--t-faint)" }} />
      <span className="text-[11px]" style={{ color: "var(--t-faint)" }}>
        Загружаем данные сервера (безопасность, трафик)…
      </span>
    </div>
  );
}

function CertBlock({ cert }: { cert: CertInfo | null }) {
  const days = cert?.daysLeft;
  const tone = days === undefined ? "var(--t-low)"
    : days < 0  ? "var(--err)"
    : days < 14 ? "var(--warn)"
    : "var(--ok)";
  const text = days === undefined ? "неизвестно"
    : days < 0  ? `истёк ${-days} дн. назад`
    : `${days} дн.`;
  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <ShieldCheck size={12} style={{ color: tone }} />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest flex-1">
          Сертификат
        </span>
        <span className="text-xs font-medium tabular-nums" style={{ color: tone }}>{text}</span>
      </div>
      {cert?.notAfter && (
        <p className="text-[10px] text-[var(--t-faint)] mt-1">до {cert.notAfter}</p>
      )}
    </div>
  );
}

// ── Speedtest block — «Характеристики и скорость» (SUCCESS, both modes) ──
// Shows the last stored run on mount (GET history — no SSH), and runs a new
// probe on demand: POST /api/stats/node-speedtest with the node's own SSH creds
// from savedForm (per-request, never stored server-side).

interface SpeedtestRun {
  ts?: number;
  iperf_mbps?: number | null; iperf_jitter?: number | null; ping_ms?: number | null;
  traceroute?: string | null;
  st_down?: number | null; st_up?: number | null; st_ping?: number | null;
  xray_down?: number | null; xray_up?: number | null; xray_ping?: number | null;
  cpu?: string | null; ram_mb?: number | null; disk?: string | null;
}
interface TestServer { id: string; name: string; ip: string; iperf_port: number }

const fmtMbps = (v?: number | null) => (v == null ? "—" : `${v.toFixed(1)} Мбит/с`);
const fmtMs   = (v?: number | null) => (v == null ? "—" : `${v.toFixed(1)} мс`);

// Cumulative metric levels for the iperf run (1=throughput, 2=+ping, 3=+traceroute).
const METRIC_LEVELS: { level: number; label: string }[] = [
  { level: 1, label: "Скорость" },
  { level: 2, label: "+пинг/джиттер" },
  { level: 3, label: "+трассировка" },
];

function SpeedtestBlock({ form }: { form: FormData }) {
  const [last,     setLast]     = useState<SpeedtestRun | null>(null);
  const [servers,  setServers]  = useState<TestServer[]>([]);
  const [serverId, setServerId] = useState("");
  const [xrayLink, setXrayLink] = useState("");
  const [level,    setLevel]    = useState(1);
  const [running,  setRunning]  = useState(false);
  // A run takes minutes; guard the post-await setState against unmount (retry
  // recreates the card). Same pattern as the stats poll above.
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);

  useEffect(() => {
    let alive = true;
    fetch(`/api/stats/node-speedtest/history?resource_key=${encodeURIComponent(form.ip)}&limit=1`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (alive && d?.history?.length) setLast(d.history[0]); })
      .catch(() => {});
    fetch("/api/testservers")
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (alive && Array.isArray(d?.servers)) setServers(d.servers); })
      .catch(() => {});
    return () => { alive = false; };
  }, [form.ip]);

  const runTest = async () => {
    setRunning(true);
    try {
      // Same SSH-port rule as the stats poll: the deployed node already
      // switched to the new port when change_ssh_port was on.
      const sshPort = parseInt(
        form.change_ssh_port ? form.new_ssh_port : form.current_ssh_port, 10,
      ) || 22;
      const res = await fetch("/api/stats/node-speedtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: form.ip, ssh_port: sshPort, ssh_user: form.ssh_user, ssh_password: form.ssh_password,
          testserver_id: serverId || null,
          xray_link: xrayLink.trim() || null,
          metrics: Array.from({ length: level }, (_, i) => i + 1),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (aliveRef.current)
          toast(typeof err.detail === "string" ? err.detail : "Ошибка теста скорости", "error");
        return;
      }
      const d = await res.json();
      if (!aliveRef.current) return;
      if (d.current) setLast(d.current);
      (d.warnings ?? []).forEach((w: string) => toast(w, "info"));
    } catch (e) {
      if (aliveRef.current) toast((e as Error).message, "error");
    } finally {
      if (aliveRef.current) setRunning(false);
    }
  };

  const Row = ({ label, value }: { label: string; value: string }) => (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[var(--t-low)] shrink-0">{label}</span>
      <span className="text-[var(--t-hi)] tabular-nums text-right truncate">{value}</span>
    </div>
  );

  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5"
      onClick={e => e.stopPropagation()}>
      <div className="flex items-center gap-1.5 mb-2">
        <Gauge size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Характеристики и скорость
        </span>
      </div>

      {/* Last stored result (characteristics + speeds) — скрыт, пока на ноде
          ещё не было ни одного прогона: вместо таблицы из «—» пустая секция
          не рисуется, остаётся только строка с кнопкой запуска. */}
      {last ? (
        <div className="flex flex-col gap-1.5 text-[11px] mb-2">
          <Row label="ЦП" value={last.cpu || "—"} />
          <Row label="RAM" value={last.ram_mb != null ? `${(last.ram_mb / 1024).toFixed(1)} ГБ` : "—"} />
          <Row label="Диск" value={last.disk || "—"} />
          <div className="pt-1 border-t border-[var(--line-soft)] flex flex-col gap-1.5">
            <Row label="iperf3" value={
              last.iperf_mbps != null
                ? `${fmtMbps(last.iperf_mbps)}${last.ping_ms != null ? ` · пинг ${fmtMs(last.ping_ms)}` : ""}`
                : "—"
            } />
            <Row label="Speedtest" value={
              last.st_down != null || last.st_up != null
                ? `↓ ${fmtMbps(last?.st_down)} · ↑ ${fmtMbps(last?.st_up)}`
                : "—"
            } />
            <Row label="Xray-туннель" value={
              last.xray_down != null || last.xray_up != null
                ? `↓ ${fmtMbps(last?.xray_down)} · ↑ ${fmtMbps(last?.xray_up)}`
                : "—"
            } />
          </div>
        </div>
      ) : (
        <p className="text-[10px] text-[var(--t-faint)] mb-2">
          Тест ещё не запускался на этой ноде.
        </p>
      )}

      {/* Run controls */}
      <div className="flex flex-col gap-1.5 pt-1.5 border-t border-[var(--line-soft)]">
        {servers.length === 0 ? (
          <p className="text-[10px] text-[var(--t-faint)]">
            Нет тест-серверов (Настройки → Сервера для тестирования) — iperf3-проба недоступна.
          </p>
        ) : (
          <select
            value={serverId}
            onChange={e => setServerId(e.target.value)}
            disabled={running}
            className="bg-[var(--bg2)] border border-[var(--line)] rounded px-1.5 py-1
                       text-[11px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]"
          >
            <option value="">Тест-сервер: не использовать</option>
            {servers.map(s => (
              <option key={s.id} value={s.id}>{s.name} ({s.ip}:{s.iperf_port})</option>
            ))}
          </select>
        )}
        <input
          type="password"
          value={xrayLink}
          onChange={e => setXrayLink(e.target.value)}
          disabled={running}
          placeholder="Xray-ссылка (vless/trojan/vmess/ss, опционально)"
          autoComplete="off"
          spellCheck={false}
          className="bg-[var(--bg2)] border border-[var(--line)] rounded px-1.5 py-1
                     text-[11px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]"
        />
        <div className="flex items-center gap-1">
          {METRIC_LEVELS.map(m => (
            <button key={m.level} type="button" disabled={running}
              onClick={() => setLevel(m.level)}
              className={`px-1.5 py-0.5 rounded border text-[10px] transition-colors ${
                level === m.level
                  ? "bg-[var(--accent-dim)] border-[var(--accent-line)] text-[var(--accent-hi)]"
                  : "bg-[var(--bg2)] border-[var(--line)] text-[var(--t-low)] hover:bg-[var(--bg3)]"
              }`}>
              {m.label}
            </button>
          ))}
        </div>
        <ActionButton
          label={running ? "Тест выполняется…" : "Запустить тест"}
          icon={<Play size={11} />}
          loading={running}
          variant="default"
          title="Запустить тест скорости"
          onClick={runTest}
          className="w-full justify-center py-1.5"
        />
      </div>
    </div>
  );
}

// ── Component management block — reinstall / reconfigure / uninstall ──
function ManageBlock({ form, onOp, busy }: {
  form: FormData;
  onOp: (component: string, action: NodeAction, title: string) => void;
  busy: boolean;
}) {
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const comps = manageableComponents(form);
  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5"
      onClick={e => e.stopPropagation()}>
      <div className="flex items-center gap-1.5 mb-2">
        <Wrench size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Управление компонентами
        </span>
      </div>
      <div className="flex flex-col gap-1">
        {comps.map(c => (
          <div key={c.id} className="flex items-center gap-2 py-0.5 flex-wrap">
            <span className="text-xs text-[var(--t-mid)] flex-1 truncate">{c.label}</span>
            <ActionButton
              label="Переустановить"
              icon={<RefreshCw size={11} />}
              variant="ghost"
              disabled={busy}
              title={`Переустановить ${c.label}`}
              onClick={() => onOp(c.id, "reinstall", `Переустановка: ${c.label}`)}
            />
            {confirmDel === c.id ? (
              <ActionButton
                label="Точно?"
                icon={<ShieldAlert size={10} />}
                variant="danger"
                disabled={busy}
                title="Подтвердить удаление"
                onClick={() => { setConfirmDel(null); onOp(c.id, "uninstall", `Удаление: ${c.label}`); }}
              />
            ) : (
              <ActionButton
                label="Удалить"
                icon={<Trash2 size={11} />}
                variant="ghost"
                disabled={busy}
                title={`Удалить ${c.label}`}
                onClick={() => setConfirmDel(c.id)}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Op-stream overlay (component reinstall/uninstall progress) ──
function OpStreamModal({ title, logs, status, onClose }: {
  title: string; logs: string[]; status: TaskStatus; onClose: () => void;
}) {
  const done = status === "success" || status === "failed";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
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

// ── Status helpers ────────────────────────────────────────────

function StatusIcon({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  const base = "rounded-full p-1.5 shrink-0";
  if (isRunning)            return <div className={`${base} bg-[var(--accent-dim)] text-[var(--accent-hi)]`}><Loader2 size={14} className="animate-spin" /></div>;
  if (status === "success") return <div className={`${base} bg-[var(--ok-dim)] text-[var(--ok)]`}><CheckCircle2 size={14} /></div>;
  if (status === "failed")  return <div className={`${base} bg-[var(--err-dim)] text-[var(--err)]`}><XCircle size={14} /></div>;
  return <div className={`${base} bg-[var(--bg3)] text-[var(--t-low)]`}><Server size={14} /></div>;
}

function StatusBadge({ status, isRunning }: { status: TaskStatus; isRunning: boolean }) {
  if (isRunning) return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-[var(--accent-dim)] border border-[var(--accent-line)] text-[var(--accent-hi)] shrink-0">
      <span className="dot pulse" style={{ background: "var(--accent-hi)" }} /> Работает
    </span>
  );
  if (status === "success") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-[var(--ok-dim)] border border-[var(--ok-line)] text-[var(--ok)] shrink-0">
      <CheckCircle2 size={10} /> Готово
    </span>
  );
  if (status === "failed") return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-[var(--err-dim)] border border-[var(--err-line)] text-[var(--err)] shrink-0">
      <XCircle size={10} /> Ошибка
    </span>
  );
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px]
                     font-medium bg-[var(--bg3)] border border-[var(--line)] text-[var(--t-low)] shrink-0">
      Ожидание
    </span>
  );
}

// ── Security block (Fail2Ban / TrafficGuard) — SUCCESS nodes ──
function SecurityBlock({ stats, onUpdateXray, xrayUpdateBusy }: {
  stats: SecurityStats | null;
  onUpdateXray?: (version: string) => void;
  xrayUpdateBusy?: boolean;
}) {
  const [xrayInput, setXrayInput] = useState("latest");
  // Active bans get a soft amber highlight to signal repelled attacks.
  const f2bActiveCls = stats && stats.fail2banActive > 0
    ? "text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn-line)]"
    : "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]";
  const tgActiveCls = stats && stats.trafficGuardActive > 0
    ? "text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn-line)]"
    : "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]";

  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <ShieldCheck size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Безопасность сервера
        </span>
      </div>
      {stats === null ? (
        <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Сбор метрик по SSH…
        </p>
      ) : (
        <div className="flex flex-col gap-1.5 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)]">Fail2Ban (SSH)</span>
            <span className="tabular-nums">
              <span className={`px-1.5 py-0.5 rounded border ${f2bActiveCls}`}>
                {stats.fail2banActive} активных
              </span>
              <span className="text-[var(--t-faint)]"> / {stats.fail2banTotal} всего</span>
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[var(--t-low)]">TrafficGuard (CDN)</span>
            <span className={`px-1.5 py-0.5 rounded border tabular-nums ${tgActiveCls}`}>
              {stats.trafficGuardActive} заблокировано
            </span>
          </div>
          {stats.ytRegion && (
            <div className="flex items-center justify-between">
              <span className="text-[var(--t-low)] flex items-center gap-1"><Youtube size={10} /> YouTube Region</span>
              <span className={`px-1.5 py-0.5 rounded border tabular-nums ${
                stats.ytRegion === "ads" ? "text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn-line)]"
                : stats.ytRegion === "unknown" ? "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]"
                : "text-[var(--ok)] bg-[var(--ok-dim)] border-[var(--ok-line)]"
              }`}>
                {stats.ytRegion === "ads" ? "Реклама" : stats.ytRegion === "unknown" ? "Неизвестно" : stats.ytRegion}
              </span>
            </div>
          )}
          {stats.nginxUpdater && (
            <div className="flex items-center justify-between">
              <span className="text-[var(--t-low)] flex items-center gap-1">Nginx Patch</span>
              <span className={`px-1.5 py-0.5 rounded border tabular-nums ${
                stats.nginxUpdater === "vuln" ? "text-[var(--err)] bg-[var(--err-dim)] border-[var(--err-line)]"
                : stats.nginxUpdater === "ok" ? "text-[var(--ok)] bg-[var(--ok-dim)] border-[var(--ok-line)]"
                : "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]"
              }`}>
                {stats.nginxUpdater === "vuln" ? "Уязвим" : stats.nginxUpdater === "ok" ? "OK" : "Отсутствует"}
              </span>
            </div>
          )}
          {onUpdateXray && (
            <div className="flex items-center justify-between gap-2 pt-1 mt-0.5 border-t border-[var(--line-soft)]">
              <span className="text-[var(--t-low)] flex items-center gap-1 flex-none">
                Xray-core
                {stats.xrayVersion && (
                  <span className="px-1.5 py-0.5 rounded border tabular-nums text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]">
                    {stats.xrayVersion}
                  </span>
                )}
              </span>
              <div className="flex items-center gap-1 flex-1 justify-end min-w-0">
                <input value={xrayInput} onChange={e => setXrayInput(e.target.value)}
                  placeholder="latest / v25.9.11" disabled={xrayUpdateBusy}
                  className="input text-[10px] px-1.5 py-1 w-[104px] min-w-0" />
                <button type="button" disabled={xrayUpdateBusy || !xrayInput.trim()}
                  onClick={() => onUpdateXray(xrayInput.trim())}
                  className="px-2 py-1 rounded-md text-[10px] font-medium border transition-colors
                             bg-[var(--bg2)] hover:bg-[var(--bg3)] text-[var(--t-mid)] border-[var(--line)]
                             disabled:opacity-50 flex-none">
                  Обновить
                </button>
              </div>
            </div>
          )}
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
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-2">
        <Network size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Сетевой трафик
        </span>
        <select
          value={period}
          onChange={e => setPeriod(e.target.value as TrafficPeriod)}
          onClick={e => e.stopPropagation()}
          className="ml-auto bg-[var(--bg2)] border border-[var(--line)] rounded px-1.5 py-0.5
                     text-[10px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]"
        >
          {(["today", "week", "month"] as TrafficPeriod[]).map(p => (
            <option key={p} value={p}>{PERIOD_LABEL[p]}</option>
          ))}
        </select>
      </div>
      {b === null ? (
        <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Чтение vnstat…
        </p>
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-[var(--bg1)] border border-[var(--line-soft)] rounded-xl w-full max-w-5xl h-[85vh]
                      flex flex-col overflow-hidden shadow-2xl">

        {/* Header */}
        <div className="shrink-0 flex items-center gap-3 px-5 py-3.5 border-b border-[var(--line-soft)]">
          <Server size={15} className="text-[var(--t-low)] shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-[var(--t-hi)] truncate">{job.domain}</p>
            <p className="text-xs text-[var(--t-low)]">{job.ip}:{job.newSshPort}</p>
          </div>

          <div className="flex items-center gap-2">
            {/* Edit */}
            <button onClick={onEdit}
              title="Редактировать конфигурацию"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                         bg-[var(--bg3)] hover:bg-[var(--row-hover)] text-[var(--t-mid)] border border-[var(--line)]
                         transition-colors">
              <Pencil size={11} /> Редактировать
            </button>

            {/* Stop (running) */}
            {isRunning && (
              <button onClick={onStop}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                           border btn-danger
                           transition-colors">
                <Square size={11} fill="currentColor" /> Остановить
              </button>
            )}

            {/* Retry (failed) */}
            {isFailed && (
              <button onClick={onRetry} disabled={retrying}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium
                           border btn-warn
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
            className="ml-1 p-1.5 rounded text-[var(--t-faint)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors">
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 grid grid-cols-1 sm:grid-cols-[260px_1fr] min-h-0">

          <div className="border-r border-[var(--line-soft)] p-4 overflow-y-auto">
            <StepProgress
              currentStep={stepStatus.current_step}
              totalSteps={stepStatus.total_steps}
              status={stepStatus.status}
              steps={DEPLOY_STEPS}
            />
          </div>

          <div className="flex flex-col min-h-0">
            <div className="shrink-0 px-4 py-2 border-b border-[var(--line-soft)] flex items-center gap-2">
              <TermIcon size={12} className="text-[var(--t-faint)]" />
              <span className="text-[11px] text-[var(--t-faint)] uppercase tracking-widest font-medium">
                Вывод терминала
              </span>
              {logs.length > 0 && (
                <span className="ml-auto text-[11px] text-[var(--t-faint)] tabular-nums">
                  {logs.length} строк
                </span>
              )}
            </div>
            <div className="flex-1 p-3 min-h-0">
              {logs.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center gap-2
                                text-[var(--t-faint)] text-sm border border-[var(--line-soft)] rounded-lg">
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

// ── Свёрнутая карточка успешной ноды (заголовок + безопасность + сертификат) ──
function CompactSecurity({ stats }: { stats: SecurityStats }) {
  const active = stats.fail2banActive > 0 || stats.trafficGuardActive > 0;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] text-[var(--t-low)] shrink-0"
      title={`Fail2Ban: ${stats.fail2banActive} активных · TrafficGuard: ${stats.trafficGuardActive} заблокировано`}
    >
      <ShieldCheck size={12} style={{ color: active ? "var(--warn)" : "var(--ok)" }} />
      <span className="tabular-nums">Fail2Ban {stats.fail2banActive}</span>
      <span className="text-[var(--t-faint)]">·</span>
      <span className="tabular-nums">TrafficGuard {stats.trafficGuardActive}</span>
    </span>
  );
}

function CompactCert({ cert }: { cert: CertInfo | null }) {
  const days = cert?.daysLeft;
  const tone = days === undefined ? "var(--t-low)"
    : days < 0  ? "var(--err)"
    : days < 14 ? "var(--warn)"
    : "var(--ok)";
  const text = days === undefined ? "неизвестно"
    : days < 0 ? `истёк ${-days} дн.` : `${days} дн.`;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[11px] shrink-0"
      title={cert?.notAfter ? `Сертификат до ${cert.notAfter}` : "Сертификат"}
    >
      <Lock size={12} style={{ color: tone }} />
      <span className="text-[var(--t-low)]">Сертификат</span>
      <span className="tabular-nums font-medium" style={{ color: tone }}>{text}</span>
    </span>
  );
}

function CollapsedCard({ job, security, cert, statsReady, markHex, onExpand }: {
  job:        DeployJobSummary;
  security:   SecurityStats | null;
  cert:       CertInfo | null;
  statsReady: boolean;
  markHex:    string | undefined;
  onExpand:   () => void;
}) {
  const hasRemnanode = job.savedForm.mode !== "haproxy";
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16 }}
      role="button"
      tabIndex={0}
      aria-label={`Развернуть карточку ${job.domain}`}
      onClick={onExpand}
      onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onExpand(); } }}
      style={markHex ? { borderLeft: `3px solid ${markHex}`, background: cardTint(markHex) } : undefined}
      className="cursor-pointer rounded-xl border border-[var(--line)] bg-[var(--bg2)]
                 hover:bg-[var(--bg3)] transition-colors flex flex-col"
    >
      <div className="px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0">
        <StatusIcon status="success" isRunning={false} />
        <div className="min-w-0 flex-1" style={{ minWidth: 140 }}>
          <p className="text-sm font-medium text-[var(--t-hi)] truncate">{job.domain}</p>
          <p className="text-xs text-[var(--t-low)]">{job.ip}:{job.newSshPort}</p>
        </div>

        {!statsReady ? (
          <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--t-faint)] shrink-0">
            <Loader2 size={12} className="animate-spin" /> Сбор данных…
          </span>
        ) : (
          <>
            {security && <CompactSecurity stats={security} />}
            {!security && (
              <span className="inline-flex items-center gap-1 text-[11px] text-[var(--t-faint)] shrink-0">
                <ShieldAlert size={12} /> сервер офлайн
              </span>
            )}
            {hasRemnanode && cert && <CompactCert cert={cert} />}
          </>
        )}

        <span className="inline-flex items-center gap-1 text-[11px] font-medium text-[var(--t-low)] shrink-0">
          <ChevronDown size={14} /> Развернуть
        </span>
      </div>
    </motion.div>
  );
}

// ── Замена образа remnanode (выбор версии из GET /api/node/remnanode/versions) ──
// Валидный Docker-тег: буквы/цифры/точки/дефисы/подчёркивания (без слэшей —
// это уже тег, а не репозиторий), длина 1–128.
const REMNANODE_TAG_RE = /^[A-Za-z0-9._-]{1,128}$/;

function RemnanodeVersionBlock({ onReplace, busy }: {
  onReplace: (version: string) => void;
  busy:      boolean;
}) {
  const [versions, setVersions] = useState<string[] | null>(null);
  const [version,  setVersion]  = useState("");
  const [loading,  setLoading]  = useState(true);
  // Ручной тег на случай, когда GET /versions вернул ошибку/пусто (backend
  // чинят, список станет доступен через снапшот — но фронт должен не падать).
  const [manual,   setManual]   = useState("");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch("/api/node/remnanode/versions")
      .then(r => (r.ok ? r.json() : null))
      .then((d: { versions?: unknown } | null) => {
        if (!alive) return;
        setVersions(Array.isArray(d?.versions) ? (d.versions as string[]) : []);
      })
      .catch(() => { if (alive) setVersions([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const manualTag   = manual.trim();
  const manualValid = REMNANODE_TAG_RE.test(manualTag);

  return (
    <div className="mx-4 mb-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] px-3 py-2.5"
      onClick={e => e.stopPropagation()}>
      <div className="flex items-center gap-1.5 mb-2">
        <Boxes size={12} className="text-[var(--t-low)]" />
        <span className="text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest">
          Образ remnanode
        </span>
      </div>

      {loading ? (
        <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1.5">
          <Loader2 size={10} className="animate-spin" /> Загружаем доступные версии…
        </p>
      ) : !versions || versions.length === 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-[10px] text-[var(--t-faint)]">
            Список версий недоступен — введите тег образа вручную.
          </p>
          <div className="flex items-center gap-2">
            <input
              value={manual}
              onChange={e => setManual(e.target.value)}
              disabled={busy}
              placeholder="например: v2.0.1"
              spellCheck={false}
              autoComplete="off"
              className="flex-1 min-w-0 bg-[var(--bg2)] border border-[var(--line)] rounded px-2 py-1.5
                         text-[11px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]
                         disabled:opacity-45 disabled:cursor-not-allowed"
            />
            <ActionButton
              label="Заменить"
              icon={<RefreshCw size={11} />}
              variant="default"
              disabled={busy || !manualValid}
              loading={busy}
              title="Заменить образ remnanode на указанный тег"
              onClick={() => onReplace(manualTag)}
            />
          </div>
          {manualTag !== "" && !manualValid && (
            <p className="text-[10px] text-[var(--warn)]">
              Тег: только буквы, цифры, точки, дефисы и подчёркивания (без слэшей), до 128 символов.
            </p>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <select
            value={version}
            onChange={e => setVersion(e.target.value)}
            disabled={busy}
            className="flex-1 min-w-0 bg-[var(--bg2)] border border-[var(--line)] rounded px-2 py-1.5
                       text-[11px] text-[var(--t-mid)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-dim)]"
          >
            <option value="">Выберите версию…</option>
            {versions.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <ActionButton
            label="Заменить"
            icon={<RefreshCw size={11} />}
            variant="default"
            disabled={busy || !version}
            loading={busy}
            title="Заменить образ remnanode на выбранную версию"
            onClick={() => onReplace(version)}
          />
        </div>
      )}
    </div>
  );
}
