import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { TaskStatus } from "../hooks/useTaskStream";

export const DEPLOY_STEPS = [
  "Подключение",
  "Обновление системы",
  "Node Accelerator",
  "TrafficGuard",
  "Dual-port SSH + ребут",
  "Проверка после ребута",
  "Cloudflare DNS + SSL",
  "Remnanode",
  "WARP Native",
  "SSL Certbot",
  "Маскировочный сайт",
];

export const RENEW_STEPS = [
  "Подключение к серверу",
  "Обновление сертификата",
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

export function StepProgress({ currentStep, totalSteps, status, steps = DEPLOY_STEPS }: Props) {
  const isRunning = status === "running";
  const elapsed   = useStepTimer(isRunning);

  const doneCount =
    status === "success" ? totalSteps : Math.max(0, currentStep - 1);
  const pct = totalSteps > 0 ? Math.round((doneCount / totalSteps) * 100) : 0;

  const barColor =
    status === "success" ? "var(--ok)"
    : status === "failed" ? "var(--err)"
    : "var(--accent)";

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
        {steps.map((label, i) => {
          const stepNum  = i + 1;
          const isDone   = status === "success" || currentStep > stepNum;
          const isActive = currentStep === stepNum && isRunning;
          const isFailed = currentStep === stepNum && status === "failed";

          const rowStyle = isActive
            ? { background: "var(--accent-dim)", color: "var(--accent-hi)", borderColor: "var(--accent-line)" }
            : isDone
            ? { color: "var(--ok)" }
            : isFailed
            ? { color: "var(--err)" }
            : { color: "var(--t-faint)" };

          return (
            <div
              key={stepNum}
              className={`flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-sm
                          transition-all duration-200 ${isActive ? "border shadow-sm" : ""}`}
              style={rowStyle}
            >
              <span className="shrink-0">
                {isDone   ? <CheckCircle2 size={13} />
                : isActive ? <Loader2 size={13} className="animate-spin" />
                : isFailed ? <XCircle size={13} />
                :             <Circle size={13} />}
              </span>

              <span className="flex-1 leading-tight">
                <span className="text-xs faint mr-1.5">{stepNum}.</span>
                {label}
              </span>

              {isActive && (
                <span className="text-xs tabular-nums ml-auto shrink-0" style={{ color: "var(--accent-hi)" }}>
                  {elapsed}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
