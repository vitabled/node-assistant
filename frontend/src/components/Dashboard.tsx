import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  Activity, Zap, RefreshCw, Loader2, ChevronDown, CheckCircle2,
  AlertTriangle, XCircle, Server, Clock,
  Check, Plus, Trash2,
} from "lucide-react";
import { COUNTRIES } from "./CountrySelect";
import { getFlagEmoji } from "../utils/format";

// ── Types (mirror /api/checker/statuspage + /incidents) ───────
type TickStatus = "up" | "slow" | "down";
interface Bar { ts: number; status: TickStatus }
interface Node {
  stableId: string; name: string; groupName: string; protocol: string;
  online: boolean; latencyMs: number; uptime30d: number | null; bars: Bar[];
}
type GState = "ok" | "partial" | "down" | "unknown";
interface Global {
  state: GState; uptime30d: number | null; protocols: string[];
  total: number; online: number; offline: number;
}
interface StatusResp { container: string; reachable: boolean; nodes: Node[]; global: Global }
interface Incident {
  stableId: string; name: string; group: string; start: number; end: number;
  durationSec: number; reason: string; ongoing: boolean;
}

// ── Helpers ───────────────────────────────────────────────────
function extractFlag(s: string): string | null {
  const arr = Array.from(s);
  for (let i = 0; i < arr.length - 1; i++) {
    const a = arr[i].codePointAt(0)!, b = arr[i + 1].codePointAt(0)!;
    if (a >= 0x1F1E6 && a <= 0x1F1FF && b >= 0x1F1E6 && b <= 0x1F1FF) return arr[i] + arr[i + 1];
  }
  return null;
}
// Resolve a flag from a node's location group. xray-checker gives a free-form
// groupName (a 2-letter code, a country name, or a string with an embedded
// flag), so we try each and always route the final code through getFlagEmoji.
function flagFor(group: string): string {
  const embedded = extractFlag(group);          // already-a-flag emoji
  if (embedded) return embedded;
  const g = group.trim();
  if (/^[A-Za-z]{2}$/.test(g)) return getFlagEmoji(g);   // raw ISO code, e.g. "US"
  const gl = g.toLowerCase();
  const match = COUNTRIES.find(c =>
    c.code !== "XX" && (c.name.toLowerCase() === gl || gl.includes(c.name.toLowerCase())));
  return match ? getFlagEmoji(match.code) : "🌐";
}
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

export function Dashboard() {
  const [data, setData]         = useState<StatusResp | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [ticks, setTicks]       = useState(30);
  const [checking, setChecking] = useState(false);
  const [loading, setLoading]   = useState(true);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [instances, setInstances] = useState<{ id: string; name: string }[]>([]);
  const [checkerId, setCheckerId] = useState("local");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Global checker-instance selector (Ф2): pick which instance the status page shows.
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
  // The checker can be `running` yet unreachable → backend returns `global: {}`
  // (an incomplete object, not the declared full `Global`). Gate on `g.state`
  // being present so an empty global degrades to "unknown" instead of indexing
  // BANNER with undefined and crashing the whole tree.
  const state: GState = running && g?.state ? g.state : "unknown";
  const banner = BANNER[state];

  // Group nodes by country (groupName).
  const groups = useMemo(() => {
    const map = new Map<string, Node[]>();
    (data?.nodes ?? []).forEach(n => {
      const key = n.groupName || "Прочее";
      (map.get(key) ?? map.set(key, []).get(key)!).push(n);
    });
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [data]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="ni-pagebody max-w-5xl mx-auto px-6 py-6">

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

        {/* Tracked subscriptions (multi-subscription aggregation, Ф9) */}
        <SubscriptionSelector />

        {/* Global health banner */}
        <div className="ni-health rounded-xl border p-5 mb-6 flex items-center gap-4" style={banner.style}>
          {banner.icon}
          <div className="flex-1">
            <p className="text-lg font-semibold">{banner.text}</p>
            <p className="text-xs opacity-70 mt-0.5">
              {running && g?.state
                ? `${g.online} из ${g.total} узлов онлайн`
                : loading ? "Загрузка…" : "Мониторинг не запущен — включите его в настройках мониторинга выше"}
            </p>
          </div>
          <div className="ni-health-stats flex items-center gap-6 text-right">
            <Stat label="Аптайм 30 дней" value={g?.uptime30d != null ? `${g.uptime30d}%` : "—"} />
            <Stat label="Активных протоколов" value={g?.protocols ? String(g.protocols.length) : "—"}
              sub={g?.protocols?.join(", ")} />
          </div>
        </div>

        {/* Node groups */}
        {groups.length === 0 ? (
          <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-8 text-center text-[var(--t-faint)] text-sm">
            {running ? "Нет нод в подписке." : "Мониторинг неактивен."}
          </div>
        ) : groups.map(([country, nodes]) => {
          const isCollapsed = collapsed[country];
          const flag = flagFor(country);
          const anyDown = nodes.some(n => !n.online);
          return (
            <div key={country} className="mb-4 rounded-xl border border-[var(--line-soft)] overflow-hidden">
              <button
                onClick={() => setCollapsed(c => ({ ...c, [country]: !c[country] }))}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-[var(--bg2)] hover:bg-[var(--bg3)] transition-colors">
                <span className="text-lg leading-none">{flag}</span>
                <span className="text-sm font-medium text-[var(--t-hi)]">{country}</span>
                <span className="text-[11px] text-[var(--t-faint)]">Нод: {nodes.length}</span>
                {anyDown && <span className="w-1.5 h-1.5 rounded-full bg-[var(--err)]" />}
                <ChevronDown size={14} className={`ml-auto text-[var(--t-faint)] transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
              </button>
              {!isCollapsed && (
                <div className="divide-y divide-[var(--line-soft)]">
                  {nodes.map(n => <NodeRow key={n.stableId} node={n} flag={flag} ticks={ticks} />)}
                </div>
              )}
            </div>
          );
        })}

        {/* Incident history */}
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

      </div>
    </div>
  );
}

// CheckerControls moved to components/monitoring/CheckerControls.tsx (Ф2).

// ── Tracked subscriptions (multi-subscription selector, Ф9) ───────────────
// Manages the account's subscription set fed into the shared subs-aggregator.

interface SubStatus {
  id: string;
  url: string;
  background: boolean;
  enabled: boolean;
  last_error: string | null;
  config_count: number | null;
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
function NodeRow({ node, flag, ticks }: { node: Node; flag: string; ticks: number }) {
  // Right-align bars: pad the left with "no-data" slots.
  const pad = Math.max(0, ticks - node.bars.length);
  return (
    <div className="ni-noderow flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--row-hover)]">
      {/* name + protocol */}
      <div className="ni-node-name flex items-center gap-2 min-w-0 w-52 shrink-0">
        <span className="text-base leading-none">{flag}</span>
        <div className="min-w-0">
          <p className="text-sm text-[var(--t-hi)] truncate">{node.name}</p>
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
