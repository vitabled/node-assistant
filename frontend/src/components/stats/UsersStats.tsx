import { useState, useEffect, type ReactNode } from "react";
import { Pencil, Plus, Trash2, ChevronUp, ChevronDown, Columns2, EyeOff } from "lucide-react";
import {
  useStatWidgets, WIDGET_KINDS, type WidgetKind,
  filterNodeLoad, filterMigrations, filterCheckerNodes, hiddenCount,
} from "./statWidgetsStore";
import { HiddenServers } from "./HiddenServers";
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

function State({ loading, err, empty, allHidden, children }:
  { loading: boolean; err: boolean; empty: boolean; allHidden?: boolean; children: ReactNode }) {
  if (loading) return <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Загрузка…</p>;
  if (err) return <p style={{ fontSize: 12, color: "var(--err)" }}>Не удалось загрузить данные</p>;
  // «Данных нет» и «всё скрыто вручную» — разные состояния: во втором случае
  // пользователь иначе решит, что статистика сломалась.
  if (empty && allHidden) return <p style={{ fontSize: 12, color: "var(--t-faint)" }}>Все серверы скрыты</p>;
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
        <option value="server-monitor">Server uptime (по IP)</option>
      </select>
    </label>
  );
}

// ── widgets ──────────────────────────────────────────────────

// Fixed data-ink hues for the multi-line node-load chart (per CLAUDE.md, chart
// palettes stay fixed hues; other UI uses CSS-var tokens).
const LINE_COLORS = ["#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa", "#f87171"];

function _fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleString("ru-RU",
    { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// Large, readable multi-line chart (6a): Y-axis + grid + time labels + legend +
// hover tooltip. Pure inline SVG (CSP self-contained). Each node = one line.
function NodeLoadChart({ nodes }: { nodes: NodeLoad[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = nodes.slice(0, 6).filter(n => n.points.length);
  const W = 640, H = 240, padL = 34, padR = 12, padT = 12, padB = 28;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  let tMin = Infinity, tMax = -Infinity, vMax = 1;
  for (const n of shown) for (const p of n.points) {
    if (p.ts < tMin) tMin = p.ts; if (p.ts > tMax) tMax = p.ts;
    if (p.usersOnline > vMax) vMax = p.usersOnline;
  }
  const span = Math.max(1, tMax - tMin);
  const x = (ts: number) => padL + ((ts - tMin) / span) * plotW;
  const y = (v: number) => padT + (1 - v / vMax) * plotH;

  // Merged sorted timeline for the hover guide.
  const allTs = Array.from(new Set(shown.flatMap(n => n.points.map(p => p.ts)))).sort((a, b) => a - b);
  const hoverTs = hover != null ? allTs[hover] : null;
  const valAt = (n: NodeLoad, ts: number) => {
    let best = n.points[0], bd = Infinity;
    for (const p of n.points) { const d = Math.abs(p.ts - ts); if (d < bd) { bd = d; best = p; } }
    return best?.usersOnline ?? 0;
  };

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => Math.round(vMax * f));
  const xTicks = [0, 1, 2, 3].map(i => tMin + (span * i) / 3);

  return (
    <div style={{ position: "relative", width: "100%" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={e => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          const ts = tMin + ((px - padL) / plotW) * span;
          if (!allTs.length) return;
          let bi = 0, bd = Infinity;
          allTs.forEach((t, i) => { const d = Math.abs(t - ts); if (d < bd) { bd = d; bi = i; } });
          setHover(bi);
        }}>
        {/* horizontal grid + Y labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} stroke="var(--line-soft)" strokeWidth={1} />
            <text x={padL - 5} y={y(v) + 3} textAnchor="end" fontSize={9} fill="var(--t-faint)">{v}</text>
          </g>
        ))}
        {/* X time labels */}
        {xTicks.map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === 3 ? "end" : "middle"}
            fontSize={9} fill="var(--t-faint)">{_fmtTs(t)}</text>
        ))}
        {/* node lines */}
        {shown.map((n, ni) => (
          <polyline key={n.node_uuid} fill="none" stroke={LINE_COLORS[ni % LINE_COLORS.length]}
            strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round"
            points={n.points.map(p => `${x(p.ts)},${y(p.usersOnline)}`).join(" ")} />
        ))}
        {/* hover guide */}
        {hoverTs != null && (
          <line x1={x(hoverTs)} x2={x(hoverTs)} y1={padT} y2={padT + plotH}
            stroke="var(--t-faint)" strokeWidth={1} strokeDasharray="3 3" />
        )}
        {hoverTs != null && shown.map((n, ni) => (
          <circle key={n.node_uuid} cx={x(hoverTs)} cy={y(valAt(n, hoverTs))} r={2.5}
            fill={LINE_COLORS[ni % LINE_COLORS.length]} />
        ))}
      </svg>

      {/* hover tooltip */}
      {hoverTs != null && (
        <div style={{
          position: "absolute", top: 4, right: 4, background: "var(--bg1)",
          border: "1px solid var(--line-soft)", borderRadius: "var(--r-sm)",
          padding: "6px 8px", fontSize: 10, pointerEvents: "none", boxShadow: "var(--shadow-pop)",
        }}>
          <div style={{ color: "var(--t-low)", marginBottom: 3 }}>{_fmtTs(hoverTs)}</div>
          {shown.map((n, ni) => (
            <div key={n.node_uuid} style={{ display: "flex", alignItems: "center", gap: 5 }}>
              <span style={{ width: 7, height: 7, borderRadius: 2, background: LINE_COLORS[ni % LINE_COLORS.length], flex: "none" }} />
              <span className="trunc" style={{ flex: 1, maxWidth: 110 }}>{n.node_name || n.node_uuid.slice(0, 8)}</span>
              <span className="num" style={{ color: "var(--t-hi)" }}>{valAt(n, hoverTs)}</span>
            </div>
          ))}
        </div>
      )}

      {/* legend with current / peak */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", marginTop: 8 }}>
        {shown.map((n, ni) => (
          <div key={n.node_uuid} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
            <span style={{ width: 9, height: 3, borderRadius: 2, background: LINE_COLORS[ni % LINE_COLORS.length], flex: "none" }} />
            <span className="trunc" style={{ maxWidth: 130 }}>{n.node_name || n.node_uuid.slice(0, 8)}</span>
            <span style={{ color: "var(--t-low)" }}>сейчас {n.current_online} · пик {n.peak_online}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WNodeLoad() {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ nodes: NodeLoad[] }>(`/api/stats/users/node-load?hours=${hours}`);
  const hidden = useStatWidgets(s => s.hidden);
  const raw = data?.nodes ?? [];
  const nodes = filterNodeLoad(hidden, raw);
  return (
    <Card title="Загрузка нод во времени" Icon={TrendingUp}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0} allHidden={raw.length > 0}>
        <NodeLoadChart nodes={nodes} />
      </State>
    </Card>
  );
}

function WAvgPerNode() {
  const [hours, setHours] = useState(168);
  const { data, err, loading } = useFetch<{ nodes: NodeLoad[] }>(`/api/stats/users/node-load?hours=${hours}`);
  const hidden = useStatWidgets(s => s.hidden);
  const raw = data?.nodes ?? [];
  // Фильтруем ДО среза top-N, иначе скрытая нода занимала бы место видимой.
  const nodes = filterNodeLoad(hidden, raw).slice(0, 8);
  const max = Math.max(1, ...nodes.map(n => n.avg_online));
  return (
    <Card title="Среднее юзеров на ноду · самые загруженные" Icon={BarChart3}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0} allHidden={raw.length > 0}>
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
  const hidden = useStatWidgets(s => s.hidden);
  const rawMigs = data?.migrations ?? [];
  const migs = filterMigrations(hidden, rawMigs);
  const nm = (id: string) => nameMap[id] || id.slice(0, 8);
  return (
    <Card title="Миграции пользователей" Icon={ArrowRightLeft}
      settings={<WidgetSettings><WindowSelect value={hours} onChange={setHours} /></WidgetSettings>}>
      <div style={{ marginTop: -6, marginBottom: 10 }}>
        <span className="chip" style={{ fontSize: 10, padding: "1px 7px" }}>оценка</span>
        <span style={{ fontSize: 10, color: "var(--t-faint)", marginLeft: 6 }}>приближённо, по нагрузке topUsers</span>
      </div>
      <State loading={loading} err={err} empty={migs.length === 0} allHidden={rawMigs.length > 0}>
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

// Server-uptime uses its own status-page route; xray checkers use the checker one.
function _statusUrl(cid: string): string {
  return cid === "server-monitor"
    ? "/api/server-monitor/statuspage?ticks=30"
    : `/api/checker/statuspage?checker_id=${cid}&ticks=30`;
}

function WStableNodes({ instances }: { instances: Instance[] }) {
  const [cid, setCid] = useState("local");
  const { data, err, loading } = useFetch<{ nodes: CheckerNode[] }>(_statusUrl(cid));
  const hidden = useStatWidgets(s => s.hidden);
  const raw = data?.nodes ?? [];
  const nodes = filterCheckerNodes(hidden, cid, raw)
    .filter(n => n.uptime30d != null)
    .sort((a, b) => (b.uptime30d ?? 0) - (a.uptime30d ?? 0)).slice(0, 8);
  return (
    <Card title="Самые стабильные ноды" Icon={ShieldCheck}
      settings={<WidgetSettings><CheckerSelect value={cid} onChange={setCid} instances={instances} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0} allHidden={raw.length > 0}>
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
  const { data, err, loading } = useFetch<{ nodes: CheckerNode[] }>(_statusUrl(cid));
  const hidden = useStatWidgets(s => s.hidden);
  const raw = data?.nodes ?? [];
  const online = filterCheckerNodes(hidden, cid, raw).filter(n => n.online && n.latencyMs >= 0);
  const nodes = [...online].sort((a, b) => a.latencyMs - b.latencyMs).slice(0, 8);
  const max = Math.max(1, ...nodes.map(n => n.latencyMs));
  return (
    <Card title="Самые быстрые ноды" Icon={Zap}
      settings={<WidgetSettings><CheckerSelect value={cid} onChange={setCid} instances={instances} /></WidgetSettings>}>
      <State loading={loading} err={err} empty={nodes.length === 0} allHidden={raw.length > 0}>
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

// Widget registry (Wave-5 Plan G) — the store holds only layout; kind→component here.
interface WidgetCtx { nameMap: Record<string, string>; instances: Instance[] }
const WIDGETS: Record<WidgetKind, { title: string; render: (c: WidgetCtx) => ReactNode }> = {
  "node-load":     { title: "Загрузка нод",       render: () => <WNodeLoad /> },
  "avg-per-node":  { title: "Среднее и пик",      render: () => <WAvgPerNode /> },
  "top-users":     { title: "Топ пользователей",  render: () => <WTopUsers /> },
  "migrations":    { title: "Миграции",           render: c => <WMigrations nameMap={c.nameMap} /> },
  "stable-nodes":  { title: "Стабильные ноды",    render: c => <WStableNodes instances={c.instances} /> },
  "fast-nodes":    { title: "Быстрые ноды",       render: c => <WFastNodes instances={c.instances} /> },
};

export function UsersStats() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [nameMap, setNameMap] = useState<Record<string, string>>({});
  const { layout, hidden, editing, hydrate, setEditing, add, remove, resize, move } = useStatWidgets();
  const [palette, setPalette] = useState(false);
  const [pickServers, setPickServers] = useState(false);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  useEffect(() => { hydrate(); }, [hydrate]);
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
      {pickServers && (
        <HiddenServers nameMap={nameMap} instances={instances} onClose={() => setPickServers(false)} />
      )}
      <div className="ni-pagehead" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
        <Users size={18} style={{ color: "var(--accent)" }} />
        <div style={{ flex: 1 }}>
          <h1 style={{ fontSize: 16, fontWeight: 700, color: "var(--t-hi)" }}>Статистика пользователей</h1>
          <p style={{ fontSize: 12, color: "var(--t-low)" }}>Загрузка нод, миграции и качество — по историческим снимкам</p>
        </div>
        <div className="ni-pagehead-actions" style={{ display: "flex", gap: 6 }}>
          {editing && (
            <div style={{ position: "relative" }}>
              <button className="btn btn-sm" onClick={() => setPalette(p => !p)}><Plus size={13} /> Виджет</button>
              {palette && (
                <div className="card" style={{ position: "absolute", right: 0, top: "110%", zIndex: 20, padding: 6, display: "flex", flexDirection: "column", gap: 2, minWidth: 190 }}>
                  {WIDGET_KINDS.map(k => (
                    <button key={k} className="navitem" style={{ textAlign: "left" }} onClick={() => { add(k); setPalette(false); }}>
                      {WIDGETS[k].title}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <button className="btn btn-sm" onClick={() => setPickServers(true)}>
            <EyeOff size={13} /> Серверы
            {hiddenCount(hidden) > 0 && (
              <span className="chip" style={{ marginLeft: 6, fontSize: 10, padding: "0 6px" }}>{hiddenCount(hidden)}</span>
            )}
          </button>
          <button className={`btn btn-sm ${editing ? "btn-primary" : ""}`} onClick={() => setEditing(!editing)}>
            <Pencil size={13} /> {editing ? "Готово" : "Редактировать"}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2" style={{ display: "grid", gap: 16 }}>
        {[...layout].sort((a, b) => a.order - b.order).map(inst => {
          const def = WIDGETS[inst.kind];
          if (!def) return null;
          return (
            <div key={inst.instanceId} style={{ gridColumn: inst.w === 2 ? "1 / -1" : undefined, position: "relative" }}>
              {editing && (
                <div style={{ position: "absolute", top: 6, right: 6, zIndex: 10, display: "flex", gap: 2,
                  background: "var(--bg2)", border: "1px solid var(--line-soft)", borderRadius: "var(--r-sm)", padding: 2 }}>
                  <button className="iconbtn" title="Вверх" onClick={() => move(inst.instanceId, -1)}><ChevronUp size={13} /></button>
                  <button className="iconbtn" title="Вниз" onClick={() => move(inst.instanceId, 1)}><ChevronDown size={13} /></button>
                  <button className="iconbtn" title="Ширина 1↔2" onClick={() => resize(inst.instanceId, inst.w === 2 ? 1 : 2)}><Columns2 size={13} /></button>
                  <button className={`iconbtn ${confirmDel === inst.instanceId ? "text-[var(--err)]" : ""}`} title="Удалить"
                    onClick={() => {
                      if (confirmDel === inst.instanceId) { remove(inst.instanceId); setConfirmDel(null); }
                      else { setConfirmDel(inst.instanceId); setTimeout(() => setConfirmDel(c => (c === inst.instanceId ? null : c)), 3000); }
                    }}>
                    <Trash2 size={13} />
                  </button>
                </div>
              )}
              {def.render({ nameMap, instances })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
