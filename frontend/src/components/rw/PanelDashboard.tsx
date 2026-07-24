import { useMemo, useState } from "react";
import { SyncGroupPanel } from "./SyncGroupPanel";
import { PanelRegistry } from "../common/PanelRegistry";
import { Plus, ServerCog, X } from "lucide-react";
import { PanelWidget } from "./PanelWidget";
import { PanelManageModal } from "./PanelManageModal";
import { PanelDeployForm, type PanelDeployPayload } from "./PanelDeployForm";
import { panelJobsKey } from "../../auth/store";

// Ф6 — Remnawave panel/subscription install dashboard. Widgets live in
// localStorage panel_jobs_<id> (SSH creds included, client-only — the server
// gets them per-request and never persists them). Mirrors DeployDashboard's
// functional-setState pattern so a new widget appears immediately (no F5).

export interface PanelJobSummary {
  id:           string;               // stable id — survives retry (taskId does not)
  taskId:       string;
  savedForm:    PanelDeployPayload;   // the exact PanelDeployRequest that was sent
  createdAt:    number;
  target:       PanelDeployPayload["target"];
  finalStatus?: "success" | "failed";
}

// A stable id so Ф7/Ф8/Ф9 can address a panel independently of its current
// task-stream (retry mints a new taskId; the id stays put).
function newId(): string {
  try { return crypto.randomUUID(); } catch { return `p${Date.now()}${Math.floor(Math.random() * 1e6)}`; }
}

function loadJobs(): PanelJobSummary[] {
  try { return JSON.parse(localStorage.getItem(panelJobsKey()) ?? "[]"); }
  catch { return []; }
}
function saveJobs(jobs: PanelJobSummary[]) {
  try { localStorage.setItem(panelJobsKey(), JSON.stringify(jobs)); }
  catch {}
}

export function PanelDashboard() {
  const [jobs,      setJobs]      = useState<PanelJobSummary[]>(loadJobs);
  const [showForm,  setShowForm]  = useState(false);
  const [manageJob, setManageJob] = useState<PanelJobSummary | null>(null);

  // Most recent successfully deployed panel — used to prefill a registry entry.
  // `panel_jobs` stays client-only (it holds SSH creds); only the URL crosses over.
  // `target: "subpage"` deploys no panel at all, hence the panel_domain check.
  const deployed = useMemo(() => {
    const ok = jobs.filter(j => j.finalStatus === "success" && j.savedForm?.panel_domain);
    return ok.length ? ok[ok.length - 1] : null;
  }, [jobs]);
  const deployedUrl = deployed ? `https://${deployed.savedForm.panel_domain}` : "";
  const deployedName = deployed?.savedForm.panel_domain || "";

  const submit = async (payload: PanelDeployPayload): Promise<string> => {
    const res = await fetch("/api/panel/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    const { task_id } = await res.json();
    return task_id as string;
  };

  const addJob = async (payload: PanelDeployPayload) => {
    const task_id = await submit(payload);
    const job: PanelJobSummary = {
      id: newId(), taskId: task_id, savedForm: payload, createdAt: Date.now(), target: payload.target,
    };
    // Functional update — derive from the latest committed state, not the closure
    // snapshot (running widgets stream status and call setJobs meanwhile). Same
    // fix as DeployDashboard: without this the card only showed after an F5.
    setJobs(prev => {
      const updated = [job, ...prev];
      saveJobs(updated);
      return updated;
    });
    setShowForm(false);
  };

  const retryJob = async (job: PanelJobSummary) => {
    const task_id = await submit(job.savedForm);
    const newJob: PanelJobSummary = { ...job, taskId: task_id, createdAt: Date.now(), finalStatus: undefined };
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
      // Bail out if the status is already set — returning the SAME reference
      // lets React skip the re-render, breaking the widget effect's
      // render→setJobs→new-callback→effect loop for finished cards.
      if (prev.find(j => j.taskId === taskId)?.finalStatus === status) return prev;
      const updated = prev.map(j => (j.taskId === taskId ? { ...j, finalStatus: status } : j));
      saveJobs(updated);
      return updated;
    });
  };

  // Ф7 — patch a job's saved server-data (ip / domains / ssh) in place. Keyed by
  // the STABLE id (not taskId — a retry mints a new taskId, the id survives). The
  // open manage modal is re-pointed at the fresh record so it reflects the edit.
  const editJob = (updated: PanelJobSummary) => {
    setJobs(prev => {
      const next = prev.map(j => (j.id === updated.id ? updated : j));
      saveJobs(next);
      return next;
    });
    setManageJob(updated);
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-6 ni-pagebody">

        <div className="flex items-center justify-between mb-6 ni-pagehead">
          <div>
            <h1 className="text-base font-semibold text-[var(--t-hi)]">Установка панели</h1>
            <p className="text-xs text-[var(--t-low)] mt-0.5">
              {jobs.length > 0 ? `${jobs.length} установок` : "Нет установок"}
            </p>
          </div>
          <div className="ni-pagehead-actions">
            <button onClick={() => setShowForm(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors">
              <Plus size={13} /> Установить панель
            </button>
          </div>
        </div>

        {/* The SAME registry the settings screen edits — «сделать главной» here
            and there hit one endpoint, so the two screens cannot disagree.
            `prefill` seeds a new entry from the last successfully deployed panel;
            the API token is left blank on purpose (it is issued in the panel
            itself and we never persist the deploy-time one). */}
        <div className="mb-5">
          <PanelRegistry
            addLabel={deployedUrl ? "+ Из развёрнутой" : "+ Панель"}
            prefill={deployedUrl ? { name: deployedName, panel_url: deployedUrl } : undefined}
            hint="Главная панель общая с «Настройками»: её используют деплой нод, конфиги и ассистент."
          />
        </div>

        {jobs.length === 0 ? (
          <EmptyState onAdd={() => setShowForm(true)} />
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {jobs.map(job => (
              <PanelWidget
                key={job.taskId}
                job={job}
                onRemove={removeJob}
                onRetry={retryJob}
                onStatusChange={updateJobStatus}
                onManage={setManageJob}
              />
            ))}
          </div>
        )}

        {jobs.length > 0 && (
          <div className="mt-8 pt-6 border-t border-[var(--line-soft)]">
            <SyncGroupPanel jobs={jobs} />
          </div>
        )}
      </div>

      {showForm && (
        <FormModal onClose={() => setShowForm(false)} onSubmit={addJob} />
      )}

      {manageJob && (
        <PanelManageModal
          key={manageJob.id}
          job={manageJob}
          onClose={() => setManageJob(null)}
          onEditJob={editJob}
        />
      )}
    </div>
  );
}

function FormModal({ onClose, onSubmit }: {
  onClose: () => void; onSubmit: (payload: PanelDeployPayload) => Promise<void>;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
        style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <ServerCog size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>Установка Remnawave</h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>
        <div className="p-5">
          <PanelDeployForm onSubmit={onSubmit} onCancel={onClose} />
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <ServerCog size={40} className="mb-4" style={{ color: "var(--t-faint)" }} />
      <p className="text-sm mb-1" style={{ color: "var(--t-low)" }}>Нет установленных панелей</p>
      <p className="text-xs mb-5" style={{ color: "var(--t-faint)" }}>
        Нажмите «Установить панель» чтобы развернуть Remnawave или страницу подписок
      </p>
      <button onClick={onAdd}
        className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm btn btn-primary">
        <Plus size={14} /> Установить панель
      </button>
    </div>
  );
}
