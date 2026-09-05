import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Plus, Rocket, X, ServerCog, Search, Loader2,
  CheckCircle2, XCircle, HelpCircle,
} from "lucide-react";
import { DeployCard } from "./DeployCard";
import { DeployForm, type FormData } from "./DeployForm";
import { deployJobsKey } from "../auth/store";
import { Page, Seg, EmptyState } from "../theme/ui";
import { Stagger, StaggerItem } from "../theme/motion";
import {
  fetchDeployJobs, upsertDeployJob, deleteDeployJob, reconcileJobs,
} from "./deployJobsSync";
import { toast } from "./infra/Toast";

export interface DeployJobSummary {
  taskId:     string;
  domain:     string;
  ip:         string;
  newSshPort: number;
  startedAt:  number;
  savedForm:  FormData;          // full form data for retry / edit
  finalStatus?: "success" | "failed";
  color?:     string;            // цветовая маркировка виджета (Wave-4 PR-9)
}

// S2 toolbar: локальный фильтр по статусу (running = ещё без finalStatus).
type StatusFilter = "all" | "running" | "success" | "failed";

function loadJobs(): DeployJobSummary[] {
  try { return JSON.parse(localStorage.getItem(deployJobsKey()) ?? "[]"); }
  catch { return []; }
}

function saveJobs(jobs: DeployJobSummary[]) {
  try { localStorage.setItem(deployJobsKey(), JSON.stringify(jobs)); }
  catch {}
}

// localStorage is now only a "pending" buffer: cards that were changed locally but
// not yet confirmed by the server. (Confirmed cards live on the server and come
// back via GET.) bufferUpsert/bufferRemove mutate just that pending set.
function bufferUpsert(job: DeployJobSummary) {
  saveJobs([...loadJobs().filter(j => j.taskId !== job.taskId), job]);
}

function bufferRemove(taskId: string) {
  saveJobs(loadJobs().filter(j => j.taskId !== taskId));
}

export function DeployDashboard() {
  const [jobs,     setJobs]     = useState<DeployJobSummary[]>(loadJobs);
  const [showForm, setShowForm] = useState(false);
  const [editJob,  setEditJob]  = useState<DeployJobSummary | null>(null);
  // Add-existing-server flow: `showExisting` = the detect/checklist modal;
  // `existingPreset` = detected creds + positive install_components selection.
  const [showExisting,   setShowExisting]   = useState(false);
  const [existingPreset, setExistingPreset] = useState<Partial<FormData> | null>(null);

  // Unsynced-state flag: true once a server push has failed, so we only toast
  // once per outage (and the next successful push clears it).
  const dirtyRef = useRef(false);

  // S2 toolbar: локальный поиск + фильтр статуса. Фильтрация идёт только по уже
  // загруженным джобам — сервер-источник и синк (deployJobsSync) не затрагиваются.
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return jobs.filter(j => {
      if (statusFilter === "success" && j.finalStatus !== "success") return false;
      if (statusFilter === "failed"  && j.finalStatus !== "failed")  return false;
      if (statusFilter === "running" && j.finalStatus)               return false;
      if (q && !`${j.domain} ${j.ip}`.toLowerCase().includes(q))     return false;
      return true;
    });
  }, [jobs, query, statusFilter]);

  const notifyDirty = useCallback(() => {
    if (!dirtyRef.current) {
      toast("Не удалось синхронизировать карточки с сервером. Изменения сохранены локально.", "error");
    }
    dirtyRef.current = true;
  }, []);

  // Sync a mutation's diff to the server WITHOUT a full-list PUT: removed taskIds
  // are DELETE'd, added/changed cards are upserted one at a time. This keeps the
  // server as the source of truth — a client with a stale local list can never
  // clobber cards another browser pushed. A change is buffered in localStorage
  // (pending) first, so a failed op survives a reload; a confirmed upsert drains
  // its taskId from the buffer.
  const syncDiff = useCallback((prev: DeployJobSummary[], next: DeployJobSummary[]) => {
    // Reference identity is enough: mutations keep untouched cards as the SAME
    // object and build a fresh object only for the cards they touch.
    const removed = prev.filter(p => !next.includes(p));
    const changed = next.filter(n => !prev.includes(n));

    for (const j of removed) {
      bufferRemove(j.taskId);
      deleteDeployJob(j.taskId)
        .then(() => { dirtyRef.current = false; })
        .catch(notifyDirty);
    }
    for (const j of changed) {
      bufferUpsert(j);
      upsertDeployJob(j)
        .then(() => { dirtyRef.current = false; bufferRemove(j.taskId); })
        .catch(notifyDirty);
    }
  }, [notifyDirty]);

  // Single mutation path: derive the next list from the latest committed state
  // (NOT a possibly-stale closure), then sync the diff to the server. Keeps the
  // functional-update fix from addJob/retryJob for every mutation (remove/color/
  // status too).
  const commitJobs = useCallback((updater: (prev: DeployJobSummary[]) => DeployJobSummary[]) => {
    setJobs(prev => {
      const next = updater(prev);
      syncDiff(prev, next);
      return next;
    });
  }, [syncDiff]);

  // On mount, load the authoritative list from the server. localStorage is only an
  // offline buffer here:
  //  - the server list is rendered as-is (server is the source of truth);
  //  - local-only taskIds (cards that never reached the server) are pushed one at
  //    a time and, once confirmed, drained from the buffer — always, not only when
  //    the server list is empty;
  //  - server unreachable (network/5xx/401) -> degrade to the buffer (the initial
  //    state is already loadJobs(); nothing to do).
  useEffect(() => {
    let live = true;
    (async () => {
      let serverJobs: DeployJobSummary[];
      try {
        serverJobs = await fetchDeployJobs<DeployJobSummary>();
      } catch {
        return; // offline — the buffer is already the render list.
      }
      if (!live) return;
      const local = loadJobs();
      const { merged, localOnly } = reconcileJobs(serverJobs, local);
      if (localOnly.length > 0) {
        const confirmed = new Set<string>();
        await Promise.all(localOnly.map(async j => {
          try {
            await upsertDeployJob(j);
            confirmed.add(j.taskId);
            dirtyRef.current = false;
          } catch {
            notifyDirty();
          }
        }));
        if (!live) return;
        // Drain the confirmed cards from the buffer; keep only still-pending ones.
        saveJobs(localOnly.filter(j => !confirmed.has(j.taskId)));
      } else {
        saveJobs([]);
      }
      if (live) setJobs(merged);
    })();
    return () => { live = false; };
  }, [notifyDirty]);

  const submitDeploy = useCallback(async (data: FormData): Promise<string> => {
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
  }, []);

  const addJob = useCallback(async (data: FormData) => {
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
    commitJobs(prev => [job, ...prev]);
    setShowForm(false);
    setEditJob(null);
    setExistingPreset(null);
  }, [submitDeploy, commitJobs]);

  const retryJob = useCallback(async (job: DeployJobSummary) => {
    const task_id = await submitDeploy(job.savedForm);
    const newJob: DeployJobSummary = {
      ...job,
      taskId:    task_id,
      startedAt: Date.now(),
      finalStatus: undefined,
    };
    commitJobs(prev => [newJob, ...prev.filter(j => j.taskId !== job.taskId)]);
  }, [submitDeploy, commitJobs]);

  const restartWaitingJob = useCallback(async (job: DeployJobSummary) => {
    const res = await fetch("/api/deploy/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: job.taskId }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
    }
    const { task_id } = await res.json();
    const replacement: DeployJobSummary = {
      ...job, taskId: task_id, startedAt: Date.now(), finalStatus: undefined,
    };
    commitJobs(prev => {
      // The backend returns the same replacement for duplicate requests. Filter
      // both ids so a double-click can never create duplicate cards either.
      return [replacement, ...prev.filter(
        j => j.taskId !== job.taskId && j.taskId !== task_id,
      )];
    });
  }, [commitJobs]);

  const removeJob = useCallback((taskId: string) => {
    commitJobs(prev => prev.filter(j => j.taskId !== taskId));
  }, [commitJobs]);

  const updateJobStatus = useCallback((taskId: string, status: "success" | "failed") => {
    commitJobs(prev => prev.map(j =>
      j.taskId === taskId ? { ...j, finalStatus: status } : j
    ));
  }, [commitJobs]);

  // Цветовая маркировка виджета ноды (Wave-4 PR-9).
  const changeJobColor = useCallback((taskId: string, colorKey: string | null) => {
    commitJobs(prev => prev.map(j => {
      if (j.taskId !== taskId) return j;
      const c = { ...j };
      if (colorKey) c.color = colorKey;
      else delete c.color;
      return c;
    }));
  }, [commitJobs]);

  return (
    <Page max={1152}>

        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-lg font-semibold text-[var(--t-hi)]" style={{ lineHeight: 1.3 }}>Деплой нод</h1>
            <p className="text-xs text-[var(--t-faint)] mt-1">
              {jobs.length > 0 ? `${jobs.length} задач` : "Нет задач деплоя"}
            </p>
            {/* Карточки хранятся на сервере (зашифрованы Fernet-хранилищем) и
                синхронизируются между устройствами этого аккаунта. Следствие:
                коллега на другом аккаунте своих карточек здесь не увидит — его
                список лежит в его собственном аккаунте. Общая картина серверная:
                «Доступность серверов» и ноды Remnawave. */}
            <p className="text-[11.5px] text-[var(--t-faint)] mt-1.5" style={{ lineHeight: 1.55 }}>
              Карточки хранятся на сервере и синхронизируются между устройствами
              вашего аккаунта. Общий список — на «Доступности серверов».
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setShowExisting(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                         border transition-colors hover:bg-[var(--bg3)]"
              style={{ borderColor: "var(--line)", color: "var(--t-mid)", background: "var(--bg2)" }}>
              <ServerCog size={13} /> Существующий сервер
            </button>
          </div>
        </div>

        {jobs.length === 0 ? (
          <EmptyState
            icon={<Rocket size={18} />}
            title="Нет задач деплоя"
            hint="Задеплойте первую ноду или добавьте существующий сервер — карточки появятся здесь."
            action={
              <button onClick={() => setShowForm(true)}
                className="btn btn-primary" style={{ marginTop: 6 }}>
                <Plus size={14} /> Добавить сервер
              </button>
            }
          />
        ) : (
          <>
            {/* S2 toolbar: поиск (ip/имя/домен) + фильтр статуса + «Новая нода». */}
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <div className="relative flex-1 min-w-[200px]">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--t-faint)]" />
                <input
                  className="input pl-8"
                  placeholder="Поиск по IP, имени или домену"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  aria-label="Поиск нод"
                />
              </div>
              <Seg
                mini
                options={[
                  { v: "all",     l: "Все" },
                  { v: "running", l: "В работе" },
                  { v: "success", l: "Успех" },
                  { v: "failed",  l: "Ошибка" },
                ]}
                value={statusFilter}
                onChange={v => setStatusFilter(v as StatusFilter)}
              />
              <button onClick={() => setShowForm(true)} className="btn btn-primary">
                <Plus size={13} /> Новая нода
              </button>
            </div>

            {filtered.length === 0 ? (
              <EmptyState
                icon={<Search size={18} />}
                title="Ничего не найдено"
                hint="Измените запрос поиска или фильтр статуса."
                action={
                  <button
                    onClick={() => { setQuery(""); setStatusFilter("all"); }}
                    className="btn btn-soft" style={{ marginTop: 6 }}>
                    Сбросить фильтры
                  </button>
                }
              />
            ) : (
              <Stagger className="ni-deploy-grid">
                {filtered.map(job => (
                  <StaggerItem key={job.taskId}>
                    <DeployCard
                      job={job}
                      onRemove={removeJob}
                      onEdit={j  => setEditJob(j)}
                      onRetry={retryJob}
                      onRestart={restartWaitingJob}
                      onStatusChange={updateJobStatus}
                      onColorChange={changeJobColor}
                    />
                  </StaggerItem>
                ))}
              </Stagger>
            )}
          </>
        )}

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
          install_components) to the deploy form (NO `initial` → settings defaults
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
    </Page>
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
  preset?:  Partial<FormData>;  // detected creds + install_components (existing-server flow)
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

// ── Add-existing-server modal (detect + positive install checklist) ───────

const DETECT_LABELS: Record<string, string> = {
  node_accelerator: "Node Accelerator",
  trafficguard:     "TrafficGuard",
  test_tools:       "Тест-инструменты",
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
  const [install,     setInstall]     = useState<Record<string, boolean>>({});
  // Detected current settings (ssh_port/remnanode_port/xhttp_path/domain/open_ports/
  // has_token) — mapped into the deploy form as a preset (Wave-4 Plan B Ф2).
  const [settings,    setSettings]    = useState<Record<string, string | number | boolean> | null>(null);

  const detect = async () => {
    setDetecting(true); setError(null); setResults(null); setSettings(null);
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
      // Positive direction: absent components are the likely install targets;
      // present/unknown stay off until the operator explicitly selects them.
      const pre: Record<string, boolean> = {};
      Object.entries(r).forEach(([k, v]) => { pre[k] = v === "absent"; });
      setInstall(pre);
      // Detected settings → prefill (secrets never returned; only has_token).
      const s = (data.settings ?? {}) as Record<string, string | number | boolean>;
      setSettings(s);
      if (s.domain && !domain.trim()) setDomain(String(s.domain));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка определения");
    } finally {
      setDetecting(false);
    }
  };

  const proceed = () => {
    const install_components = Object.entries(install).filter(([, v]) => v).map(([k]) => k);
    const s = settings ?? {};
    const preset: Partial<FormData> = {
      mode: install_components.includes("haproxy") ? "haproxy" : "remnanode",
      ip, ssh_user: sshUser, ssh_password: sshPassword,
      current_ssh_port: sshPort, new_ssh_port: sshPort,
      change_ssh_port: false,   // server is already configured — don't re-do the SSH-port dance
      domain: domain || (s.domain ? String(s.domain) : ""),
      skip_components: [],
      install_components,
      // Optional install flags mirror the positive selection. The pipeline treats
      // install_components as authoritative; keeping these flags aligned also
      // makes saved-form retries and cards accurately describe the request.
      optimize: install_components.includes("node_accelerator"),
      install_trafficguard: install_components.includes("trafficguard"),
      install_test_tools: install_components.includes("test_tools"),
      install_warp: install_components.includes("warp"),
      install_psiphon: install_components.includes("psiphon"),
      install_reshala: install_components.includes("reshala"),
      install_hysteria2: install_components.includes("hysteria2"),
      install_vnstat: false,
    };
    // Prefill detected values (form's preset wins over settings-defaults).
    if (s.remnanode_port) preset.remnanode_port = String(s.remnanode_port);
    if (s.xhttp_path)     preset.xhttp_path     = String(s.xhttp_path);
    if (s.open_ports)     preset.open_ports     = String(s.open_ports);
    onProceed(preset);
  };

  // Rows for the "detected settings" summary shown before proceeding.
  const detectedRows: [string, string][] = [];
  if (settings) {
    const push = (k: string, v: unknown) => {
      if (v !== undefined && v !== null && v !== "") detectedRows.push([k, String(v)]);
    };
    push("SSH-порт", settings.ssh_port);
    push("Порт remnanode", settings.remnanode_port);
    push("Путь XHTTP", settings.xhttp_path);
    push("Домен", settings.domain);
    push("Порты UFW", settings.open_ports);
    if (settings.has_token) detectedRows.push(["Токен ноды", "уже на сервере"]);
  }

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
                Что доустановить
              </p>
              {Object.entries(results).map(([comp, status]) => (
                <label key={comp} className="flex items-center gap-2.5 cursor-pointer py-0.5">
                  <input
                    type="checkbox"
                    checked={!!install[comp]}
                    onChange={() => setInstall(s => ({ ...s, [comp]: !s[comp] }))}
                    className="accent-[var(--accent)]"
                  />
                  <span className="text-sm flex-1" style={{ color: "var(--t-mid)" }}>
                    {DETECT_LABELS[comp] ?? comp}
                  </span>
                  <StatusChip status={status} />
                </label>
              ))}
              <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
                «неизвестно» — определить не удалось. Установлены будут только отмеченные компоненты.
              </p>
            </div>
          )}

          {detectedRows.length > 0 && (
            <div className="rounded-lg border p-3 flex flex-col gap-1.5"
                 style={{ borderColor: "var(--accent-dim)", background: "var(--bg2)" }}>
              <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--accent-hi)" }}>
                Обнаружено на сервере — подставится в форму
              </p>
              {detectedRows.map(([k, v]) => (
                <div key={k} className="flex items-center justify-between text-xs">
                  <span style={{ color: "var(--t-low)" }}>{k}</span>
                  <span className="tabular-nums" style={{ color: "var(--t-hi)" }}>{v}</span>
                </div>
              ))}
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
