import { useCallback, useState } from "react";
import { BookOpen, CheckCircle2, CloudDownload, Loader2, XCircle } from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";
import { TerminalOutput } from "../TerminalOutput";
import { StepProgress } from "../StepProgress";
import { useTaskStream, type StatusFrame } from "../../hooks/useTaskStream";

/**
 * «Копия сайта» (Справка, Wave-4 PR-11): httrack-зеркало на backend'е → файлы
 * автоматически импортируются в Библиотеку (папка «Сайты/<host>-<дата>»).
 */

const STEPS = ["Зеркалирование (httrack)", "Импорт файлов в Библиотеку"];
const DEPTHS = [1, 2, 3, 4, 5];
const SIZES = [20, 50, 80, 150, 200];

export function SiteCopy() {
  const [url, setUrl] = useState("");
  const [depth, setDepth] = useState(2);
  const [maxMb, setMaxMb] = useState(80);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<StatusFrame>({ status: "pending", current_step: 0, total_steps: STEPS.length });
  const [err, setErr] = useState("");

  useTaskStream({
    taskId,
    onLog: useCallback((l: string) => setLogs(ls => [...ls, l]), []),
    onStatus: useCallback((f: StatusFrame) => setStatus(prev => ({
      status: f.status,
      current_step: f.current_step === -1 ? prev.current_step : f.current_step,
      total_steps: f.total_steps === -1 ? prev.total_steps : f.total_steps,
    })), []),
  });

  const running = status.status === "running" || (status.status === "pending" && taskId !== null);
  const done = status.status === "success" || status.status === "failed";

  const start = async () => {
    setErr("");
    setLogs([]);
    setStatus({ status: "pending", current_step: 0, total_steps: STEPS.length });
    const res = await fetch("/api/sitecopy", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.trim(), depth, max_mb: maxMb }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
    setTaskId(d.task_id);
  };

  return (
    <Page max={760}>
      <PageHeader icon={<CloudDownload size={16} />} title="Копия сайта"
        subtitle="Зеркалирование сайта (HTTrack) — файлы автоматически попадают в Библиотеку" />

      <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div>
          <label className="label">URL сайта</label>
          <input className="input" value={url} onChange={e => setUrl(e.target.value)}
            placeholder="https://example.com" disabled={running} spellCheck={false} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label">Глубина рекурсии</label>
            <select className="selectbox" value={depth} disabled={running}
              onChange={e => setDepth(Number(e.target.value))}>
              {DEPTHS.map(d => <option key={d} value={d}>{d}{d === 2 ? " (по умолчанию)" : ""}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Максимальный объём</label>
            <select className="selectbox" value={maxMb} disabled={running}
              onChange={e => setMaxMb(Number(e.target.value))}>
              {SIZES.map(s => <option key={s} value={s}>{s} МБ</option>)}
            </select>
          </div>
        </div>
        <p className="hint">Копируется только свой домен (внешняя глубина 0). Файлы
          больше 25 МБ и внутренний кэш HTTrack пропускаются; всего — не более
          вместимости Библиотеки. Результат: «Библиотека → Файлы → Сайты/…».</p>
        {err && <p className="errmsg">{err}</p>}
        <button className="btn btn-primary" style={{ alignSelf: "flex-start" }} onClick={start}
          disabled={running || !url.trim()}>
          {running ? <Loader2 size={13} className="spin" /> : <CloudDownload size={13} />}
          Скопировать сайт
        </button>

        {taskId && (
          <>
            <StepProgress currentStep={status.current_step} totalSteps={status.total_steps}
              status={status.status} steps={STEPS} />
            {done && (
              <div style={{
                display: "flex", alignItems: "center", gap: 8, fontSize: 13,
                color: status.status === "success" ? "var(--ok)" : "var(--err)",
              }}>
                {status.status === "success"
                  ? <><CheckCircle2 size={15} /> Готово — файлы в «Библиотеке → Файлы → Сайты»</>
                  : <><XCircle size={15} /> Копирование не удалось — см. лог</>}
                {status.status === "success" && <BookOpen size={15} style={{ color: "var(--t-low)" }} />}
              </div>
            )}
            <div style={{ height: 240 }}>
              <TerminalOutput lines={logs} />
            </div>
          </>
        )}
      </div>
    </Page>
  );
}
