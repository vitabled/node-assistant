import { useState, useEffect, type ReactNode } from "react";
import { TrendingUp, BarChart3, Users, ArrowRightLeft, ShieldCheck, Zap } from "lucide-react";
import { WidgetSettings } from "./WidgetSettings";

// «Статистика → Пользователи» — 6 widgets over the Ф3 stats routes + Ф1 checker
// routes. Inline SVG sparklines + CSS bars (no external chart lib — CSP self-
// contained). Reliable widgets use per-node usersOnline; migrations are
// BEST-EFFORT (topUsers membership) and carry an «оценка» badge.

const WINDOWS = [{ h: 24, label: "24 часа" }, { h: 168, label: "7 дней" }, { h: 720, label: "30 дней" }];

type NodeLoad = {
  node_uuid: string; node_name: string; avg_online: number;
  peak_online: number; current_online: number; points: { ts: number; usersOnline: number }[];
};
type Instance = { id: string; name: string; kind: string };
type CheckerNode = { stableId: string; name: string; online: boolean; latencyMs: number; uptime30d: number | null };

async function getJson(url: string): Promise<any> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function useFetch<T>(url: string | null): { data: T | null; err: boolean; loading: boolean } {
  const [s, setS] = useState<{ data: T | null; err: boolean; loading: boolean }>(
    { data: null, err: false, loading: true });
  useEffect(() => {
    if (!url) { setS({ data: null, err: false, loading: false }); return; }
    let live = true;
    setS(p => ({ ...p, loading: true, err: false }));
    getJson(url)
      .then(d => { if (live) setS({ data: d, err: false, loading: false }); })
      .catch(() => { if (live) setS({ data: null, err: true, loading: false }); });
    return () => { live = false; };
  }, [url]);
  return s;
}

function fmtBytes(n: number): string {
  if (!n) return "0";
  const u = ["Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

// ── shared UI atoms ──────────────────────────────────────────
function Card({ title, Icon, settings, children }:
  { title: string; Icon: typeof Users; settings?: ReactNode; children: ReactNode }) {
  return (
    <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <Icon size={15} style={{ color: "var(--accent)", flex: "none" }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }} className="trunc">{title}</span>
        {settings && <div style={{ marginLeft: "auto", flex: "none" }}>{settings}</div>}
      </div>
      {children}
    </div>
  );
}

function State({ loading, err, empty, children }:
  { loading: boolean; err: boolean; empty: boolean; children: ReactNode }) {
  if (loading) return <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Загрузка…</p>;
  if (err) return <p style={{ fontSize: 12, color: "var(--err)" }}>Не удалось загрузить данные</p>;
  if (empty) return <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Данных пока нет</p>;
  return <>{children}</>;
}

function Bar({ label, value, max, sub, color }:
  { label: string; value: number; max: number; sub?: string; color: string }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 12, marginBottom: 3 }}>
        <span className="trunc">{label}</span>
        <span className="num" style={{ color: "var(--t-low)", flex: "none" }}>{sub ?? value}</span>
      </div>
      <div style={{ height: 6, background: "var(--bg2)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color }} />
      </div>
    </div>
  );
}

function Sparkline({ points, color }: { points: number[]; color: string }) {
  if (points.length < 2) return <span style={{ fontSize: 11, color: "var(--t-faint)" }}>—</span>;
  const w = 116, h = 26;
  const max = Math.max(...points, 1), min = Math.min(...points, 0);
  const rng = max - min || 1;
  const d = points.map((v, i) =>
    `${((i / (points.length - 1)) * w).toFixed(1)},${(h - ((v - min) / rng) * h).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block", flex: "none" }}>
      <polyline points={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

function WindowSelect({ value, onChange }: { value: number; onChange: (h: number) => void }) {
  return (
    <label style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="dim">Период</span>
      <select className="selectbox" value={value} onChange={e => onChange(Number(e.target.value))}>
        {WINDOWS.map(w => <option key={w.h} value={w.h}>{w.label}</option>)}
      </select>
    </label>
  );
}

function CheckerSelect({ value, onChange, instances }:
  { value: string; onChange: (id: string) => void; instances: Instance[] }) {
  return (
    <label style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="dim">Инстанс мониторинга</span>
      <select className="selectbox" value={value} onChange={e => onChange(e.target.value)}>
        {instances.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}
      </select>
    </label>
  );
}

// ── widgets ──────────────────────────────────────────────────
function WNodeLoad() {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ nodes: NodeLoad[] }>(`/api/stats/users/node-load?hours=${hours}`);
  const nodes = data?.nodes ?? [];
  return (
    <Card title="Загрузка нод во времени" Icon={TrendingUp}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {nodes.slice(0, 6).map(n => (
            <div key={n.node_uuid} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="trunc" style={{ fontSize: 12 }}>{n.node_name || n.node_uuid.slice(0, 8)}</div>
                <div style={{ fontSize: 10, color: "var(--t-low)" }}>сейчас {n.current_online} · пик {n.peak_online}</div>
              </div>
              <Sparkline points={n.points.map(p => p.usersOnline)} color="var(--accent)" />
            </div>
          ))}
        </div>
      </State>
    </Card>
  );
}

function WAvgPerNode() {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ nodes: NodeLoad[] }>(`/api/stats/users/node-load?hours=${hours}`);
  const nodes = (data?.nodes ?? []).slice(0, 8);
  const max = Math.max(1, ...nodes.map(n => n.avg_online));
  return (
    <Card title="Среднее юзеров на ноду · самые загруженные" Icon={BarChart3}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0}>
        <div>
          {nodes.map(n => (
            <Bar key={n.node_uuid} label={n.node_name || n.node_uuid.slice(0, 8)}
              value={n.avg_online} max={max} sub={`⌀ ${n.avg_online}`} color="var(--accent)" />
          ))}
        </div>
      </State>
    </Card>
  );
}

function WTopUsers() {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ users: { username: string; total: number }[] }>(
    `/api/stats/users/top-users?hours=${hours}`);
  const users = data?.users ?? [];
  const max = Math.max(1, ...users.map(u => u.total));
  return (
    <Card title="Топ пользователей по нагрузке" Icon={Users}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={users.length === 0}>
        <div>
          {users.slice(0, 10).map(u => (
            <Bar key={u.username} label={u.username || "—"} value={u.total} max={max}
              sub={fmtBytes(u.total)} color="var(--accent)" />
          ))}
        </div>
      </State>
    </Card>
  );
}

function WMigrations({ nameMap }: { nameMap: Record<string, string> }) {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ migrations: { from_node: string; to_node: string; count: number }[] }>(
    `/api/stats/users/migrations?hours=${hours}`);
  const migs = data?.migrations ?? [];
  const nm = (id: string) => nameMap[id] || id.slice(0, 8);
  return (
    <Card title="Миграции пользователей" Icon={ArrowRightLeft}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <div style={{ marginTop: -6, marginBottom: 10 }}>
        <span className="chip" style={{ fontSize: 10, padding: "1px 7px" }}>оценка</span>
        <span style={{ fontSize: 10, color: "var(--t-faint)", marginLeft: 6 }}>приближённо, по нагрузке topUsers</span>
      </div>
      <State loading={loading} err={err} empty={migs.length === 0}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {migs.slice(0, 10).map((m, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
              <span className="trunc" style={{ flex: 1, textAlign: "right" }}>{nm(m.from_node)}</span>
              <ArrowRightLeft size={12} style={{ color: "var(--t-low)", flex: "none" }} />
              <span className="trunc" style={{ flex: 1 }}>{nm(m.to_node)}</span>
              <span className="num chip" style={{ flex: "none", fontSize: 10, padding: "0 6px" }}>{m.count}</span>
            </div>
          ))}
        </div>
      </State>
    </Card>
  );
}

function WStableNodes({ instances }: { instances: Instance[] }) {
  const [cid, setCid] = useState("local");
  const { data, err, loading } = useFetch<{ nodes: CheckerNode[] }>(`/api/checker/statuspage?checker_id=${cid}&ticks=30`);
  const nodes = [...(data?.nodes ?? [])]
    .filter(n => n.uptime30d != null)
    .sort((a, b) => (b.uptime30d ?? 0) - (a.uptime30d ?? 0)).slice(0, 8);
  return (
    <Card title="Самые стабильные ноды" Icon={ShieldCheck}
      settings={<WidgetSettings><CheckerSelect value={cid} onChange={setCid} instances={instances} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0}>
        <div>
          {nodes.map(n => (
            <Bar key={n.stableId} label={n.name} value={n.uptime30d ?? 0} max={100}
              sub={`${(n.uptime30d ?? 0).toFixed(1)}%`} color="var(--ok)" />
          ))}
        </div>
      </State>
    </Card>
  );
}

function WFastNodes({ instances }: { instances: Instance[] }) {
  const [cid, setCid] = useState("local");
  const { data, err, loading } = useFetch<{ nodes: CheckerNode[] }>(`/api/checker/statuspage?checker_id=${cid}&ticks=30`);
  const online = (data?.nodes ?? []).filter(n => n.online && n.latencyMs >= 0);
  const nodes = [...online].sort((a, b) => a.latencyMs - b.latencyMs).slice(0, 8);
  const max = Math.max(1, ...nodes.map(n => n.latencyMs));
  return (
    <Card title="Самые быстрые ноды" Icon={Zap}
      settings={<WidgetSettings><CheckerSelect value={cid} onChange={setCid} instances={instances} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0}>
        <div>
          {nodes.map(n => (
            <Bar key={n.stableId} label={n.name} value={max - n.latencyMs + 1} max={max}
              sub={`${n.latencyMs} мс`} color="var(--accent)" />
          ))}
        </div>
      </State>
    </Card>
  );
}

export function UsersStats() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [nameMap, setNameMap] = useState<Record<string, string>>({});
  useEffect(() => {
    getJson("/api/checker/instances").then(d => setInstances(d.instances ?? [{ id: "local", name: "Локальный чекер", kind: "local" }])).catch(() => setInstances([{ id: "local", name: "Локальный чекер", kind: "local" }]));
    getJson("/api/stats/users/node-load?hours=720").then(d => {
      const m: Record<string, string> = {};
      for (const n of (d.nodes ?? []) as NodeLoad[]) m[n.node_uuid] = n.node_name || n.node_uuid.slice(0, 8);
      setNameMap(m);
    }).catch(() => {});
  }, []);

  return (
    <div className="ni-pagebody" style={{ flex: 1, overflowY: "auto", padding: 20 }}>
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Users size={18} style={{ color: "var(--accent)" }} />
        <div>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Статистика пользователей</h1>
          <p style={{ fontSize: 12, color: "var(--t-low)" }}>Загрузка нод, миграции и качество — по историческим снимкам</p>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2" style={{ display: "grid", gap: 16 }}>
        <WNodeLoad />
        <WAvgPerNode />
        <WTopUsers />
        <WMigrations nameMap={nameMap} />
        <WStableNodes instances={instances} />
        <WFastNodes instances={instances} />
      </div>
    </div>
  );
}
