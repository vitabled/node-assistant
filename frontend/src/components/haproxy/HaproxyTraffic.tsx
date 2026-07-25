import { useCallback, useEffect, useState } from "react";
import { Gauge, RefreshCw, Loader2 } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi, asList } from "./api";
import { useHaproxyReady, NotConnected } from "./gate";
import { fmtBytes } from "./format";
import type { NodeRecord, NodeTraffic } from "./contracts";

export function HaproxyTraffic() {
  const { ready, loading: gate } = useHaproxyReady();
  const [nodes, setNodes] = useState<NodeRecord[]>([]);
  const [nodeId, setNodeId] = useState("");
  const [traffic, setTraffic] = useState<NodeTraffic | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!ready) return;
    haproxyApi.nodes().then(v => {
      const list = asList<NodeRecord>(v, "nodes");
      setNodes(list); setNodeId(prev => prev || list[0]?.id || "");
    }).catch(() => {});
  }, [ready]);

  const load = useCallback(async () => {
    if (!ready || !nodeId) { setTraffic(null); return; }
    setLoading(true); setErr("");
    try { setTraffic(await haproxyApi.traffic(nodeId)); }
    catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready, nodeId]);
  useEffect(() => { void load(); }, [load]);

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  return (
    <Page>
      <PageHeader
        icon={<Gauge size={18} />}
        title="Трафик HAProxy"
        subtitle="Учёт байт и квоты по нодам и маршрутам"
        actions={
          <div className="flex items-center gap-2">
            <select className="selectbox !w-auto" value={nodeId} onChange={e => setNodeId(e.target.value)}>
              {nodes.length === 0 && <option value="">— нет нод —</option>}
              {nodes.map(n => <option key={n.id} value={n.id}>{n.name || n.id.slice(0, 8)}</option>)}
            </select>
            <button className="btn btn-soft" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>
          </div>
        }
      />

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      {!traffic ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">
          {nodeId ? "Нет данных о трафике." : "Добавьте ноду в разделе «Ноды»."}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
            <Tile label="Месяц" value={traffic.month} />
            <Tile label="Входящий" value={fmtBytes(traffic.bytes_in)} />
            <Tile label="Исходящий" value={fmtBytes(traffic.bytes_out)} />
            <Tile label="Всего" value={fmtBytes(traffic.used_bytes)}
              hint={traffic.enforcement ? "квоты применяются" : "только учёт"} />
          </div>

          <div className="card overflow-x-auto">
            <div className="px-4 py-3 border-b border-[var(--line-soft)] text-sm font-medium text-[var(--t-hi)]">Маршруты</div>
            {(traffic.routes?.length ?? 0) === 0 ? (
              <p className="px-4 py-6 text-center text-xs text-[var(--t-low)]">Нет маршрутов с трафиком.</p>
            ) : (
              <table className="tbl w-full">
                <thead><tr>
                  <th>Backend</th><th className="r">Трафик</th><th className="r">Квота</th>
                  <th className="c">Достигнут</th><th className="c">Блок</th>
                </tr></thead>
                <tbody>
                  {traffic.routes.map(r => (
                    <tr key={r.route_id}>
                      <td className="text-[var(--t-hi)]">{r.backend_key || r.route_id.slice(0, 8)}</td>
                      <td className="r">{fmtBytes(r.used_bytes)}</td>
                      <td className="r text-[var(--t-low)]">
                        {r.limit_bytes ? `${fmtBytes(r.quota_used_bytes)} / ${fmtBytes(r.limit_bytes)}` : "—"}
                        {r.quota_period ? <span className="text-[var(--t-faint)]"> · {r.quota_period}</span> : null}
                      </td>
                      <td className="c">{r.reached ? <span className="chip warn">да</span> : <span className="text-[var(--t-faint)]">—</span>}</td>
                      <td className="c">{r.blocked ? <span className="chip err">блок</span> : <span className="text-[var(--t-faint)]">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </Page>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="text-[11px] uppercase tracking-wide text-[var(--t-low)] font-semibold">{label}</div>
      <div className="text-lg font-semibold text-[var(--t-hi)] mt-1.5 tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-[var(--t-low)] mt-0.5">{hint}</div>}
    </div>
  );
}
