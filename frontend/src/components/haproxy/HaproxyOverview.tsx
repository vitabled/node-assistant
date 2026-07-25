import { useCallback, useEffect, useState } from "react";
import { LayoutDashboard, RefreshCw, Loader2, Boxes, Route as RouteIcon, Activity, Gauge } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi } from "./api";
import { useHaproxyReady, NotConnected } from "./HaproxyConnect";
import { fmtBytes, fmtBps } from "./format";
import type { DashboardOverview } from "./contracts";

const RANGES = [["1h", "1ч"], ["24h", "24ч"], ["7d", "7д"], ["30d", "30д"]] as const;

export function HaproxyOverview() {
  const { ready, loading: gate } = useHaproxyReady();
  const [range, setRange] = useState("24h");
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    if (!ready) return;
    setLoading(true); setErr("");
    try { setData(await haproxyApi.overview(range)); }
    catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready, range]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!ready) return;
    const t = setInterval(() => { void load(); }, 15000);
    return () => clearInterval(t);
  }, [ready, load]);

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  const t = data?.totals;
  return (
    <Page>
      <PageHeader
        icon={<LayoutDashboard size={18} />}
        title="Обзор HAProxy"
        subtitle="Сводка нод, маршрутов и трафика NodeFlow"
        actions={
          <div className="flex items-center gap-2">
            <div className="seg mini accent">
              {RANGES.map(([v, l]) => (
                <button key={v} className={range === v ? "on" : ""} onClick={() => setRange(v)}>{l}</button>
              ))}
            </div>
            <button className="btn btn-soft" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>
          </div>
        }
      />

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <Kpi icon={<Boxes size={15} />} label="Ноды" value={`${t?.nodes_online ?? 0} / ${t?.nodes_total ?? 0}`}
          hint={`${t?.nodes_degraded ?? 0} деград · ${t?.nodes_offline ?? 0} офлайн`} />
        <Kpi icon={<RouteIcon size={15} />} label="Маршруты" value={`${t?.routes_total ?? 0}`}
          hint={`${t?.connections_current ?? 0} соединений`} />
        <Kpi icon={<Activity size={15} />} label="Входящий" value={fmtBps(t?.rx_bits_per_second)}
          hint={`↑ ${fmtBps(t?.tx_bits_per_second)}`} />
        <Kpi icon={<Gauge size={15} />} label="Трафик за месяц" value={fmtBytes(t?.traffic_month_bytes)}
          hint={`бэкенды: ${t?.backends_healthy ?? 0}✓ ${t?.backends_degraded ?? 0}⚠ ${t?.backends_unavailable ?? 0}✕`} />
      </div>

      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-[var(--line-soft)] text-sm font-medium text-[var(--t-hi)]">
          Топ маршрутов по трафику
        </div>
        {(data?.top_routes?.length ?? 0) === 0 ? (
          <p className="px-4 py-6 text-center text-xs text-[var(--t-low)]">Нет данных о трафике</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="tbl w-full">
              <thead><tr>
                <th>Маршрут</th><th>Нода</th><th>Слушатель</th>
                <th className="r">Трафик</th><th className="r">Скорость</th><th className="r">Доля</th>
              </tr></thead>
              <tbody>
                {data!.top_routes.map(r => (
                  <tr key={r.route_id}>
                    <td className="text-[var(--t-hi)]">{r.name || r.route_id.slice(0, 8)}
                      {r.fallback && <span className="chip neutral ml-2">fallback</span>}</td>
                    <td className="text-[var(--t-low)]">{r.node_name}</td>
                    <td className="text-[var(--t-low)]">{r.listener_ip}:{r.listener_port}
                      {r.snis?.length ? <span className="text-[var(--t-faint)]"> · {r.snis.slice(0, 2).join(", ")}</span> : null}</td>
                    <td className="r">{fmtBytes(r.used_bytes)}</td>
                    <td className="r">{fmtBps(r.bits_per_second)}</td>
                    <td className="r text-[var(--t-low)]">{(r.share_percent ?? 0).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Page>
  );
}

function Kpi({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-1.5 text-[var(--t-low)] text-[11px] uppercase tracking-wide font-semibold">{icon}{label}</div>
      <div className="text-lg font-semibold text-[var(--t-hi)] mt-1.5 tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-[var(--t-low)] mt-0.5">{hint}</div>}
    </div>
  );
}
