import { useState } from "react";
import {
  Plus, Rocket, X, ServerCog, Search, Loader2,
  CheckCircle2, XCircle, HelpCircle,
} from "lucide-react";
import { DeployCard } from "./DeployCard";
import { DeployForm, type FormData } from "./DeployForm";
import { deployJobsKey } from "../auth/store";

export interface DeployJobSummary {
  taskId:     string;
  domain:     string;
  ip:         string;
  newSshPort: number;
  startedAt:  number;
  savedForm:  FormData;          // full form data for retry / edit
  finalStatus?: "success" | "failed";
}

function loadJobs(): DeployJobSummary[] {
  try { return JSON.parse(localStorage.getItem(deployJobsKey()) ?? "[]"); }
  catch { return []; }
}

function saveJobs(jobs: DeployJobSummary[]) {
  try { localStorage.setItem(deployJobsKey(), JSON.stringify(jobs)); }
  catch {}
}

export function DeployDashboard() {
  const [jobs,     setJobs]     = useState<DeployJobSummary[]>(loadJobs);
  const [showForm, setShowForm] = useState(false);
  const [editJob,  setEditJob]  = useState<DeployJobSummary | null>(null);
  // Add-existing-server flow: `showExisting` = the detect/checklist modal;
  // `existingPreset` = detected creds + skip_components handed to the deploy form.
  const [showExisting,   setShowExisting]   = useState(false);
  const [existingPreset, setExistingPreset] = useState<Partial<FormData> | null>(null);

  const submitDeploy = async (data: FormData): Promise<string> => {
    const res = await fetch("/api/deploy", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...data,
        current_ssh_port: parseInt(data.current_ssh_port, 10),
        new_ssh_port:     parseInt(data.new_ssh_port,     10),
        remnanode_port:   parseInt(data.remnanode_port,   10),
        remnanode_token:  data.remnanode_token || null,
        template_id:      data.template_id     || null,
        internal_squad_ids: data.internal_squad_ids,
        external_squad_ids: data.external_squad_ids,
        plugin_uuid:        data.plugin_uuid || null,
        // HAProxy relay fields (numbers parsed; strings pass through)
        haproxy_source_port: parseInt(data.haproxy_source_port, 10),
        haproxy_dest_port:   parseInt(data.haproxy_dest_port,   10),
        haproxy_maxconn:     parseInt(data.haproxy_maxconn,     10),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    const { task_id } = await res.json();
    return task_id as string;
  };

  const addJob = async (data: FormData) => {
    const task_id = await submitDeploy(data);
    const job: DeployJobSummary = {
      taskId:    task_id,
      domain:    data.domain,
      ip:        data.ip,
      newSshPort: parseInt(data.new_ssh_port, 10),
      startedAt: Date.now(),
      savedForm: data,
    };
    // Functional update: derive from the latest committed state, NOT the
    // `jobs` captured in this async closure. The closure can be stale (the
    // modal may have been open while running cards streamed status updates and
    // called setJobs), which previously dropped the new card from the live
    // grid even though it was persisted — so it only showed after an F5.
    setJobs(prev => {
      const updated = [job, ...prev];
      saveJobs(updated);
      return updated;
    });
    setShowForm(false);
    setEditJob(null);
    setExistingPreset(null);
  };

  const retryJob = async (job: DeployJobSummary) => {
    const task_id = await submitDeploy(job.savedForm);
    const newJob: DeployJobSummary = {
      ...job,
      taskId:    task_id,
      startedAt: Date.now(),
      finalStatus: undefined,
    };
    setJobs(prev => {
      const updated = [newJob, ...prev.filter(j => j.taskId !== job.taskId)];
      saveJobs(updated);
      return updated;
    });
  };

  const removeJob = (taskId: string) => {
    setJobs(prev => {
      const updated = prev.filter(j => j.taskId !== taskId);
      saveJobs(updated);
      return updated;
    });
  };

  const updateJobStatus = (taskId: string, status: "success" | "failed") => {
    setJobs(prev => {
      const updated = prev.map(j =>
        j.taskId === taskId ? { ...j, finalStatus: status } : j
      );
      saveJobs(updated);
      return updated;
    });
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-6">

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-base font-semibold text-white">Деплой нод</h1>
            <p className="text-xs text-gray-500 mt-0.5">
              {jobs.length > 0 ? `${jobs.length} задач` : "Нет задач деплоя"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setShowExisting(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                         border transition-colors hover:bg-[var(--bg3)]"
              style={{ borderColor: "var(--line)", color: "var(--t-mid)", background: "var(--bg2)" }}>
              <ServerCog size={13} /> Существующий сервер
            </button>
            <button onClick={() => setShowForm(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                         bg-blue-600 hover:bg-blue-500 text-white transition-colors">
              <Plus size={13} /> Добавить сервер
            </button>
          </div>
        </div>

        {jobs.length === 0 ? (
          <DeployEmptyState onAdd={() => setShowForm(true)} />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
            {jobs.map(job => (
              <DeployCard
                key={job.taskId}
                job={job}
                onRemove={removeJob}
                onEdit={j  => setEditJob(j)}
                onRetry={retryJob}
                onStatusChange={updateJobStatus}
              />
            ))}
          </div>
        )}
      </div>

      {/* New deploy modal — pass NO `initial` so DeployForm pulls global
          deploy-defaults (email, Cloudflare token, XHTTP path, …) into the form. */}
      {showForm && (
        <DeployFormModal
          title="Новый деплой"
          onClose={() => setShowForm(false)}
          onSubmit={addJob}
        />
      )}

      {/* Edit / retry modal */}
      {editJob && (
        <DeployFormModal
          title={`Редактирование: ${editJob.domain}`}
          initial={editJob.savedForm}
          onClose={() => setEditJob(null)}
          onSubmit={addJob}
        />
      )}

      {/* Add-existing-server: detect components, then hand a preset (creds +
          skip_components) to the deploy form (NO `initial` → settings defaults
          still prefill email/Cloudflare/etc). */}
      {showExisting && (
        <ExistingServerModal
          onClose={() => setShowExisting(false)}
          onProceed={preset => { setShowExisting(false); setExistingPreset(preset); }}
        />
      )}
      {existingPreset && (
        <DeployFormModal
          title="Доустановка на существующий сервер"
          preset={existingPreset}
          onClose={() => setExistingPreset(null)}
          onSubmit={addJob}
        />
      )}
    </div>
  );
}

// ── Form modal ────────────────────────────────────────────────

function DeployFormModal({
  title,
  initial,
  preset,
  onClose,
  onSubmit,
}: {
  title:    string;
  initial?: Partial<FormData>;  // omitted for new deploys → settings defaults apply
  preset?:  Partial<FormData>;  // detected creds + skip_components (existing-server flow)
  onClose:  () => void;
  onSubmit: (data: FormData) => Promise<void>;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="rounded-xl w-full max-w-lg
                      max-h-[90vh] overflow-y-auto shadow-2xl"
           style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
             style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <Rocket size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>{title}</h2>
          </div>
          <button onClick={onClose} className="iconbtn">
            <X size={15} />
          </button>
        </div>
        <div className="p-5">
          <DeployForm onSubmit={onSubmit} onCancel={onClose} initial={initial} preset={preset} />
        </div>
      </div>
    </div>
  );
}

// ── Add-existing-server modal (detect + skip checklist) ───────

const DETECT_LABELS: Record<string, string> = {
  node_accelerator: "Node Accelerator",
  trafficguard:     "TrafficGuard",
  remnanode:        "Remnanode",
  masking:          "Маскировочный сайт",
  warp:             "WARP Native",
  hysteria2:        "Hysteria2",
  ssl:              "SSL-сертификат",
  haproxy:          "HAProxy",
};

type DetectStatus = "present" | "absent" | "unknown";

function StatusChip({ status }: { status: DetectStatus }) {
  const map = {
    present: { icon: <CheckCircle2 size={12} />, text: "установлен",  color: "var(--ok)" },
    absent:  { icon: <XCircle size={12} />,      text: "отсутствует", color: "var(--err)" },
    unknown: { icon: <HelpCircle size={12} />,   text: "неизвестно",  color: "var(--warn)" },
  }[status];
  return (
    <span className="inline-flex items-center gap-1 text-[11px] tabular-nums" style={{ color: map.color }}>
      {map.icon} {map.text}
    </span>
  );
}

function ExistingServerModal({ onClose, onProceed }: {
  onClose:   () => void;
  onProceed: (preset: Partial<FormData>) => void;
}) {
  const [ip,          setIp]          = useState("");
  const [sshUser,     setSshUser]     = useState("root");
  const [sshPassword, setSshPassword] = useState("");
  const [sshPort,     setSshPort]     = useState("22");
  const [domain,      setDomain]      = useState("");
  const [detecting,   setDetecting]   = useState(false);
  const [error,       setError]       = useState<string | null>(null);
  const [results,     setResults]     = useState<Record<string, DetectStatus> | null>(null);
  const [skip,        setSkip]        = useState<Record<string, boolean>>({});

  const detect = async () => {
    setDetecting(true); setError(null); setResults(null);
    try {
      const res = await fetch("/api/node/detect", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip, ssh_user: sshUser, ssh_password: sshPassword,
          ssh_port: parseInt(sshPort, 10) || 22, domain,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
      }
      const data = await res.json();
      const r = (data.results ?? {}) as Record<string, DetectStatus>;
      setResults(r);
      // Pre-check the present ones as "skip" — absent/unknown left for the operator.
      const pre: Record<string, boolean> = {};
      Object.entries(r).forEach(([k, v]) => { pre[k] = v === "present"; });
      setSkip(pre);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка определения");
    } finally {
      setDetecting(false);
    }
  };

  const proceed = () => {
    const skip_components = Object.entries(skip).filter(([, v]) => v).map(([k]) => k);
    onProceed({
      ip, ssh_user: sshUser, ssh_password: sshPassword,
      current_ssh_port: sshPort, new_ssh_port: sshPort,
      change_ssh_port: false,   // server is already configured — don't re-do the SSH-port dance
      domain, skip_components,
    });
  };

  const canDetect = !!ip.trim() && !!sshPassword && !detecting;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
           style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
             style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <ServerCog size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>Существующий сервер</h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3">
          <p className="text-xs" style={{ color: "var(--t-low)" }}>
            Определим, какие компоненты уже установлены (только чтение по SSH), затем доустановим недостающее.
          </p>

          <div className="grid grid-cols-2 gap-3">
            <FieldLite label="IP-адрес" value={ip} onChange={setIp} placeholder="1.2.3.4" />
            <FieldLite label="SSH логин" value={sshUser} onChange={setSshUser} placeholder="root" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <FieldLite label="SSH пароль" value={sshPassword} onChange={setSshPassword} type="password" />
            <FieldLite label="SSH порт" value={sshPort} onChange={setSshPort} placeholder="22" />
          </div>
          <FieldLite label="Домен ноды (для проверки SSL)" value={domain} onChange={setDomain}
            placeholder="node1.example.com (опционально)" />

          <button type="button" onClick={detect} disabled={!canDetect}
            className="flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium
                       border transition-colors hover:bg-[var(--bg3)] disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ borderColor: "var(--line)", color: "var(--t-mid)", background: "var(--bg2)" }}>
            {detecting ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            Определить компоненты
          </button>

          {error && <p className="errmsg">{error}</p>}

          {results && (
            <div className="rounded-lg border p-3 flex flex-col gap-2"
                 style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
              <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--t-faint)" }}>
                Отметьте, что пропустить (не переустанавливать)
              </p>
              {Object.entries(results).map(([comp, status]) => (
                <label key={comp} className="flex items-center gap-2.5 cursor-pointer py-0.5">
                  <input
                    type="checkbox"
                    checked={!!skip[comp]}
                    onChange={() => setSkip(s => ({ ...s, [comp]: !s[comp] }))}
                    className="accent-[var(--accent)]"
                  />
                  <span className="text-sm flex-1" style={{ color: "var(--t-mid)" }}>
                    {DETECT_LABELS[comp] ?? comp}
                  </span>
                  <StatusChip status={status} />
                </label>
              ))}
              <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
                «неизвестно» — определить не удалось, решение за вами. Непропущенные компоненты будут установлены заново.
              </p>
            </div>
          )}

          <div className="mt-1 flex gap-2">
            <button type="button" onClick={proceed} disabled={!results}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                         font-semibold text-sm transition-all bg-[var(--accent)] text-[var(--primary-ink)]
                         hover:bg-[var(--accent-hi)] disabled:bg-[var(--accent-dim)] disabled:cursor-not-allowed">
              <Rocket size={15} /> Продолжить к деплою
            </button>
            <button type="button" onClick={onClose}
              className="px-4 py-2.5 rounded-lg text-sm font-medium
                         text-[var(--t-low)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors">
              Отмена
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Minimal labelled input for the detect modal (the full DeployForm's Field is
// keyed to FormData names — this one takes a free-form string setter).
function FieldLite({ label, value, onChange, placeholder, type = "text" }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className="input transition-colors"
      />
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────

function DeployEmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <Rocket size={40} className="mb-4" style={{ color: "var(--t-faint)" }} />
      <p className="text-sm mb-1" style={{ color: "var(--t-low)" }}>Нет задач деплоя</p>
      <p className="text-xs mb-5" style={{ color: "var(--t-faint)" }}>Нажмите «Добавить сервер» чтобы запустить деплой ноды</p>
      <button onClick={onAdd}
        className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm btn btn-primary">
        <Plus size={14} /> Добавить сервер
      </button>
    </div>
  );
}
