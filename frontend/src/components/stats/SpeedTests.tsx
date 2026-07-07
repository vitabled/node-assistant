import { useState, useEffect, useCallback, useRef, type ReactNode } from "react";
import { Zap, Play, Loader2, ArrowRight, History } from "lucide-react";
import { deployJobsKey, getActiveId } from "../../auth/store";
import { toast } from "../infra/Toast";

// «Статистика → Тесты скорости» (Ф2b, wave1) — interactive any-to-any iperf3
// matrix + xray-link speed test. Resources are gathered client-side from the
// deploy-node / panel jobs (localStorage, with saved SSH creds) and the
// test-server registry; the backend orchestrates the two SSH sides. Creds live
// only in localStorage and are sent per-request (never persisted server-side).

const METRICS_KEY = "ni_speedtest_metrics";

type Kind = "node" | "panel" | "testserver";

interface Resource {
  id: string;          // unique select key
  label: string;
  kind: Kind;
  ip: string;
  ssh_user: string;
  ssh_password: string;
  ssh_port: number;
  iperf_port: number;
  hasCreds: boolean;   // node/panel with saved SSH creds (required for A / xray source)
}

interface TestServer { id: string; name: string; ip: string; iperf_port: number }

interface Run {
  ts?: number; kind?: string; resource_key?: string;
  iperf_mbps?: number | null; iperf_jitter?: number | null; ping_ms?: number | null;
  traceroute?: string | null;
  xray_down?: number | null; xray_up?: number | null; xray_ping?: number | null;
}

const METRIC_LEVELS = [
  { level: 1, label: "Скорость" },
  { level: 2, label: "+пинг/джиттер" },
  { level: 3, label: "+трассировка" },
];

const fmtMbps = (v?: number | null) => (v == null ? "—" : `${v.toFixed(1)} Мбит/с`);
const fmtMs = (v?: number | null) => (v == null ? "—" : `${v.toFixed(1)} мс`);

function panelJobsKey(): string {
  return `panel_jobs_${getActiveId() ?? "none"}`;
}

function sshPortOf(f: any): number {
  // Deploy-job schema uses change_ssh_port/current/new; the panel-job schema
  // (PanelDeployRequest) stores a flat ssh_port — fall back to it.
  const p = f?.change_ssh_port ? f?.new_ssh_port : (f?.current_ssh_port ?? f?.ssh_port);
  return parseInt(p, 10) || 22;
}

// Deploy nodes (successful) + panels (may not exist yet) + registered test
// servers → a flat resource list for the A/B selectors.
function loadResources(servers: TestServer[]): Resource[] {
  const out: Resource[] = [];
  const readJobs = (key: string, kind: Kind, domainField: string) => {
    try {
      const jobs = JSON.parse(localStorage.getItem(key) || "[]");
      for (const j of Array.isArray(jobs) ? jobs : []) {
        const f = j.savedForm || j;
        const ip = f?.ip;
        if (!ip) continue;
        // Only finished-successfully resources are valid test targets — an
        // in-progress deploy (finalStatus undefined) may still be mid dual-port
        // SSH swap. Strict match, as in DomainsPanel/TestServers.
        if (j.finalStatus !== "success") continue;
        out.push({
          id: `${kind}:${j.taskId || ip}`,
          label: `${f[domainField] || j.domain || ip} (${ip})`,
          kind, ip,
          ssh_user: f.ssh_user || "root",
          ssh_password: f.ssh_password || "",
          ssh_port: sshPortOf(f),
          iperf_port: 5201,
          hasCreds: !!f.ssh_password,
        });
      }
    } catch { /* ignore malformed store */ }
  };
  readJobs(deployJobsKey(), "node", "domain");
  readJobs(panelJobsKey(), "panel", "panel_domain");
  for (const s of servers) {
    out.push({
      id: `testserver:${s.id}`,
      label: `${s.name} (${s.ip}:${s.iperf_port})`,
      kind: "testserver", ip: s.ip,
      ssh_user: "root", ssh_password: "", ssh_port: 22,
      iperf_port: s.iperf_port, hasCreds: false,
    });
  }
  return out;
}

function endpointPayload(r: Resource): Record<string, unknown> {
  if (r.kind === "testserver") return { kind: "testserver", ip: r.ip, iperf_port: r.iperf_port };
  return {
    kind: r.kind, ip: r.ip,
    ssh_user: r.ssh_user, ssh_password: r.ssh_password, ssh_port: r.ssh_port,
  };
}

function loadDefaultLevel(): number {
  const v = parseInt(localStorage.getItem(METRICS_KEY) || "1", 10);
  return v >= 1 && v <= 3 ? v : 1;
}

// ── shared atoms ──────────────────────────────────────────────
function Card({ children }: { children: ReactNode }) {
  return <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>{children}</div>;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12 }}>
      <span style={{ color: "var(--t-low)", flex: "none" }}>{label}</span>
      <span className="num trunc" style={{ color: "var(--t-hi)", textAlign: "right" }}>{value}</span>
    </div>
  );
}

function Select({ label, value, onChange, options, placeholder, disabled }:
  { label: string; value: string; onChange: (v: string) => void; options: Resource[]; placeholder: string; disabled?: boolean }) {
  return (
    <label style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <span className="dim">{label}</span>
      <select className="selectbox" value={value} disabled={disabled}
        onChange={e => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
    </label>
  );
}

function MetricSeg({ level, onChange, disabled }: { level: number; onChange: (l: number) => void; disabled?: boolean }) {
  return (
    <div className="flex items-center gap-1">
      {METRIC_LEVELS.map(m => (
        <button key={m.level} type="button" disabled={disabled} onClick={() => onChange(m.level)}
          className={`px-2 py-1 rounded border text-[11px] transition-colors ${
            level === m.level
              ? "bg-[var(--accent-dim)] border-[var(--accent-line)] text-[var(--accent-hi)]"
              : "bg-[var(--bg2)] border-[var(--line)] text-[var(--t-low)] hover:bg-[var(--bg3)]"
          } disabled:opacity-50`}>
          {m.label}
        </button>
      ))}
    </div>
  );
}

// ── result rendering ──────────────────────────────────────────
function ResultBlock({ run }: { run: Run }) {
  if (run.kind === "xray") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Row label="Источник" value={run.resource_key || "—"} />
        <Row label="Download" value={fmtMbps(run.xray_down)} />
        <Row label="Upload" value={fmtMbps(run.xray_up)} />
        <Row label="Пинг (туннель)" value={fmtMs(run.xray_ping)} />
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <Row label="Пара" value={run.resource_key || "—"} />
      <Row label="Пропускная способность" value={fmtMbps(run.iperf_mbps)} />
      {run.ping_ms != null && <Row label="Пинг" value={fmtMs(run.ping_ms)} />}
      {run.iperf_jitter != null && <Row label="Джиттер" value={fmtMs(run.iperf_jitter)} />}
      {run.traceroute && (
        <pre style={{
          marginTop: 4, fontSize: 10.5, lineHeight: 1.5, color: "var(--t-low)",
          background: "var(--bg2)", border: "1px solid var(--line-soft)", borderRadius: "var(--r-sm)",
          padding: 8, maxHeight: 160, overflow: "auto", whiteSpace: "pre-wrap",
        }}>{run.traceroute}</pre>
      )}
    </div>
  );
}

// ── page ──────────────────────────────────────────────────────
export function SpeedTests() {
  const [servers, setServers] = useState<TestServer[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  const [mode, setMode] = useState<"pair" | "xray">("pair");
  const [level, setLevel] = useState(loadDefaultLevel);

  const [aId, setAId] = useState("");
  const [bId, setBId] = useState("");
  const [srcId, setSrcId] = useState("");
  const [xrayLink, setXrayLink] = useState("");

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Run | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [formErr, setFormErr] = useState<string | null>(null);

  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);

  const refreshHistory = useCallback(() => {
    fetch("/api/speedtest/history?limit=30")
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (aliveRef.current && Array.isArray(d?.history)) setHistory(d.history); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/testservers")
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (aliveRef.current && Array.isArray(d?.servers)) setServers(d.servers); })
      .catch(() => {});
    refreshHistory();
  }, [refreshHistory]);

  useEffect(() => { setResources(loadResources(servers)); }, [servers]);

  const withCreds = resources.filter(r => r.hasCreds);      // A / xray source
  const aRes = resources.find(r => r.id === aId);
  const bRes = resources.find(r => r.id === bId);

  const runPair = async () => {
    setFormErr(null);
    if (!aRes || !bRes) { setFormErr("Выберите обе стороны"); return; }
    if (aRes.ip === bRes.ip) { setFormErr("Стороны A и B не могут совпадать"); return; }
    setRunning(true); setResult(null);
    try {
      const res = await fetch("/api/speedtest/pair", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          a: endpointPayload(aRes), b: endpointPayload(bRes),
          metrics: Array.from({ length: level }, (_, i) => i + 1),
        }),
      });
      await handleRun(res);
    } catch (e) { if (aliveRef.current) toast((e as Error).message, "error"); }
    finally { if (aliveRef.current) setRunning(false); }
  };

  const runXray = async () => {
    setFormErr(null);
    const src = resources.find(r => r.id === srcId);
    if (!src) { setFormErr("Выберите источник"); return; }
    if (!xrayLink.trim()) { setFormErr("Укажите xray-ссылку"); return; }
    setRunning(true); setResult(null);
    try {
      const res = await fetch("/api/speedtest/xray", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: endpointPayload(src), xray_link: xrayLink.trim(),
          metrics: Array.from({ length: level }, (_, i) => i + 1),
        }),
      });
      await handleRun(res);
    } catch (e) { if (aliveRef.current) toast((e as Error).message, "error"); }
    finally { if (aliveRef.current) setRunning(false); }
  };

  const handleRun = async (res: Response) => {
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (aliveRef.current)
        toast(typeof err.detail === "string" ? err.detail : "Ошибка теста скорости", "error");
      return;
    }
    const d = await res.json();
    if (!aliveRef.current) return;
    if (d.current) setResult(d.current);
    if (Array.isArray(d.history)) setHistory(d.history);
    (d.warnings ?? []).forEach((w: string) => toast(w, "info"));
  };

  const canRun = mode === "pair" ? !!(aRes && bRes) : !!(srcId && xrayLink.trim());

  return (
    <div className="ni-pagebody" style={{ flex: 1, overflowY: "auto", padding: 20 }}>
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Zap size={18} style={{ color: "var(--accent)" }} />
        <div>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Тесты скорости</h1>
          <p style={{ fontSize: 12, color: "var(--t-low)" }}>iperf3 между любой парой ресурсов и замер через xray-ссылку</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2" style={{ display: "grid", gap: 16 }}>
        {/* ── run form ── */}
        <Card>
          <div className="seg" style={{ width: "fit-content" }}>
            <button className={mode === "pair" ? "on" : ""} disabled={running} onClick={() => setMode("pair")}>Пара (iperf3)</button>
            <button className={mode === "xray" ? "on" : ""} disabled={running} onClick={() => setMode("xray")}>Xray-ссылка</button>
          </div>

          {resources.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--t-faint)" }}>
              Нет доступных ресурсов. Разверните ноду (Деплой) или добавьте тест-сервер
              (Настройки → Сервера для тестирования).
            </p>
          ) : mode === "pair" ? (
            <>
              {withCreds.length === 0 && (
                <p style={{ fontSize: 11, color: "var(--t-faint)" }}>
                  Для стороны A нужен ресурс с сохранёнными SSH-данными (нода/панель).
                </p>
              )}
              <Select label="Сторона A (клиент)" value={aId} onChange={setAId}
                options={withCreds} placeholder="Выберите ресурс" disabled={running} />
              <div style={{ display: "flex", justifyContent: "center", color: "var(--t-faint)" }}>
                <ArrowRight size={14} />
              </div>
              <Select label="Сторона B (приёмник)" value={bId} onChange={setBId}
                options={resources} placeholder="Выберите ресурс" disabled={running} />
            </>
          ) : (
            <>
              <Select label="Источник" value={srcId} onChange={setSrcId}
                options={withCreds} placeholder="Выберите ресурс" disabled={running} />
              <label style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="dim">Xray-ссылка</span>
                <input type="password" className="input" value={xrayLink} disabled={running}
                  autoComplete="off" spellCheck={false}
                  onChange={e => { setXrayLink(e.target.value); setFormErr(null); }}
                  placeholder="vless / trojan / vmess / ss" />
              </label>
            </>
          )}

          {resources.length > 0 && (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span className="dim" style={{ fontSize: 12 }}>Метрики</span>
                <MetricSeg level={level} onChange={setLevel} disabled={running} />
              </div>
              {formErr && <span className="errmsg">{formErr}</span>}
              <button type="button" disabled={running || !canRun}
                onClick={mode === "pair" ? runPair : runXray} className="btn btn-primary"
                style={{ width: "fit-content" }}>
                {running
                  ? <><Loader2 size={13} className="animate-spin" /> Тест выполняется…</>
                  : <><Play size={13} /> Запустить</>}
              </button>
            </>
          )}
        </Card>

        {/* ── result ── */}
        <Card>
          <span className="micro flex items-center gap-2"><Zap size={13} /> Результат</span>
          {running ? (
            <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Идёт замер, это может занять несколько минут…</p>
          ) : result ? (
            <ResultBlock run={result} />
          ) : (
            <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Запустите тест, чтобы увидеть результат.</p>
          )}
        </Card>
      </div>

      {/* ── history ── */}
      <div className="card" style={{ padding: 16, marginTop: 16 }}>
        <span className="micro flex items-center gap-2" style={{ marginBottom: 12 }}>
          <History size={13} /> История тестов
        </span>
        {history.length === 0 ? (
          <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Тестов пока не было.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="tbl" style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left" }}>Когда</th>
                  <th style={{ textAlign: "left" }}>Тип</th>
                  <th style={{ textAlign: "left" }}>Ресурс</th>
                  <th style={{ textAlign: "left" }}>Результат</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={i}>
                    <td className="num" style={{ color: "var(--t-low)" }}>
                      {h.ts ? new Date(h.ts * 1000).toLocaleString("ru-RU",
                        { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"}
                    </td>
                    <td><span className="chip">{h.kind === "xray" ? "xray" : "iperf3"}</span></td>
                    <td className="trunc" style={{ maxWidth: 220 }}>{h.resource_key || "—"}</td>
                    <td className="num">
                      {h.kind === "xray"
                        ? `↓ ${fmtMbps(h.xray_down)} · ↑ ${fmtMbps(h.xray_up)}`
                        : `${fmtMbps(h.iperf_mbps)}${h.ping_ms != null ? ` · пинг ${fmtMs(h.ping_ms)}` : ""}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
