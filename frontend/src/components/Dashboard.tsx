import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  Activity, Zap, RefreshCw, Loader2, ChevronDown, CheckCircle2,
  AlertTriangle, XCircle, Server, Clock,
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
const barColor: Record<TickStatus, string> = {
  up: "bg-green-500", slow: "bg-amber-500", down: "bg-red-500",
};
function uptimeColor(pct: number | null): string {
  if (pct == null) return "text-gray-500";
  if (pct >= 99.5) return "text-green-400";
  if (pct >= 98) return "text-yellow-400";
  return "text-red-400";
}
function latencyColor(online: boolean, ms: number): string {
  if (!online) return "text-gray-600";
  if (ms < 300) return "text-green-400";
  if (ms < 800) return "text-yellow-400";
  return "text-red-400";
}

// Global banner appearance per health state.
const BANNER: Record<GState, { cls: string; icon: React.ReactNode; text: string }> = {
  ok:      { cls: "border-green-800/50 bg-green-950/40 text-green-300", icon: <CheckCircle2 size={22} />, text: "Все узлы работают стабильно" },
  partial: { cls: "border-amber-800/50 bg-amber-950/40 text-amber-300", icon: <AlertTriangle size={22} />, text: "Частичные сбои" },
  down:    { cls: "border-red-800/50 bg-red-950/40 text-red-300",       icon: <XCircle size={22} />,       text: "Критическая нестабильность сети" },
  unknown: { cls: "border-gray-700/50 bg-gray-900/40 text-gray-400",    icon: <Activity size={22} />,      text: "Нет данных мониторинга" },
};

export function Dashboard() {
  const [data, setData]         = useState<StatusResp | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [ticks, setTicks]       = useState(30);
  const [checking, setChecking] = useState(false);
  const [loading, setLoading]   = useState(true);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (n: number) => {
    try {
      const [s, inc] = await Promise.all([
        fetch(`/api/checker/statuspage?ticks=${n}`).then(r => r.json()),
        fetch(`/api/checker/incidents?days=7`).then(r => r.json()).catch(() => ({ incidents: [] })),
      ]);
      setData(s);
      setIncidents(Array.isArray(inc.incidents) ? inc.incidents : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, []);

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
      <div className="max-w-5xl mx-auto px-6 py-6">

        {/* Header row */}
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-base font-semibold text-white flex items-center gap-2">
            <Activity size={16} className="text-blue-400" /> Статус нод сети
          </h1>
          <div className="flex items-center gap-2">
            <div className="flex rounded-md border border-gray-800 overflow-hidden">
              {[30, 60, 90].map(n => (
                <button key={n} onClick={() => setTicks(n)}
                  className={`px-2.5 py-1 text-[11px] font-medium transition-colors ${
                    ticks === n ? "bg-gray-800 text-white" : "text-gray-500 hover:text-gray-300"}`}>
                  {n}
                </button>
              ))}
            </div>
            <button onClick={() => load(ticks)}
              className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors" title="Обновить">
              <RefreshCw size={13} />
            </button>
            <button onClick={deepCheck} disabled={checking || !running}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
                         bg-blue-600 hover:bg-blue-500 text-white transition-colors
                         disabled:opacity-40 disabled:cursor-not-allowed">
              {checking ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
              Перепроверить все ноды
            </button>
          </div>
        </div>

        {/* Global health banner */}
        <div className={`rounded-xl border p-5 mb-6 flex items-center gap-4 ${banner.cls}`}>
          {banner.icon}
          <div className="flex-1">
            <p className="text-lg font-semibold">{banner.text}</p>
            <p className="text-xs opacity-70 mt-0.5">
              {running && g?.state
                ? `${g.online} из ${g.total} узлов онлайн`
                : loading ? "Загрузка…" : "xray-checker не запущен — настройте его в Настройки → Деплой"}
            </p>
          </div>
          <div className="flex items-center gap-6 text-right">
            <Stat label="Аптайм 30 дней" value={g?.uptime30d != null ? `${g.uptime30d}%` : "—"} />
            <Stat label="Активных протоколов" value={g?.protocols ? String(g.protocols.length) : "—"}
              sub={g?.protocols?.join(", ")} />
          </div>
        </div>

        {/* Node groups */}
        {groups.length === 0 ? (
          <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-8 text-center text-gray-600 text-sm">
            {running ? "Нет нод в подписке." : "Мониторинг неактивен."}
          </div>
        ) : groups.map(([country, nodes]) => {
          const isCollapsed = collapsed[country];
          const flag = flagFor(country);
          const anyDown = nodes.some(n => !n.online);
          return (
            <div key={country} className="mb-4 rounded-xl border border-gray-800 overflow-hidden">
              <button
                onClick={() => setCollapsed(c => ({ ...c, [country]: !c[country] }))}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 bg-gray-900/60 hover:bg-gray-900 transition-colors">
                <span className="text-lg leading-none">{flag}</span>
                <span className="text-sm font-medium text-gray-200">{country}</span>
                <span className="text-[11px] text-gray-600">Нод: {nodes.length}</span>
                {anyDown && <span className="w-1.5 h-1.5 rounded-full bg-red-400" />}
                <ChevronDown size={14} className={`ml-auto text-gray-600 transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
              </button>
              {!isCollapsed && (
                <div className="divide-y divide-gray-800/60">
                  {nodes.map(n => <NodeRow key={n.stableId} node={n} flag={flag} ticks={ticks} />)}
                </div>
              )}
            </div>
          );
        })}

        {/* Incident history */}
        <div className="mt-6 rounded-xl border border-gray-800 bg-gray-900/40 p-4">
          <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest mb-3 flex items-center gap-2">
            <Clock size={12} /> История доступности за последние 7 дней
          </p>
          {incidents.length === 0 ? (
            <p className="text-xs text-gray-600 py-3 text-center">Инцидентов не зафиксировано — все ноды были стабильны. ✓</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {incidents.slice(0, 50).map((it, i) => (
                <li key={i} className="flex items-start gap-2.5 text-xs">
                  <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${it.ongoing ? "bg-red-400 animate-pulse" : "bg-gray-600"}`} />
                  <span className="text-gray-500 tabular-nums shrink-0">{fmtWhen(it.start)}</span>
                  <span className="text-gray-300">
                    Нода <span className="text-white font-medium">{it.name}</span>
                    {it.group && <span className="text-gray-500"> ({it.group})</span>}
                    {it.ongoing
                      ? <span className="text-red-400"> недоступна сейчас</span>
                      : <> была недоступна в течение <span className="text-amber-300">{fmtDuration(it.durationSec)}</span></>}.
                    <span className="text-gray-600"> Причина: {it.reason}.</span>
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

// ── Node row (compact status strip) ───────────────────────────
function NodeRow({ node, flag, ticks }: { node: Node; flag: string; ticks: number }) {
  // Right-align bars: pad the left with "no-data" slots.
  const pad = Math.max(0, ticks - node.bars.length);
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-900/40">
      {/* name + protocol */}
      <div className="flex items-center gap-2 min-w-0 w-52 shrink-0">
        <span className="text-base leading-none">{flag}</span>
        <div className="min-w-0">
          <p className="text-sm text-gray-200 truncate">{node.name}</p>
          {node.protocol && (
            <span className="inline-block text-[10px] uppercase tracking-wide text-gray-500
                             bg-gray-800/70 rounded px-1.5 py-0.5 mt-0.5">{node.protocol}</span>
          )}
        </div>
      </div>

      {/* uptime bar grid */}
      <div className="flex-1 flex items-end gap-[2px] h-7 min-w-0" title={`Последние ${ticks} проверок`}>
        {Array.from({ length: pad }).map((_, i) => (
          <span key={`p${i}`} className="flex-1 max-w-[6px] h-4 rounded-sm bg-gray-800/70" />
        ))}
        {node.bars.map((b, i) => (
          <span key={i} className={`flex-1 max-w-[6px] h-7 rounded-sm ${barColor[b.status]}`}
            title={`${fmtWhen(b.ts)} — ${b.status === "up" ? "OK" : b.status === "slow" ? "высокая задержка" : "недоступна"}`} />
        ))}
      </div>

      {/* ping */}
      <div className={`w-16 text-right text-sm tabular-nums shrink-0 ${latencyColor(node.online, node.latencyMs)}`}>
        {node.online ? `${node.latencyMs} мс` : "офлайн"}
      </div>

      {/* 30d uptime */}
      <div className={`w-20 text-right text-sm tabular-nums shrink-0 ${uptimeColor(node.uptime30d)}`}
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
