import { useCallback, useEffect, useState } from "react";
import { Route as RouteIcon, Plus, RefreshCw, Loader2, Pencil, Trash2, X } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi, asList } from "./api";
import { useHaproxyReady, NotConnected } from "./HaproxyConnect";
import { fmtBytes } from "./format";
import {
  emptyRouteDraft, routeToDraft, routePayload, validateDraft, QUOTA_PERIODS,
  type RouteDraft, type RouteMatchMode, type RouteTargetMode, type ProxyProtocol,
} from "./routeModel";
import type { NodeRecord, RouteRecord } from "./contracts";

const MATCH_LABEL: Record<string, string> = { any_tcp: "любой TCP", sni: "SNI", destination_ip: "IP назначения" };
const DEPLOY_LABEL: Record<string, string> = {
  deployed: "развёрнут", pending: "ожидает", deploying: "разворачивается", error: "ошибка", deleting: "удаляется",
};

export function HaproxyRoutes() {
  const { ready, loading: gate } = useHaproxyReady();
  const [nodes, setNodes] = useState<NodeRecord[]>([]);
  const [nodeId, setNodeId] = useState("");
  const [routes, setRoutes] = useState<RouteRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState<RouteRecord | "new" | null>(null);

  useEffect(() => {
    if (!ready) return;
    haproxyApi.nodes().then(v => {
      const list = asList<NodeRecord>(v, "nodes");
      setNodes(list);
      setNodeId(prev => prev || list[0]?.id || "");
    }).catch(() => {});
  }, [ready]);

  const loadRoutes = useCallback(async () => {
    if (!ready || !nodeId) { setRoutes([]); return; }
    setLoading(true); setErr("");
    try { setRoutes(asList<RouteRecord>(await haproxyApi.routes(nodeId), "routes")); }
    catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready, nodeId]);
  useEffect(() => { void loadRoutes(); }, [loadRoutes]);

  const toggleEnabled = async (r: RouteRecord) => {
    try { await haproxyApi.updateRoute(nodeId, r.id, { expected_version: r.version, enabled: !r.enabled }); await loadRoutes(); }
    catch (e: any) { setErr(e?.message || "Не удалось изменить"); }
  };
  const del = async (r: RouteRecord) => {
    try { await haproxyApi.deleteRoute(nodeId, r.id); await loadRoutes(); }
    catch (e: any) { setErr(e?.message || "Не удалось удалить"); }
  };

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  return (
    <Page>
      <PageHeader
        icon={<RouteIcon size={18} />}
        title="Маршруты HAProxy"
        subtitle="TCP-релеи: listener → backend, по SNI / IP / любому TCP"
        actions={
          <div className="flex items-center gap-2">
            <select className="selectbox !w-auto" value={nodeId} onChange={e => setNodeId(e.target.value)}>
              {nodes.length === 0 && <option value="">— нет нод —</option>}
              {nodes.map(n => <option key={n.id} value={n.id}>{n.name || n.id.slice(0, 8)} ({n.address})</option>)}
            </select>
            <button className="btn btn-soft" onClick={() => void loadRoutes()} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>
            <button className="btn btn-primary" onClick={() => setEditing("new")} disabled={!nodeId}><Plus size={14} /> Маршрут</button>
          </div>
        }
      />

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      {!nodeId ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">Сначала добавьте ноду в разделе «Ноды».</div>
      ) : routes.length === 0 ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">
          У этой ноды пока нет маршрутов. Создайте первый TCP-релей.
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="tbl w-full">
            <thead><tr>
              <th>Маршрут</th><th>Listener</th><th>Match</th><th>Target</th>
              <th className="r">Квота</th><th className="c">Статус</th><th className="c">Вкл</th><th></th>
            </tr></thead>
            <tbody>
              {routes.map(r => (
                <tr key={r.id}>
                  <td className="text-[var(--t-hi)]">{r.name || r.id.slice(0, 8)}
                    {r.fallback && <span className="chip neutral ml-2">fallback</span>}</td>
                  <td className="text-[var(--t-low)]">{r.listener_ip}:{r.listener_port}</td>
                  <td className="text-[var(--t-low)]">{MATCH_LABEL[r.match_mode] ?? r.match_mode}
                    {r.match_mode === "sni" && r.snis?.length ? <span className="text-[var(--t-faint)]"> · {r.snis.slice(0, 2).join(", ")}{r.snis.length > 2 ? "…" : ""}</span> : null}</td>
                  <td className="text-[var(--t-low)]">{r.target_type === "unix" ? r.unix_socket_path : `${r.target_host}:${r.target_port}`}</td>
                  <td className="r text-[var(--t-low)]">{r.quota_bytes ? fmtBytes(r.quota_bytes) : "—"}</td>
                  <td className="c">
                    <span className={`chip ${r.deployment_state === "error" ? "err" : r.deployed ? "ok" : "warn"}`}>
                      {DEPLOY_LABEL[r.deployment_state] ?? r.deployment_state ?? (r.deployed ? "развёрнут" : "ожидает")}
                    </span>
                  </td>
                  <td className="c">
                    <button className={`seg mini inline-flex ${r.enabled ? "accent" : ""}`} onClick={() => toggleEnabled(r)} title="Вкл/выкл">
                      <span className={`px-2 py-0.5 rounded ${r.enabled ? "on" : ""}`}>{r.enabled ? "вкл" : "выкл"}</span>
                    </button>
                  </td>
                  <td className="r whitespace-nowrap">
                    <button className="btn btn-ghost !p-1.5" onClick={() => setEditing(r)} title="Изменить"><Pencil size={14} /></button>
                    <button className="btn btn-ghost !p-1.5 text-[var(--err)]" onClick={() => del(r)} title="Удалить"><Trash2 size={14} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <RouteEditor nodeId={nodeId} route={editing === "new" ? null : editing}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void loadRoutes(); }} />
      )}
    </Page>
  );
}

function RouteEditor({ nodeId, route, onClose, onSaved }: {
  nodeId: string; route: RouteRecord | null; onClose: () => void; onSaved: () => void;
}) {
  const [d, setD] = useState<RouteDraft>(() => route ? routeToDraft(route) : emptyRouteDraft());
  const [enabled, setEnabled] = useState(route ? route.enabled : true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = <K extends keyof RouteDraft>(k: K, v: RouteDraft[K]) => setD(p => ({ ...p, [k]: v }));

  const save = async () => {
    const msg = validateDraft(d);
    if (msg) { setErr(msg); return; }
    setBusy(true); setErr("");
    try {
      const payload = routePayload(d, enabled, route?.version);
      if (route) await haproxyApi.updateRoute(nodeId, route.id, payload);
      else await haproxyApi.createRoute(nodeId, payload);
      onSaved();
    } catch (e: any) { setErr(e?.message || "Не удалось сохранить"); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onMouseDown={e => e.target === e.currentTarget && !busy && onClose()}>
      <div className="bg-[var(--bg1)] border border-[var(--line)] rounded-xl w-full max-w-lg p-5 max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-[var(--t-hi)]">{route ? "Изменить маршрут" : "Новый маршрут"}</h2>
          <button className="btn btn-ghost !p-1" onClick={() => !busy && onClose()}><X size={16} /></button>
        </div>

        <div className="flex flex-col gap-3">
          <L label="Имя маршрута"><input className="input" value={d.name} onChange={e => set("name", e.target.value)} placeholder="edge-tls" /></L>

          <L label="Режим сопоставления">
            <select className="selectbox" value={d.matchMode} onChange={e => set("matchMode", e.target.value as RouteMatchMode)}>
              <option value="any_tcp">Любой TCP (fallback)</option>
              <option value="sni">По SNI</option>
              <option value="destination_ip">По IP назначения</option>
            </select>
          </L>

          <div className="grid grid-cols-2 gap-3">
            <L label="Listener IP"><input className="input" value={d.listenerIP} onChange={e => set("listenerIP", e.target.value)} placeholder="*" spellCheck={false} /></L>
            <L label="Listener порт"><input className="input" value={d.listenerPort} onChange={e => set("listenerPort", e.target.value)} inputMode="numeric" /></L>
          </div>

          {d.matchMode === "sni" && (
            <L label="SNI (через запятую)"><textarea className="input" rows={2} value={d.snis} onChange={e => set("snis", e.target.value)}
              placeholder="a.example.com, b.example.com" spellCheck={false} /></L>
          )}

          <L label="Тип назначения">
            <div className="seg mini accent">
              {(["ip", "domain", "unix"] as RouteTargetMode[]).map(m => (
                <button key={m} className={d.targetMode === m ? "on" : ""} onClick={() => set("targetMode", m)}>
                  {m === "ip" ? "IP" : m === "domain" ? "Домен" : "Unix"}
                </button>
              ))}
            </div>
          </L>

          {d.targetMode === "unix" ? (
            <L label="Путь Unix-сокета"><input className="input" value={d.unixSocketPath} onChange={e => set("unixSocketPath", e.target.value)} placeholder="/run/service.sock" spellCheck={false} /></L>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <L label={d.targetMode === "ip" ? "IP назначения" : "Домен назначения"}>
                <input className="input" value={d.targetHost} onChange={e => set("targetHost", e.target.value)} placeholder={d.targetMode === "ip" ? "10.20.0.8" : "backend.internal"} spellCheck={false} /></L>
              <L label="Порт назначения"><input className="input" value={d.targetPort} onChange={e => set("targetPort", e.target.value)} inputMode="numeric" /></L>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <L label="PROXY-протокол">
              <select className="selectbox" value={d.proxyProtocol} onChange={e => set("proxyProtocol", e.target.value as ProxyProtocol)}>
                <option value="none">нет</option><option value="v1">send-proxy (v1)</option><option value="v2">send-proxy-v2</option>
              </select>
            </L>
            <label className="flex items-end gap-2 text-xs text-[var(--t-mid)] cursor-pointer pb-2">
              <input type="checkbox" checked={d.healthCheck} onChange={e => set("healthCheck", e.target.checked)} /> Health-check
            </label>
          </div>

          <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer">
            <input type="checkbox" checked={d.quotaEnabled} onChange={e => set("quotaEnabled", e.target.checked)} /> Лимит трафика (квота)
          </label>
          {d.quotaEnabled && (
            <div className="grid grid-cols-2 gap-3 pl-6">
              <L label="Лимит"><div className="flex gap-2">
                <input className="input" value={d.quotaValue} onChange={e => set("quotaValue", e.target.value)} inputMode="decimal" />
                <select className="selectbox !w-24" value={d.quotaUnit} onChange={e => set("quotaUnit", e.target.value as "GiB" | "TiB")}>
                  <option value="GiB">ГиБ</option><option value="TiB">ТиБ</option>
                </select>
              </div></L>
              <L label="Период">
                <select className="selectbox" value={d.quotaPeriod} onChange={e => set("quotaPeriod", e.target.value as RouteDraft["quotaPeriod"])}>
                  {QUOTA_PERIODS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </L>
              <label className="flex items-center gap-2 text-xs text-[var(--t-mid)] cursor-pointer col-span-2">
                <input type="checkbox" checked={d.quotaAction === "block_new"} onChange={e => set("quotaAction", e.target.checked ? "block_new" : "observe")} />
                Блокировать новые соединения при достижении лимита
              </label>
            </div>
          )}

          <details className="text-xs">
            <summary className="cursor-pointer text-[var(--t-low)]">Экспертный слой (backend-директивы HAProxy)</summary>
            <textarea className="input mt-2" rows={3} value={d.expertOverride} onChange={e => set("expertOverride", e.target.value)}
              placeholder="timeout server 1h" spellCheck={false} />
          </details>

          <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer">
            <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Маршрут включён
          </label>

          {err && <p className="text-xs text-[var(--err)]">{err}</p>}
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button className="btn btn-soft" onClick={onClose} disabled={busy}>Отмена</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy && <Loader2 size={14} className="animate-spin" />} {route ? "Сохранить" : "Создать"}
          </button>
        </div>
      </div>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-col gap-1"><label className="label">{label}</label>{children}</div>;
}
