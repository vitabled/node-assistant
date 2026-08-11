import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Globe, Loader2, Rocket, XCircle } from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";
import { TerminalOutput } from "../TerminalOutput";
import { StepProgress } from "../StepProgress";
import { useTaskStream, type StatusFrame } from "../../hooks/useTaskStream";
import { deployJobsKey } from "../../auth/store";

/**
 * «REGRU хостинг» (Обходы БС, Wave-4 PR-9): привязка домена, размещённого на
 * reg.ru, к существующей ноде — через существующий флоу /api/replace-domain/node
 * (выпуск сертификата + смена домена + рестарт). SSH-креды — из карточек деплоя
 * (localStorage), на сервере не хранятся.
 */

interface DeployJobLite {
  taskId: string;
  domain: string;
  ip: string;
  newSshPort?: number;
  savedForm?: {
    domain?: string; ip?: string; ssh_user?: string;
    ssh_password?: string; ssh_port?: string | number;
    cert_provider?: string; email?: string; cf_api_key?: string;
  };
}

const STEPS = ["Подключение", "Выпуск сертификата", "Смена домена и рестарт"];
const DOMAIN_RE = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;

export function RegruTool() {
  const jobs = useMemo<DeployJobLite[]>(() => {
    try {
      const arr = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
      return Array.isArray(arr) ? arr.filter(j => j.savedForm?.domain) : [];
    } catch { return []; }
  }, []);

  const [jobId, setJobId] = useState("");
  const job = jobs.find(j => j.taskId === jobId) || jobs[0];
  const [newDomain, setNewDomain] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<StatusFrame>({ status: "pending", current_step: 0, total_steps: STEPS.length });
  const [err, setErr] = useState("");
  const busyRef = useRef(false);

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
    if (!job || busyRef.current) return;
    setErr("");
    const nd = newDomain.trim().toLowerCase();
    if (!DOMAIN_RE.test(nd)) { setErr("Некорректный домен (нужен FQDN, например node1.example.ru)"); return; }
    busyRef.current = true;
    setLogs([]);
    setStatus({ status: "pending", current_step: 0, total_steps: STEPS.length });
    const f = job.savedForm!;
    try {
      const res = await fetch("/api/replace-domain/node", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: f.ip || job.ip,
          ssh_user: f.ssh_user || "root",
          ssh_password: f.ssh_password || "",
          ssh_port: Number(f.ssh_port || job.newSshPort || 22),
          old_domain: f.domain || job.domain,
          new_domain: nd,
          cert_provider: f.cert_provider || "cloudflare",
          email: f.email || "",
          cf_api_key: f.cf_api_key || null,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setTaskId(d.task_id);
    } finally { busyRef.current = false; }
  };

  useEffect(() => { if (!jobId && jobs.length) setJobId(jobs[0].taskId); }, [jobs, jobId]);

  return (
    <Page max={860}>
      <PageHeader icon={<Globe size={16} />} title="REGRU хостинг"
        subtitle="Обход через домен на reg.ru: привязка reg.ru-домена к существующей ноде" />

      <div className="card card-p" style={{ marginBottom: 14 }}>
        <p className="micro" style={{ marginBottom: 8 }}>Шаги на стороне reg.ru</p>
        <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: "var(--t-mid)", display: "flex", flexDirection: "column", gap: 4 }}>
          <li>Закажите домен (или хостинг с доменом) в <strong>reg.ru</strong> — белый для DPI регистратор/хостинг.</li>
          <li>В DNS-зоне создайте <strong>A-запись</strong> домена на IP вашей ноды.</li>
          <li>Дождитесь резолва и вернитесь сюда — сервис перевыпустит сертификат и сменит домен ноды.</li>
        </ol>
      </div>

      {jobs.length === 0 ? (
        <div className="card card-p" style={{ textAlign: "center", color: "var(--t-faint)", fontSize: 13 }}>
          Нет карточек деплоя — сначала задеплойте ноду («Деплой ноды»): креды для смены домена берутся из её карточки.
        </div>
      ) : (
        <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <p className="micro">Привязка домена к ноде</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Нода</label>
              <select className="selectbox" value={job?.taskId || ""} onChange={e => setJobId(e.target.value)} disabled={running}>
                {jobs.map(j => <option key={j.taskId} value={j.taskId}>{j.savedForm?.domain} ({j.ip})</option>)}
              </select>
            </div>
            <div>
              <label className="label">Новый reg.ru-домен</label>
              <input className="input" value={newDomain} onChange={e => setNewDomain(e.target.value)}
                placeholder="node1.example.ru" disabled={running} />
            </div>
          </div>
          {job && (
            <p className="hint">
              Текущий домен: <strong>{job.savedForm?.domain}</strong> · SSH {job.ip}:{job.newSshPort || job.savedForm?.ssh_port || 22}.
              Креды и настройки сертификата подставляются из карточки деплоя.
            </p>
          )}
          {err && <p className="errmsg">{err}</p>}
          <button className="btn btn-primary" style={{ alignSelf: "flex-start" }} onClick={start}
            disabled={running || !job || !newDomain.trim()}>
            {running ? <Loader2 size={13} className="spin" /> : <Rocket size={13} />}
            Сменить домен ноды
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
                    ? <><CheckCircle2 size={15} /> Домен ноды сменён на {newDomain.trim().toLowerCase()}</>
                    : <><XCircle size={15} /> Смена домена не удалась — см. лог</>}
                </div>
              )}
              <div style={{ height: 220 }}>
                <TerminalOutput lines={logs} />
              </div>
            </>
          )}
        </div>
      )}
    </Page>
  );
}
