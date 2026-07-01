import { useState, useCallback } from "react";
import { CheckCircle2, XCircle, Terminal as TermIcon } from "lucide-react";
import { Sidebar, type Tab }               from "./components/Sidebar";
import { Dashboard }                       from "./components/Dashboard";
import { DeployDashboard }                 from "./components/DeployDashboard";
import { Settings }                        from "./components/Settings";
import { Templates }                       from "./components/Templates";
import { CertsForm, type CertsFormData }  from "./components/CertsForm";
import { TrafficRules }                   from "./components/TrafficRules";
import { InfraProviders }                 from "./components/infra/InfraProviders";
import { InfraBillingNodes }              from "./components/infra/InfraBillingNodes";
import { InfraHistory }                   from "./components/infra/InfraHistory";
import { InfraAnalytics }                 from "./components/infra/InfraAnalytics";
import { Toaster }                        from "./components/infra/Toast";
import { StepProgress, RENEW_STEPS }       from "./components/StepProgress";
import { TerminalOutput }                  from "./components/TerminalOutput";
import { useTaskStream, type StatusFrame } from "./hooks/useTaskStream";

const SIDEBAR_KEY = "sidebar_collapsed";

const INITIAL_CERT_STATUS: StatusFrame = {
  status:       "pending",
  current_step: 0,
  total_steps:  RENEW_STEPS.length,
};

export default function App() {
  // ── Sidebar ────────────────────────────────────────────────
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) === "1"; }
    catch { return false; }
  });

  const toggleSidebar = () =>
    setSidebarCollapsed(v => {
      const next = !v;
      try { localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0"); } catch {}
      return next;
    });

  // ── Certs task state ───────────────────────────────────────
  const [certTaskId,     setCertTaskId]     = useState<string | null>(null);
  const [certLogs,       setCertLogs]       = useState<string[]>([]);
  const [certStepStatus, setCertStepStatus] = useState<StatusFrame>(INITIAL_CERT_STATUS);

  const addCertLog   = useCallback((line: string) => setCertLogs(l => [...l, line]), []);
  const onCertStatus = useCallback((frame: StatusFrame) =>
    setCertStepStatus(prev => ({
      status:       frame.status,
      current_step: frame.current_step === -1 ? prev.current_step : frame.current_step,
      total_steps:  frame.total_steps  === -1 ? prev.total_steps  : frame.total_steps,
    })), []);

  useTaskStream({ taskId: certTaskId, onLog: addCertLog, onStatus: onCertStatus });

  const certIsRunning =
    certStepStatus.status === "running" ||
    (certStepStatus.status === "pending" && certTaskId !== null);
  const certIsDone =
    certStepStatus.status === "success" || certStepStatus.status === "failed";

  const renewCerts = async (data: CertsFormData) => {
    setCertLogs([]);
    setCertTaskId(null);
    setCertStepStatus(INITIAL_CERT_STATUS);
    const res = await fetch("/api/certs/renew", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...data,
        ssh_port:   parseInt(data.ssh_port, 10),
        cf_api_key: data.cf_api_key || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      addCertLog(`\x1b[31m[HTTP ${res.status}] ${JSON.stringify(err.detail ?? err)}\x1b[0m`);
      return;
    }
    const { task_id } = await res.json();
    setCertTaskId(task_id);
  };

  // ── Render ─────────────────────────────────────────────────
  return (
    <div className="h-screen bg-gray-950 flex overflow-hidden">
      {/* Global toast stack (used by the infra-billing pages) */}
      <Toaster />
      <Sidebar
        activeTab={tab}
        onTabChange={setTab}
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
      />

      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">

        {/* ── Dashboard ── */}
        {tab === "dashboard" && <Dashboard />}

        {/* ── Deploy dashboard ── */}
        {tab === "deploy" && <DeployDashboard />}

        {/* ── Templates ── */}
        {tab === "templates" && <Templates />}

        {/* ── Settings ── */}
        {tab === "settings" && <Settings />}

        {/* ── Traffic Rules ── */}
        {tab === "traffic" && <TrafficRules />}

        {/* ── Инфра-биллинг ── */}
        {tab === "infra-providers" && <InfraProviders />}
        {tab === "infra-nodes"     && <InfraBillingNodes />}
        {tab === "infra-history"   && <InfraHistory />}
        {tab === "infra-analytics" && <InfraAnalytics />}

        {/* ── Certs ── */}
        {tab === "certs" && (
          <>
            {/* Thin status bar */}
            <div className="shrink-0 h-11 border-b border-gray-800/80 px-4 flex items-center gap-3">
              <span className="text-sm font-medium text-white">Обновить SSL</span>
              {certTaskId && (
                <div className="ml-auto">
                  {certStepStatus.status === "success" && (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                                     bg-green-950/60 border border-green-800/50
                                     text-green-400 text-xs font-medium">
                      <CheckCircle2 size={11} /> Успешно
                    </span>
                  )}
                  {certStepStatus.status === "failed" && (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                                     bg-red-950/60 border border-red-800/50
                                     text-red-400 text-xs font-medium">
                      <XCircle size={11} /> Ошибка
                    </span>
                  )}
                  {certIsRunning && (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
                                     bg-blue-950/60 border border-blue-800/50
                                     text-blue-400 text-xs font-medium">
                      <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                      Шаг {certStepStatus.current_step}/{certStepStatus.total_steps}
                    </span>
                  )}
                </div>
              )}
            </div>

            {/* Split: form+stepper | terminal */}
            <div className="flex-1 grid grid-cols-[360px_1fr] min-h-0">

              {/* Left */}
              <div className="border-r border-gray-800/80 flex flex-col overflow-y-auto">
                <div className="p-5">
                  <CertsForm onSubmit={renewCerts} disabled={certIsRunning} />
                </div>
                {certTaskId && (
                  <div className="px-5 pb-5 border-t border-gray-800/60 pt-4">
                    <p className="text-[11px] font-medium text-gray-600 uppercase tracking-widest mb-3">
                      Прогресс
                    </p>
                    <StepProgress
                      currentStep={certStepStatus.current_step}
                      totalSteps={certStepStatus.total_steps}
                      status={certStepStatus.status}
                      steps={RENEW_STEPS}
                    />
                  </div>
                )}
              </div>

              {/* Right: terminal */}
              <div className="flex flex-col min-h-0">
                <div className="shrink-0 px-4 py-2 border-b border-gray-800/60
                                flex items-center gap-2">
                  <TermIcon size={13} className="text-gray-600" />
                  <span className="text-[11px] text-gray-600 uppercase tracking-widest font-medium">
                    Вывод терминала
                  </span>
                  {certLogs.length > 0 && (
                    <span className="ml-auto text-[11px] text-gray-700 tabular-nums">
                      {certLogs.length} строк
                    </span>
                  )}
                </div>

                {certIsDone && (
                  <div className={`shrink-0 flex items-center gap-3 px-4 py-2.5
                                   text-sm border-b ${
                    certStepStatus.status === "success"
                      ? "bg-green-950/40 border-green-800/40 text-green-300"
                      : "bg-red-950/40  border-red-800/40  text-red-300"
                  }`}>
                    {certStepStatus.status === "success"
                      ? <><CheckCircle2 size={15} className="shrink-0" /> Сертификаты обновлены</>
                      : <><XCircle size={15} className="shrink-0" /> Ошибка выполнения</>
                    }
                  </div>
                )}

                <div className="flex-1 p-3 min-h-0">
                  {certLogs.length === 0 && !certTaskId ? (
                    <div className="h-full flex flex-col items-center justify-center gap-2
                                    text-gray-700 text-sm border border-gray-800/50 rounded-lg">
                      <TermIcon size={28} className="opacity-30" />
                      <span>Заполните форму и нажмите «Обновить сертификаты»</span>
                    </div>
                  ) : (
                    <TerminalOutput lines={certLogs} />
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
