import { useEffect, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, Circle, Loader2, XCircle, ChevronDown } from "lucide-react";
import type { TaskStatus } from "../hooks/useTaskStream";

export const DEPLOY_STEPS = [
  "Подключение",
  "Обновление системы",
  "Node Accelerator",
  "TrafficGuard",
  "Добавление порта SSH",
  "Перезагрузка",
  "Проверка нового порта SSH",
  "Удаление старого порта SSH",
  "Cloudflare DNS + SSL",
  "Remnanode",
  "Маскировочный сайт",
  "WARP Native",
  "Hysteria2",
];

// Collapsible groups over the flat step list (1-indexed, inclusive). Steps not
// covered by any group render standalone (Подключение=1, Обновление=2, SSL=9).
const STEP_GROUPS: { title: string; from: number; to: number }[] = [
  { title: "Оптимизация ОС",      from: 3,  to: 4  },
  { title: "Сеть",                from: 5,  to: 8  },
  { title: "Установка remnanode", from: 10, to: 13 },
];

export const RENEW_STEPS = [
  "Подключение к серверу",
  "Выпуск и установка сертификата",
  "Перезапуск сервисов",
];

interface Props {
  currentStep: number;
  totalSteps:  number;
  status:      TaskStatus;
  steps?:      string[];   // defaults to DEPLOY_STEPS
}

function useStepTimer(running: boolean): string {
  const [secs, setSecs]   = useState(0);
  const startRef          = useRef<number | null>(null);

  useEffect(() => {
    if (!running) {
      setSecs(0);
      startRef.current = null;
      return;
    }
    startRef.current = Date.now();
    setSecs(0);
    const id = setInterval(
      () => setSecs(Math.floor((Date.now() - startRef.current!) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [running]);

  const m = String(Math.floor(secs / 60)).padStart(2, "0");
  const s = String(secs % 60).padStart(2, "0");
  return `${m}:${s}`;
}

interface StepState { currentStep: number; status: TaskStatus; isRunning: boolean; elapsed: string }

function StepRow({ stepNum, displayNum, label, s, nested }: {
  stepNum: number; displayNum?: string; label: string; s: StepState; nested?: boolean;
}) {
  const isDone   = s.status === "success" || s.currentStep > stepNum;
  const isActive = s.currentStep === stepNum && s.isRunning;
  const isFailed = s.currentStep === stepNum && s.status === "failed";

  const rowStyle = isActive
    ? { background: "var(--accent-dim)", color: "var(--accent-hi)", borderColor: "var(--accent-line)" }
    : isDone   ? { color: "var(--ok)" }
    : isFailed ? { color: "var(--err)" }
    : { color: "var(--t-faint)" };

  return (
    <div
      className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm
                  transition-all duration-200 ${isActive ? "border shadow-sm" : ""}`}
      style={{ ...rowStyle, paddingLeft: nested ? 22 : undefined }}
    >
      <span className="shrink-0">
        {isDone   ? <CheckCircle2 size={13} />
        : isActive ? <Loader2 size={13} className="animate-spin" />
        : isFailed ? <XCircle size={13} />
        :             <Circle size={13} />}
      </span>
      <span className="flex-1 leading-tight">
        <span className="text-xs faint mr-1.5">{displayNum ?? stepNum}.</span>
        {label}
      </span>
      {isActive && (
        <span className="text-xs tabular-nums ml-auto shrink-0" style={{ color: "var(--accent-hi)" }}>
          {s.elapsed}
        </span>
      )}
    </div>
  );
}

function StepGroup({ major, title, from, to, steps, s }: {
  major: number; title: string; from: number; to: number; steps: string[]; s: StepState;
}) {
  const activeInside = s.currentStep >= from && s.currentStep <= to;
  const allDone = s.status === "success" || s.currentStep > to;
  const [collapsed, setCollapsed] = useState(false);
  // A group holding the active/failed step stays open regardless of manual collapse.
  const open = !collapsed || activeInside;

  const dot = s.status === "failed" && activeInside ? "var(--err)"
    : allDone ? "var(--ok)"
    : activeInside ? "var(--accent-hi)"
    : "var(--t-faint)";

  return (
    <div className="rounded-md" style={{ border: "1px solid var(--line-soft)" }}>
      <button type="button" onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left rounded-md
                   hover:bg-[var(--bg3)] transition-colors">
        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
        <span className="text-[11px] font-semibold uppercase tracking-wider flex-1" style={{ color: "var(--t-low)" }}>
          <span className="faint mr-1.5">{major}.</span>{title}
        </span>
        {allDone && <CheckCircle2 size={12} style={{ color: "var(--ok)" }} />}
        <ChevronDown size={13} style={{ color: "var(--t-faint)", transform: open ? "none" : "rotate(-90deg)", transition: "transform .15s" }} />
      </button>
      {open && (
        <div className="flex flex-col gap-0.5 px-1 pb-1">
          {Array.from({ length: to - from + 1 }, (_, k) => {
            const stepNum = from + k;
            return <StepRow key={stepNum} stepNum={stepNum} displayNum={`${major}.${k + 1}`} label={steps[stepNum - 1]} s={s} nested />;
          })}
        </div>
      )}
    </div>
  );
}

export function StepProgress({ currentStep, totalSteps, status, steps = DEPLOY_STEPS }: Props) {
  const isRunning = status === "running";
  const elapsed   = useStepTimer(isRunning);
  const s: StepState = { currentStep, status, isRunning, elapsed };

  const doneCount =
    status === "success" ? totalSteps : Math.max(0, currentStep - 1);
  const pct = totalSteps > 0 ? Math.round((doneCount / totalSteps) * 100) : 0;

  const barColor =
    status === "success" ? "var(--ok)"
    : status === "failed" ? "var(--err)"
    : "var(--accent)";

  // Grouping applies only to the full deploy step list (not RENEW_STEPS).
  const grouped = steps === DEPLOY_STEPS;

  return (
    <div className="flex flex-col gap-3">
      {/* Progress bar */}
      <div className="flex items-center gap-2">
        <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "var(--bg3)" }}>
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${pct}%`, background: barColor }}
          />
        </div>
        <span className="text-xs tabular-nums w-8 text-right dim">{pct}%</span>
      </div>

      {/* Step list */}
      <div className="flex flex-col gap-0.5">
        {!grouped
          ? steps.map((label, i) => (
              <StepRow key={i + 1} stepNum={i + 1} label={label} s={s} />
            ))
          : (() => {
              // Cosmetic hierarchical numbering (1, 2, 3.1, 3.2, …): each
              // top-level block (standalone step or group) gets a major number;
              // group children get major.child. The flat 13-index → backend-step
              // mapping is untouched (StepRow still keys its status off stepNum).
              const rows: ReactNode[] = [];
              let n = 1;
              let major = 1;
              while (n <= steps.length) {
                const grp = STEP_GROUPS.find(g => g.from === n);
                if (grp) {
                  rows.push(
                    <StepGroup key={`g-${grp.title}`} major={major} title={grp.title} from={grp.from} to={grp.to} steps={steps} s={s} />,
                  );
                  n = grp.to + 1;
                } else {
                  rows.push(<StepRow key={n} stepNum={n} displayNum={String(major)} label={steps[n - 1]} s={s} />);
                  n += 1;
                }
                major += 1;
              }
              return rows;
            })()}
      </div>
    </div>
  );
}
