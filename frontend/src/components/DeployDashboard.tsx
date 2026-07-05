import { useState } from "react";
import { Plus, Rocket, X } from "lucide-react";
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
          <button onClick={() => setShowForm(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                       bg-blue-600 hover:bg-blue-500 text-white transition-colors">
            <Plus size={13} /> Добавить сервер
          </button>
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
    </div>
  );
}

// ── Form modal ────────────────────────────────────────────────

function DeployFormModal({
  title,
  initial,
  onClose,
  onSubmit,
}: {
  title:    string;
  initial?: Partial<FormData>;  // omitted for new deploys → settings defaults apply
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
          <DeployForm onSubmit={onSubmit} onCancel={onClose} initial={initial} />
        </div>
      </div>
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
