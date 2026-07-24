import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  Activity, Zap, RefreshCw, Loader2, ChevronDown, CheckCircle2,
  AlertTriangle, XCircle, Clock,
  Check, Plus, Trash2, Pencil, X, Save, Server, Radio, Eye, EyeOff, Download,
} from "lucide-react";
import { FlagChip } from "./common/FlagChip";
import { ImportFromSubscription } from "./ImportFromSubscription";
import { resolveCountryCode, splitFlagEmoji } from "../utils/countryAliases";

// ── Types (mirror /api/checker/statuspage + /incidents) ───────
type TickStatus = "up" | "slow" | "down";
interface Bar { ts: number; status: TickStatus }
interface Node {
  stableId: string; name: string; groupName: string; protocol: string;
  online: boolean; latencyMs: number; uptime30d: number | null; bars: Bar[];
  subId?: string;
  // server-uptime extras (present only on the Server uptime tab)
  source?: string; ip?: string; port?: number; country?: string; note?: string;
  hidden?: boolean;   // Волна 6: убран с глаз, но продолжает пробиться
}
type GState = "ok" | "partial" | "down" | "unknown";
interface Global {
  state: GState; uptime30d: number | null; protocols: string[];
  total: number; online: number; offline: number;
}
interface SubMeta { id: string; label: string }
interface StatusResp {
  container?: string; reachable: boolean; nodes: Node[]; global: Global;
  subscriptions?: SubMeta[];
}
interface Incident {
  stableId: string; name: string; group: string; start: number; end: number;
  durationSec: number; reason: string; ongoing: boolean;
}

// ── Helpers ───────────────────────────────────────────────────
// Resolve an alpha-2 code from a node's location group. xray-checker gives a
// free-form groupName (a 2-letter code, an English or Russian country name, or a
// string with an embedded flag emoji) — `resolveCountryCode` tries each. We keep
// the CODE, not an emoji: flags render as `FlagChip` SVGs, because a
// regional-indicator pair shows up as two bare letters on several Windows builds.
const flagFor = resolveCountryCode;
function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec} сек`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m} мин ${s} сек`;
  const h = Math.floor(m / 60);
  return `${h} ч ${m % 60} мин`;
}
function fmtWhen(ts: number): string {
  return new Date(ts * 1000).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
// These return CSS-var color strings (not class names) — applied via inline style.
const barColor: Record<TickStatus, string> = {
  up: "var(--ok)", slow: "var(--warn)", down: "var(--err)",
};
function uptimeColor(pct: number | null): string {
  if (pct == null) return "var(--t-low)";
  if (pct >= 99.5) return "var(--ok)";
  if (pct >= 98) return "var(--warn)";
  return "var(--err)";
}
function latencyColor(online: boolean, ms: number): string {
  if (!online) return "var(--t-faint)";
  if (ms < 300) return "var(--ok)";
  if (ms < 800) return "var(--warn)";
  return "var(--err)";
}

// Global banner appearance per health state (style objects, not class strings —
// applied via style= so the banner follows the light/dark theme tokens).
const BANNER: Record<GState, { style: React.CSSProperties; icon: React.ReactNode; text: string }> = {
  ok:      { style: { borderColor: "var(--ok-line)", background: "var(--ok-dim)", color: "var(--ok)" },
             icon: <CheckCircle2 size={22} />, text: "Все узлы работают стабильно" },
  partial: { style: { borderColor: "var(--warn-line)", background: "var(--warn-dim)", color: "var(--warn)" },
             icon: <AlertTriangle size={22} />, text: "Частичные сбои" },
  down:    { style: { borderColor: "var(--err-line)", background: "var(--err-dim)", color: "var(--err)" },
             icon: <XCircle size={22} />, text: "Критическая нестабильность сети" },
  unknown: { style: { borderColor: "var(--line-soft)", background: "var(--bg2)", color: "var(--t-low)" },
             icon: <Activity size={22} />, text: "Нет данных мониторинга" },
};

// ── Top-level: tab switcher (Xray uptime / Server uptime) ──────
type DashTab = "xray" | "server";

export function Dashboard() {
  const [tab, setTab] = useState<DashTab>("xray");
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="ni-pagebody max-w-5xl mx-auto px-6 py-6">
        {/* Horizontal tabs */}
        <div className="flex rounded-lg border border-[var(--line-soft)] overflow-hidden mb-5 w-fit">
          {([["xray", "Xray uptime", <Radio size={13} key="i" />],
             ["server", "Server uptime", <Server size={13} key="i" />]] as const).map(([id, label, icon]) => (
            <button key={id} onClick={() => setTab(id as DashTab)}
              className={`flex items-center gap-1.5 px-4 py-2 text-[13px] font-medium transition-colors ${
                tab === id ? "bg-[var(--bg3)] text-[var(--t-hi)]" : "text-[var(--t-low)] hover:text-[var(--t-mid)]"}`}>
              {icon}{label}
            </button>
          ))}
        </div>
        {tab === "xray" ? <XrayUptime /> : <ServerUptime />}
      </div>
    </div>
  );
}

// ── Shared: health banner + incident log ──────────────────────
function HealthBanner({ state, primary, secondary, stats }: {
  state: GState; primary?: string; secondary: string; stats?: React.ReactNode;
}) {
  const banner = BANNER[state];
  return (
    <div className="ni-health rounded-xl border p-5 mb-6 flex items-center gap-4" style={banner.style}>
      {banner.icon}
      <div className="flex-1">
        <p className="text-lg font-semibold">{primary ?? banner.text}</p>
        <p className="text-xs opacity-70 mt-0.5">{secondary}</p>
      </div>
      {stats && <div className="ni-health-stats flex items-center gap-6 text-right">{stats}</div>}
    </div>
  );
}

function IncidentLog({ incidents }: { incidents: Incident[] }) {
  return (
    <div className="mt-6 rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-4">
      <p className="micro mb-3 flex items-center gap-2">
        <Clock size={12} /> История доступности за последние 7 дней
      </p>
      {incidents.length === 0 ? (
        <p className="text-xs text-[var(--t-faint)] py-3 text-center">Инцидентов не зафиксировано — все ноды были стабильны. ✓</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {incidents.slice(0, 50).map((it, i) => (
            <li key={i} className="flex items-start gap-2.5 text-xs">
              <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${it.ongoing ? "bg-[var(--err)] animate-pulse" : "bg-[var(--t-faint)]"}`} />
              <span className="text-[var(--t-low)] tabular-nums shrink-0">{fmtWhen(it.start)}</span>
              <span className="text-[var(--t-mid)]">
                Нода <span className="text-[var(--t-hi)] font-medium">{it.name}</span>
                {it.group && <span className="text-[var(--t-low)]"> ({it.group})</span>}
                {it.ongoing
                  ? <span className="text-[var(--err)]"> недоступна сейчас</span>
                  : <> была недоступна в течение <span className="text-[var(--warn)]">{fmtDuration(it.durationSec)}</span></>}.
                <span className="text-[var(--t-faint)]"> Причина: {it.reason}.</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// A collapsible country subgroup of node rows.
function CountryGroup({ country, nodes, ticks, defaultOpen = true }: {
  country: string; nodes: Node[]; ticks: number; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const cc = flagFor(country);
  const anyDown = nodes.some(n => !n.online);
  const label = country || "Прочее";
  return (
    <div className="rounded-xl border border-[var(--line-soft)] overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-[var(--bg2)] hover:bg-[var(--bg3)] transition-colors">
        <FlagChip code={cc} size={20} />
        <span className="text-sm font-medium text-[var(--t-hi)]">{label}</span>
        <span className="text-[11px] text-[var(--t-faint)]">Нод: {nodes.length}</span>
        {anyDown && <span className="w-1.5 h-1.5 rounded-full bg-[var(--err)]" />}
        <ChevronDown size={14} className={`ml-auto text-[var(--t-faint)] transition-transform ${open ? "" : "-rotate-90"}`} />
      </button>
      {open && (
        <div className="divide-y divide-[var(--line-soft)]">
          {nodes.map(n => <NodeRow key={n.stableId} node={n} cc={cc} ticks={ticks} />)}
        </div>
      )}
    </div>
  );
}

// ── Xray uptime tab (the original status page) ────────────────
function XrayUptime() {
  const [data, setData]         = useState<StatusResp | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [ticks, setTicks]       = useState(30);
  const [checking, setChecking] = useState(false);
  const [loading, setLoading]   = useState(true);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [instances, setInstances] = useState<{ id: string; name: string }[]>([]);
  const [checkerId, setCheckerId] = useState("local");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch("/api/checker/instances").then(r => r.json())
      .then(d => setInstances(Array.isArray(d.instances) ? d.instances : []))
      .catch(() => {});
  }, []);

  const load = useCallback(async (n: number) => {
    try {
      const [s, inc] = await Promise.all([
        fetch(`/api/checker/statuspage?ticks=${n}&checker_id=${encodeURIComponent(checkerId)}`).then(r => r.json()),
        fetch(`/api/checker/incidents?days=7&checker_id=${encodeURIComponent(checkerId)}`).then(r => r.json()).catch(() => ({ incidents: [] })),
      ]);
      setData(s);
      setIncidents(Array.isArray(inc.incidents) ? inc.incidents : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, [checkerId]);

  useEffect(() => {
    load(ticks);
    timer.current = setInterval(() => load(ticks), 10_000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [load, ticks]);

  const deepCheck = async () => {
    setChecking(true);
    try { await fetch("/api/checker/check", { method: "POST" }); await load(ticks); }
    catch { /* ignore */ }
    setChecking(false);
  };

  const g = data?.global;
  const running = data?.container === "running";
  const state: GState = running && g?.state ? g.state : "unknown";

  // Two-level grouping: subscription → country. subId maps to a subscription
  // label; within a subscription, nodes are grouped by country (from the name).
  const subGroups = useMemo(() => {
    const subLabels = new Map((data?.subscriptions ?? []).map(s => [s.id, s.label]));
    const bySub = new Map<string, Node[]>();
    (data?.nodes ?? []).forEach(n => {
      const key = n.subId || "";
      (bySub.get(key) ?? bySub.set(key, []).get(key)!).push(n);
    });
    return Array.from(bySub.entries())
      .map(([subId, nodes]) => {
        const byCountry = new Map<string, Node[]>();
        nodes.forEach(n => {
          const c = n.groupName || "Прочее";
          (byCountry.get(c) ?? byCountry.set(c, []).get(c)!).push(n);
        });
        return {
          subId,
          label: subLabels.get(subId) || (subId ? subId : "Без привязки к подписке"),
          countries: Array.from(byCountry.entries()).sort((a, b) => a[0].localeCompare(b[0])),
          count: nodes.length,
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data]);

  return (
    <>
      {/* Header row */}
      <div className="ni-pagehead flex items-center justify-between mb-4">
        <h1 className="text-base font-semibold text-[var(--t-hi)] flex items-center gap-2">
          <Activity size={16} className="text-[var(--accent-hi)]" /> Статус нод сети
        </h1>
        <div className="ni-pagehead-actions flex items-center gap-2">
          {instances.length > 1 && (
            <select className="selectbox" value={checkerId}
              onChange={e => { setLoading(true); setCheckerId(e.target.value); }}
              title="Инстанс мониторинга">
              {instances.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
            </select>
          )}
          <div className="flex rounded-md border border-[var(--line-soft)] overflow-hidden">
            {[30, 60, 90].map(n => (
              <button key={n} onClick={() => setTicks(n)}
                className={`px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  ticks === n ? "bg-[var(--bg3)] text-[var(--t-hi)]" : "text-[var(--t-low)] hover:text-[var(--t-mid)]"}`}>
                {n}
              </button>
            ))}
          </div>
          <button onClick={() => load(ticks)} className="iconbtn" title="Обновить">
            <RefreshCw size={13} />
          </button>
          <button onClick={deepCheck} disabled={checking || !running} className="btn btn-primary">
            {checking ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
            Перепроверить все ноды
          </button>
        </div>
      </div>

      <SubscriptionSelector />

      <HealthBanner
        state={state}
        secondary={running && g?.state
          ? `${g.online} из ${g.total} узлов онлайн`
          : loading ? "Загрузка…" : "Мониторинг не запущен — включите его в настройках мониторинга выше"}
        stats={<>
          <Stat label="Аптайм 30 дней" value={g?.uptime30d != null ? `${g.uptime30d}%` : "—"} />
          <Stat label="Активных протоколов" value={g?.protocols ? String(g.protocols.length) : "—"}
            sub={g?.protocols?.join(", ")} />
        </>}
      />

      {/* Subscription → country groups */}
      {subGroups.length === 0 ? (
        <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-8 text-center text-[var(--t-faint)] text-sm">
          {running ? "Нет нод в подписке." : "Мониторинг неактивен."}
        </div>
      ) : subGroups.map(sg => {
        const isCollapsed = collapsed[sg.subId];
        return (
          <div key={sg.subId || "_none"} className="mb-5">
            <button
              onClick={() => setCollapsed(c => ({ ...c, [sg.subId]: !c[sg.subId] }))}
              className="w-full flex items-center gap-2.5 mb-2 text-left">
              <Radio size={14} className="text-[var(--accent-hi)]" />
              <span className="text-sm font-semibold text-[var(--t-hi)] truncate">{sg.label}</span>
              <span className="text-[11px] text-[var(--t-faint)]">Нод: {sg.count}</span>
              <ChevronDown size={14} className={`ml-auto text-[var(--t-faint)] transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
            </button>
            {!isCollapsed && (
              <div className="flex flex-col gap-3 pl-1">
                {sg.countries.map(([country, nodes]) => (
                  <CountryGroup key={country} country={country} nodes={nodes} ticks={ticks} />
                ))}
              </div>
            )}
          </div>
        );
      })}

      <IncidentLog incidents={incidents} />
    </>
  );
}

// ── Server uptime tab (by-IP availability monitor) ────────────
interface SrvForm { name: string; country: string; ip: string; port: string; note: string }
const SRV_EMPTY: SrvForm = { name: "", country: "", ip: "", port: "443", note: "" };

function ServerUptime() {
  const [data, setData]         = useState<StatusResp | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [ticks, setTicks]       = useState(30);
  const [loading, setLoading]   = useState(true);
  const [modal, setModal]       = useState<{ editing?: Node } | null>(null);
  const [importing, setImporting] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (n: number) => {
    try {
      const [s, inc] = await Promise.all([
        fetch(`/api/server-monitor/statuspage?ticks=${n}`).then(r => r.json()),
        fetch(`/api/server-monitor/incidents?days=7`).then(r => r.json()).catch(() => ({ incidents: [] })),
      ]);
      setData(s);
      setIncidents(Array.isArray(inc.incidents) ? inc.incidents : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, []);

  // Auto-sync deployed nodes from the browser's deploy_jobs on mount.
  useEffect(() => {
    try {
      const accId = (localStorage.getItem("ni_active_account") || "").replace(/^"|"$/g, "");
      const raw = localStorage.getItem(`deploy_jobs_${accId}`);
      const jobs = raw ? JSON.parse(raw) : [];
      const deployed = (Array.isArray(jobs) ? jobs : [])
        .filter((j: any) => j?.savedForm?.mode === "remnanode" && j?.savedForm?.ip)
        .map((j: any) => ({
          name: j.savedForm.domain || j.savedForm.ip,
          country: (j.savedForm.country_code || "").toUpperCase(),
          ip: j.savedForm.ip,
          port: 443,
        }));
      if (deployed.length)
        fetch("/api/server-monitor/servers/sync-deployed", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(deployed),
        }).catch(() => {});
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    load(ticks);
    timer.current = setInterval(() => load(ticks), 10_000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [load, ticks]);

  const g = data?.global;
  const state: GState = g?.state ?? "unknown";

  const removeServer = async (id: string) => {
    await fetch(`/api/server-monitor/servers/${id}`, { method: "DELETE" }).catch(() => {});
    load(ticks);
  };

  // Скрытие — единственный способ убрать с глаз deployed-строку: удалить её
  // нельзя (ре-синк из deploy_jobs вернёт), а PATCH остальных полей у неё
  // запрещён. В отличие от удаления, скрытие НЕ трогает историю проб.
  const setHidden = async (id: string, hidden: boolean) => {
    await fetch(`/api/server-monitor/servers/${id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden }),
    }).catch(() => {});
    load(ticks);
  };

  const hiddenNodes = useMemo(() => (data?.nodes ?? []).filter(n => n.hidden), [data]);

  // Group by country — скрытые в страновые группы не попадают.
  const groups = useMemo(() => {
    const map = new Map<string, Node[]>();
    (data?.nodes ?? []).filter(n => !n.hidden).forEach(n => {
      const key = n.country || n.groupName || "Прочее";
      (map.get(key) ?? map.set(key, []).get(key)!).push(n);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [data]);

  return (
    <>
      <div className="ni-pagehead flex items-center justify-between mb-4">
        <h1 className="text-base font-semibold text-[var(--t-hi)] flex items-center gap-2">
          <Server size={16} className="text-[var(--accent-hi)]" /> Доступность серверов
        </h1>
        <div className="ni-pagehead-actions flex items-center gap-2">
          <div className="flex rounded-md border border-[var(--line-soft)] overflow-hidden">
            {[30, 60, 90].map(n => (
              <button key={n} onClick={() => setTicks(n)}
                className={`px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  ticks === n ? "bg-[var(--bg3)] text-[var(--t-hi)]" : "text-[var(--t-low)] hover:text-[var(--t-mid)]"}`}>
                {n}
              </button>
            ))}
          </div>
          <button onClick={() => load(ticks)} className="iconbtn" title="Обновить">
            <RefreshCw size={13} />
          </button>
          <button onClick={() => setImporting(true)} className="btn btn-soft" title="Импорт из подписки">
            <Download size={13} /> Из подписки
          </button>
          <button onClick={() => setModal({})} className="btn btn-primary">
            <Plus size={13} /> Добавить сервер
          </button>
        </div>
      </div>

      <HealthBanner
        state={state}
        secondary={g?.state
          ? `${g.online} из ${g.total} серверов онлайн`
          : loading ? "Загрузка…" : "Серверы не добавлены — добавьте вручную или задеплойте ноду"}
        stats={<Stat label="Аптайм 30 дней" value={g?.uptime30d != null ? `${g.uptime30d}%` : "—"} />}
      />

      {groups.length === 0 ? (
        <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-8 text-center text-[var(--t-faint)] text-sm">
          Нет отслеживаемых серверов. Нажмите «Добавить сервер» или задеплойте ноду.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {groups.map(([country, nodes]) => {
            const cc = flagFor(country);
            return (
              <div key={country} className="rounded-xl border border-[var(--line-soft)] overflow-hidden">
                <div className="flex items-center gap-2.5 px-4 py-2.5 bg-[var(--bg2)]">
                  <FlagChip code={cc} size={20} />
                  <span className="text-sm font-medium text-[var(--t-hi)]">{country || "Прочее"}</span>
                  <span className="text-[11px] text-[var(--t-faint)]">Серверов: {nodes.length}</span>
                </div>
                <div className="divide-y divide-[var(--line-soft)]">
                  {nodes.map(n => (
                    <NodeRow key={n.stableId} node={n} cc={cc} ticks={ticks}
                      trailing={
                        <div className="flex items-center gap-1 shrink-0">
                          {n.source !== "manual" && (
                            <span className="text-[10px] text-[var(--t-faint)]" title="Из деплоя">авто</span>
                          )}
                          <button className="iconbtn" title="Скрыть из списка"
                            onClick={() => setHidden(n.stableId, true)}><EyeOff size={13} /></button>
                          {n.source === "manual" && (
                            <>
                              <button className="iconbtn" title="Редактировать"
                                onClick={() => setModal({ editing: n })}><Pencil size={13} /></button>
                              <button className="iconbtn danger" title="Удалить (сотрёт историю)"
                                onClick={() => { if (confirm("Удалить сервер? История проб будет стёрта.")) removeServer(n.stableId); }}>
                                <Trash2 size={13} /></button>
                            </>
                          )}
                        </div>
                      } />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {hiddenNodes.length > 0 && (
        <details className="rounded-xl border border-[var(--line-soft)] overflow-hidden">
          <summary className="flex items-center gap-2.5 px-4 py-2.5 bg-[var(--bg2)] cursor-pointer text-sm text-[var(--t-mid)]">
            <EyeOff size={14} className="text-[var(--t-low)]" />
            Скрытые ({hiddenNodes.length})
            <span className="text-[11px] text-[var(--t-faint)]">не влияют на счётчики и статус</span>
          </summary>
          <div className="divide-y divide-[var(--line-soft)]">
            {hiddenNodes.map(n => (
              <div key={n.stableId} className="flex items-center gap-2 px-4 py-2 text-sm">
                <span className="trunc flex-1 text-[var(--t-low)]">{n.name}</span>
                <span className="text-[11px] text-[var(--t-faint)]">{n.ip}</span>
                <button className="iconbtn" title="Показать снова"
                  onClick={() => setHidden(n.stableId, false)}><Eye size={13} /></button>
              </div>
            ))}
          </div>
        </details>
      )}

      <IncidentLog incidents={incidents} />

      {importing && (
        <ImportFromSubscription
          onClose={() => setImporting(false)}
          onImported={() => load(ticks)}
        />
      )}

      {modal !== null && (
        <ServerModal
          initial={modal.editing}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); load(ticks); }}
        />
      )}
    </>
  );
}

function ServerModal({ initial, onClose, onSaved }: {
  initial?: Node; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<SrvForm>(initial ? {
    name: initial.name || "", country: initial.country || "", ip: initial.ip || "",
    port: String(initial.port ?? 443), note: initial.note || "",
  } : SRV_EMPTY);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof SrvForm, v: string) => setForm(f => ({ ...f, [k]: v }));

  const save = async () => {
    if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(form.ip.trim())) { setErr("Некорректный IPv4-адрес"); return; }
    setSaving(true); setErr(null);
    const body = {
      name: form.name, country: form.country, ip: form.ip.trim(),
      port: Number(form.port) || 443, note: form.note,
    };
    try {
      const res = initial
        ? await fetch(`/api/server-monitor/servers/${initial.stableId}`, {
            method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
        : await fetch("/api/server-monitor/servers", {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) { setErr("Ошибка сохранения"); setSaving(false); return; }
      onSaved();
    } catch { setErr("Ошибка сети"); setSaving(false); }
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-md">
        <div className="shrink-0 flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
          <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
            {initial ? "Редактировать сервер" : "Новый сервер"}
          </h2>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-3">
          <Fld label="Название"><input className="input" value={form.name}
            onChange={e => set("name", e.target.value)} placeholder="Мой сервер" /></Fld>
          <Fld label="Страна (ISO, напр. DE)"><input className="input" value={form.country}
            onChange={e => set("country", e.target.value.toUpperCase().slice(0, 2))} placeholder="DE" /></Fld>
          <Fld label="IP-адрес"><input className="input font-mono" value={form.ip}
            onChange={e => set("ip", e.target.value)} placeholder="1.2.3.4" /></Fld>
          <Fld label="Порт (для TCP-проверки)"><input className="input" value={form.port}
            onChange={e => set("port", e.target.value.replace(/\D/g, ""))} placeholder="443" /></Fld>
          <Fld label="Примечание"><input className="input" value={form.note}
            onChange={e => set("note", e.target.value)} placeholder="—" /></Fld>
          {err && <p className="errmsg">{err}</p>}
        </div>
        <div className="shrink-0 flex justify-end gap-2 px-5 py-3.5" style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button onClick={onClose} className="btn btn-ghost">Отмена</button>
          <button onClick={save} disabled={saving || !form.ip.trim()} className="btn btn-primary">
            {saving ? <><Loader2 size={13} className="animate-spin" /> Сохранение…</> : <><Save size={13} /> Сохранить</>}
          </button>
        </div>
      </div>
    </div>
  );
}

function Fld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

// ── Tracked subscriptions (multi-subscription selector, Ф9) ───────────────
interface SubStatus {
  id: string; url: string; background: boolean; enabled: boolean;
  last_error: string | null; config_count: number | null;
}

function SubscriptionSelector() {
  const [subs,    setSubs]    = useState<SubStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [newUrl,  setNewUrl]  = useState("");
  const [adding,  setAdding]  = useState(false);
  const [addErr,  setAddErr]  = useState<string | null>(null);
  const [busyId,  setBusyId]  = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/subscriptions/status");
      const d = await res.json();
      setSubs(Array.isArray(d) ? d : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    timer.current = setInterval(load, 15_000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [load]);

  const toggleBackground = async (s: SubStatus) => {
    setBusyId(s.id);
    try {
      await fetch(`/api/subscriptions/${s.id}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ background: !s.background }),
      });
      await load();
    } catch { /* ignore */ }
    setBusyId(null);
  };

  const refreshSub = async (id: string) => {
    setBusyId(id);
    try { await fetch(`/api/subscriptions/${id}/refresh`, { method: "POST" }); await load(); }
    catch { /* ignore */ }
    setBusyId(null);
  };

  const removeSub = async (id: string) => {
    setBusyId(id);
    try { await fetch(`/api/subscriptions/${id}`, { method: "DELETE" }); await load(); }
    catch { /* ignore */ }
    setBusyId(null);
  };

  const addSub = async () => {
    const url = newUrl.trim();
    if (!url) return;
    setAdding(true); setAddErr(null);
    try {
      const res = await fetch("/api/subscriptions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, background: true }),
      });
      if (res.status === 422) {
        setAddErr("URL должен начинаться с http:// или https://");
      } else if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setAddErr(String(d.detail ?? "Ошибка"));
      } else {
        setNewUrl("");
        await load();
      }
    } catch (e) { setAddErr(String(e)); }
    setAdding(false);
  };

  return (
    <div className="card card-p mb-6">
      <p className="micro mb-3">Отслеживаемые подписки</p>

      {loading ? (
        <p className="text-xs text-[var(--t-faint)] py-2">Загрузка…</p>
      ) : subs.length === 0 ? (
        <p className="text-xs text-[var(--t-faint)] py-2">Нет отслеживаемых подписок — добавьте ссылку ниже.</p>
      ) : (
        <ul className="flex flex-col gap-2 mb-3">
          {subs.map(s => (
            <li key={s.id} className="flex flex-col gap-1.5 rounded-md px-3 py-2 bg-[var(--bg3)]">
              <div className="flex items-center gap-2.5">
                <button type="button" role="checkbox" aria-checked={s.background}
                  onClick={() => toggleBackground(s)} disabled={busyId === s.id}
                  className={`ck ${s.background ? "on" : ""}`} title="Фоновая подписка (в общий агрегат)">
                  {s.background && <Check size={11} />}
                </button>
                <span className="text-xs font-mono trunc flex-1 text-[var(--t-mid)]" title={s.url}>{s.url}</span>
                {s.config_count != null && (
                  <span className="chip neutral">{s.config_count} конфигов</span>
                )}
                <button type="button" onClick={() => refreshSub(s.id)} disabled={busyId === s.id}
                  className="iconbtn" title="Обновить">
                  {busyId === s.id ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                </button>
                <button type="button" onClick={() => removeSub(s.id)} disabled={busyId === s.id}
                  className="iconbtn danger" title="Удалить">
                  <Trash2 size={13} />
                </button>
              </div>
              {s.last_error && (
                <p className="text-[11px] text-[var(--err)]">Ошибка: {s.last_error}</p>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2">
        <input className="input" value={newUrl} onChange={e => setNewUrl(e.target.value)}
          placeholder="https://panel.example.com/sub/…"
          onKeyDown={e => { if (e.key === "Enter") addSub(); }} />
        <button type="button" onClick={addSub} disabled={adding} className="btn btn-primary">
          {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
        </button>
      </div>
      {addErr && <p className="errmsg mt-1">{addErr}</p>}
    </div>
  );
}

// ── Node row (compact status strip) ───────────────────────────
function NodeRow({ node, cc, ticks, trailing }: {
  node: Node; cc: string; ticks: number; trailing?: React.ReactNode;
}) {
  // Right-align bars: pad the left with "no-data" slots.
  const pad = Math.max(0, ticks - node.bars.length);
  // Subscription remarks often carry their own flag emoji ("🇳🇱 Амстердам").
  // Pull it out: it's more specific than the group's flag, and left inline it
  // would render as two bare letters on Windows.
  const own = splitFlagEmoji(node.name);
  return (
    <div className="ni-noderow flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--row-hover)]">
      {/* name + protocol */}
      <div className="ni-node-name flex items-center gap-2 min-w-0 w-52 shrink-0">
        <FlagChip code={own.code || cc} size={18} />
        <div className="min-w-0">
          {/* A remark that is ONLY a flag leaves nothing behind — fall back to the
              code so the row keeps a label instead of going blank. */}
          <p className="text-sm text-[var(--t-hi)] truncate">{own.rest || own.code || node.name}</p>
          {node.protocol && (
            <span className="inline-block text-[10px] uppercase tracking-wide text-[var(--t-low)]
                             bg-[var(--bg3)] rounded px-1.5 py-0.5 mt-0.5">{node.protocol}</span>
          )}
        </div>
      </div>

      {/* uptime bar grid */}
      <div className="ni-node-bars flex-1 flex items-end gap-[2px] h-7 min-w-0" title={`Последние ${ticks} проверок`}>
        {Array.from({ length: pad }).map((_, i) => (
          <span key={`p${i}`} className="flex-1 max-w-[6px] h-4 rounded-sm bg-[var(--bg3)]" />
        ))}
        {node.bars.map((b, i) => (
          <span key={i} className="flex-1 max-w-[6px] h-7 rounded-sm" style={{ background: barColor[b.status] }}
            title={`${fmtWhen(b.ts)} — ${b.status === "up" ? "OK" : b.status === "slow" ? "высокая задержка" : "недоступна"}`} />
        ))}
      </div>

      {/* ping */}
      <div className="w-16 text-right text-sm tabular-nums shrink-0" style={{ color: latencyColor(node.online, node.latencyMs) }}>
        {node.online ? `${node.latencyMs} мс` : "офлайн"}
      </div>

      {/* 30d uptime */}
      <div className="w-20 text-right text-sm tabular-nums shrink-0" style={{ color: uptimeColor(node.uptime30d) }}
        title="Аптайм за 30 дней">
        {node.uptime30d != null ? `${node.uptime30d}%` : "—"}
      </div>

      {trailing}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-widest opacity-60">{label}</p>
      <p className="text-xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="text-[10px] opacity-50 truncate max-w-[160px]">{sub}</p>}
    </div>
  );
}
