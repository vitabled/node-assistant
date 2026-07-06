import { useState, useEffect, useCallback } from "react";
import {
  CheckCircle2, XCircle, Terminal as TermIcon, ChevronRight,
} from "lucide-react";
import { Sidebar, type Tab }               from "./components/Sidebar";
import { Dashboard }                       from "./components/Dashboard";
import { DeployDashboard }                 from "./components/DeployDashboard";
import { Settings }                        from "./components/Settings";
import { Templates }                       from "./components/Templates";
import { Hosts }                           from "./components/Hosts";
import { CertsForm, type CertsFormData }  from "./components/CertsForm";
import { DomainsPanel }                    from "./components/DomainsPanel";
import { TrafficRules }                   from "./components/TrafficRules";
import { InfraDashboard }                 from "./components/infra/InfraDashboard";
import { InfraProviders }                 from "./components/infra/InfraProviders";
import { InfraProjects }                  from "./components/infra/InfraProjects";
import { InfraServices }                  from "./components/infra/InfraServices";
import { InfraPayments }                  from "./components/infra/InfraPayments";
import { InfraSettings }                  from "./components/infra/InfraSettings";
import { InfraApiTokens }                 from "./components/infra/InfraApiTokens";
import { Toaster }                        from "./components/infra/Toast";
import { StepProgress, RENEW_STEPS }       from "./components/StepProgress";
import { TerminalOutput }                  from "./components/TerminalOutput";
import { useTaskStream, type StatusFrame } from "./hooks/useTaskStream";
import { AccountMenu }                     from "./auth/AccountMenu";
import { tabKey, getActiveId }             from "./auth/store";
import {
  applyAccent, applyDensity, applyThemeMode, loadAccent, loadDensity, loadThemeMode,
} from "./theme/tweaks";

const SIDEBAR_KEY = "sidebar_collapsed";

const INITIAL_CERT_STATUS: StatusFrame = {
  status: "pending", current_step: 0, total_steps: RENEW_STEPS.length,
};

// Breadcrumb labels per tab.
const CRUMB: Record<Tab, [string, string]> = {
  "dashboard":       ["Node Installer", "Дешборд"],
  "deploy":          ["Node Installer", "Деплой ноды"],
  "certs":           ["Node Installer", "Управление SSL"],
  "templates":       ["Node Installer", "Шаблоны"],
  "hosts":           ["Node Installer", "Хосты"],
  "traffic":         ["Node Installer", "Трафик"],
  "settings":        ["Node Installer", "Настройки"],
  "infra-dashboard": ["Инфра-биллинг", "Dashboard"],
  "infra-providers": ["Инфра-биллинг", "Провайдеры"],
  "infra-projects":  ["Инфра-биллинг", "Проекты"],
  "infra-services":  ["Инфра-биллинг", "Услуги и тарифы"],
  "infra-payments":  ["Инфра-биллинг", "Платежи"],
  "infra-settings":  ["Инфра-биллинг", "Настройки биллинга"],
  "infra-tokens":    ["Инфра-биллинг", "API токены"],
};

export default function App() {
  // Restore the last-open tab for THIS account (per-account, survives reload).
  const [tab, setTab] = useState<Tab>(() => {
    try {
      const stored = localStorage.getItem(tabKey());
      if (stored && stored in CRUMB) return stored as Tab;
    } catch {}
    return "dashboard";
  });
  useEffect(() => {
    try { localStorage.setItem(tabKey(), tab); } catch {}
  }, [tab]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) === "1"; } catch { return false; }
  });
  const toggleSidebar = () =>
    setSidebarCollapsed(v => {
      const next = !v;
      try { localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0"); } catch {}
      return next;
    });

  // ── Appearance ─────────────────────────────────────────────
  // Apply the persisted appearance on mount. App is keyed by activeId (see
  // AuthGate), so this re-runs on account switch → the per-account theme mode is
  // re-read and its matchMedia listener re-armed. The controls live in
  // Settings → Тема and call apply*/save* imperatively; nothing to lift here.
  useEffect(() => {
    applyThemeMode(loadThemeMode(getActiveId()));
    applyAccent(loadAccent());
    applyDensity(loadDensity());
  }, []);

  // ── Certs task state ───────────────────────────────────────
  const [certTaskId, setCertTaskId]         = useState<string | null>(null);
  const [certLogs, setCertLogs]             = useState<string[]>([]);
  const [certStepStatus, setCertStepStatus] = useState<StatusFrame>(INITIAL_CERT_STATUS);

  const addCertLog = useCallback((line: string) => setCertLogs(l => [...l, line]), []);
  const onCertStatus = useCallback((frame: StatusFrame) =>
    setCertStepStatus(prev => ({
      status: frame.status,
      current_step: frame.current_step === -1 ? prev.current_step : frame.current_step,
      total_steps:  frame.total_steps  === -1 ? prev.total_steps  : frame.total_steps,
    })), []);

  useTaskStream({ taskId: certTaskId, onLog: addCertLog, onStatus: onCertStatus });

  const certIsRunning =
    certStepStatus.status === "running" ||
    (certStepStatus.status === "pending" && certTaskId !== null);
  const certIsDone = certStepStatus.status === "success" || certStepStatus.status === "failed";

  const deployCert = async (data: CertsFormData) => {
    setCertLogs([]); setCertTaskId(null); setCertStepStatus(INITIAL_CERT_STATUS);
    const res = await fetch("/api/certs/deploy", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...data, ssh_port: parseInt(data.ssh_port, 10), cf_api_key: data.cf_api_key || null }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      addCertLog(`\x1b[31m[HTTP ${res.status}] ${JSON.stringify(err.detail ?? err)}\x1b[0m`);
      return;
    }
    const { task_id } = await res.json();
    setCertTaskId(task_id);
  };

  const crumb = CRUMB[tab];

  return (
    <div style={{ display: "flex", height: "100%", position: "relative" }}>
      <Toaster />
      <Sidebar activeTab={tab} onTabChange={setTab} collapsed={sidebarCollapsed} onToggle={toggleSidebar} />

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Topbar */}
        <header style={{
          height: 52, flex: "none", borderBottom: "1px solid var(--line-soft)",
          background: "var(--topbar-bg)", backdropFilter: "blur(var(--glass-blur))",
          display: "flex", alignItems: "center", gap: 12, padding: "0 20px",
        }}>
          <nav style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, minWidth: 0 }}>
            <span className="dim trunc">{crumb[0]}</span>
            <ChevronRight size={13} style={{ color: "var(--t-faint)", flex: "none" }} />
            <span className="hi trunc" style={{ fontWeight: 600 }}>{crumb[1]}</span>
          </nav>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            {/* Remnawave status (moved here from the sidebar footer) */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5 }}>
              <span className="dot" style={{ background: "var(--ok)" }} />
              <span className="dim">Remnawave</span>
              <span className="chip ok" style={{ padding: "1px 7px", fontSize: 10 }}>онлайн</span>
            </div>
            <AccountMenu />
          </div>
        </header>

        {/* Screen */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          {tab === "dashboard" && <Dashboard />}
          {tab === "deploy" && <DeployDashboard />}
          {tab === "templates" && <Templates />}
          {tab === "hosts" && <Hosts />}
          {tab === "settings" && <Settings />}
          {tab === "traffic" && <TrafficRules />}

          {tab === "infra-dashboard" && <InfraDashboard />}
          {tab === "infra-providers" && <InfraProviders />}
          {tab === "infra-projects"  && <InfraProjects />}
          {tab === "infra-services"  && <InfraServices />}
          {tab === "infra-payments"  && <InfraPayments />}
          {tab === "infra-settings"  && <InfraSettings />}
          {tab === "infra-tokens"    && <InfraApiTokens />}

          {tab === "certs" && (
            <div className="flex-1 grid grid-cols-[360px_1fr] min-h-0" style={{ display: "grid" }}>
              <div style={{ borderRight: "1px solid var(--line-soft)", display: "flex", flexDirection: "column", overflowY: "auto" }}>
                <div style={{ padding: 20 }}>
                  <CertsForm onSubmit={deployCert} disabled={certIsRunning} />
                </div>
                <div style={{ padding: "0 20px 20px" }}>
                  <DomainsPanel />
                </div>
                {certTaskId && (
                  <div style={{ padding: "16px 20px 20px", borderTop: "1px solid var(--line-soft)" }}>
                    <p className="micro" style={{ marginBottom: 12 }}>Прогресс</p>
                    <StepProgress
                      currentStep={certStepStatus.current_step}
                      totalSteps={certStepStatus.total_steps}
                      status={certStepStatus.status}
                      steps={RENEW_STEPS}
                    />
                  </div>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
                <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 8 }}>
                  <TermIcon size={13} style={{ color: "var(--t-low)" }} />
                  <span className="micro">Вывод терминала</span>
                  {certLogs.length > 0 && <span className="num" style={{ marginLeft: "auto", fontSize: 11, color: "var(--t-faint)" }}>{certLogs.length} строк</span>}
                </div>
                {certIsDone && (
                  <div style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "10px 16px", fontSize: 13,
                    borderBottom: "1px solid var(--line-soft)",
                    background: certStepStatus.status === "success" ? "var(--ok-dim)" : "var(--err-dim)",
                    color: certStepStatus.status === "success" ? "var(--ok)" : "var(--err)",
                  }}>
                    {certStepStatus.status === "success"
                      ? <><CheckCircle2 size={15} /> Сертификат задеплоен</>
                      : <><XCircle size={15} /> Ошибка выполнения</>}
                  </div>
                )}
                <div style={{ flex: 1, padding: 12, minHeight: 0 }}>
                  {certLogs.length === 0 && !certTaskId ? (
                    <div style={{
                      height: "100%", display: "flex", flexDirection: "column", alignItems: "center",
                      justifyContent: "center", gap: 8, color: "var(--t-faint)", fontSize: 13,
                      border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)",
                    }}>
                      <TermIcon size={28} style={{ opacity: .3 }} />
                      <span>Заполните форму и нажмите «Задеплоить сертификат»</span>
                    </div>
                  ) : (
                    <TerminalOutput lines={certLogs} />
                  )}
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
