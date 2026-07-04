import { useState, useEffect, useCallback, useRef } from "react";
import {
  CheckCircle2, XCircle, Terminal as TermIcon, ChevronRight, Clock, Sliders, Check,
} from "lucide-react";
import { Sidebar, type Tab }               from "./components/Sidebar";
import { Dashboard }                       from "./components/Dashboard";
import { DeployDashboard }                 from "./components/DeployDashboard";
import { Settings }                        from "./components/Settings";
import { Templates }                       from "./components/Templates";
import { CertsForm, type CertsFormData }  from "./components/CertsForm";
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
import { tabKey }                          from "./auth/store";
import {
  ACCENTS, type AccentKey, type Density,
  applyAccent, applyDensity, loadAccent, loadDensity, saveAccent, saveDensity,
} from "./theme/tweaks";

const SIDEBAR_KEY = "sidebar_collapsed";

const INITIAL_CERT_STATUS: StatusFrame = {
  status: "pending", current_step: 0, total_steps: RENEW_STEPS.length,
};

// Breadcrumb labels per tab.
const CRUMB: Record<Tab, [string, string]> = {
  "dashboard":       ["Node Installer", "Дешборд"],
  "deploy":          ["Node Installer", "Деплой ноды"],
  "certs":           ["Node Installer", "Обновить SSL"],
  "templates":       ["Node Installer", "Шаблоны"],
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

  // ── Appearance tweaks ──────────────────────────────────────
  const [accent, setAccent]   = useState<AccentKey>(loadAccent);
  const [density, setDensity] = useState<Density>(loadDensity);
  const [tweaksOpen, setTweaksOpen] = useState(false);
  const tweaksRef = useRef<HTMLDivElement>(null);
  useEffect(() => { applyAccent(accent); saveAccent(accent); }, [accent]);
  useEffect(() => { applyDensity(density); saveDensity(density); }, [density]);
  useEffect(() => {
    if (!tweaksOpen) return;
    const h = (e: MouseEvent) => { if (tweaksRef.current && !tweaksRef.current.contains(e.target as Node)) setTweaksOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [tweaksOpen]);

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

  const renewCerts = async (data: CertsFormData) => {
    setCertLogs([]); setCertTaskId(null); setCertStepStatus(INITIAL_CERT_STATUS);
    const res = await fetch("/api/certs/renew", {
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
          background: "rgba(13,17,25,.7)", backdropFilter: "blur(8px)",
          display: "flex", alignItems: "center", gap: 12, padding: "0 20px",
        }}>
          <nav style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, minWidth: 0 }}>
            <span className="dim trunc">{crumb[0]}</span>
            <ChevronRight size={13} style={{ color: "var(--t-faint)", flex: "none" }} />
            <span className="hi trunc" style={{ fontWeight: 600 }}>{crumb[1]}</span>
          </nav>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            <div className="num" style={{ fontSize: 11.5, color: "var(--t-low)", display: "flex", alignItems: "center", gap: 6 }}>
              <Clock size={12} /> {new Date().toLocaleDateString("ru-RU", { day: "2-digit", month: "short" })}
            </div>
            {/* Tweaks */}
            <div style={{ position: "relative" }} ref={tweaksRef}>
              <button className="iconbtn" onClick={() => setTweaksOpen(v => !v)} title="Внешний вид">
                <Sliders size={15} />
              </button>
              {tweaksOpen && (
                <div className="panel" style={{
                  position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 50, width: 232,
                  padding: 14, boxShadow: "0 18px 48px rgba(0,0,0,.5)", display: "flex", flexDirection: "column", gap: 14,
                }}>
                  <div>
                    <p className="micro" style={{ marginBottom: 8 }}>Акцентный цвет</p>
                    <div style={{ display: "flex", gap: 8 }}>
                      {(Object.keys(ACCENTS) as AccentKey[]).map(k => (
                        <button key={k} onClick={() => setAccent(k)} title={k}
                          style={{
                            width: 26, height: 26, borderRadius: 7, background: ACCENTS[k].base, cursor: "pointer",
                            border: accent === k ? "2px solid var(--t-hi)" : "2px solid transparent",
                            display: "grid", placeItems: "center",
                          }}>
                          {accent === k && <Check size={13} color={ACCENTS[k].ink} strokeWidth={3} />}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="micro" style={{ marginBottom: 8 }}>Плотность</p>
                    <div className="seg">
                      <button className={density === "comfortable" ? "on" : ""} onClick={() => setDensity("comfortable")}>Обычная</button>
                      <button className={density === "compact" ? "on" : ""} onClick={() => setDensity("compact")}>Плотная</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
            <AccountMenu />
          </div>
        </header>

        {/* Screen */}
        <main style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          {tab === "dashboard" && <Dashboard />}
          {tab === "deploy" && <DeployDashboard />}
          {tab === "templates" && <Templates />}
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
                  <CertsForm onSubmit={renewCerts} disabled={certIsRunning} />
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
                      ? <><CheckCircle2 size={15} /> Сертификаты обновлены</>
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
                      <span>Заполните форму и нажмите «Обновить сертификаты»</span>
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
