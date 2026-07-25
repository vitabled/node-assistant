import { useCallback, useEffect, useState } from "react";
import { Cpu, MemoryStick, Activity, Clock, Power, Trash2, KeyRound, Loader2, RouteIcon } from "lucide-react";
import { haproxyApi } from "./api";
import { fmtBytes, fmtBps, fmtPct, fmtUptime } from "./format";
import type { NodeOperational } from "./contracts";

const CONTROL_LABEL: Record<string, string> = {
  active: "активен", reloading: "перезагрузка", inactive: "остановлен", failed: "сбой",
  activating: "запуск", deactivating: "остановка", unknown: "неизвестно",
};

export function HaproxyNodeDetail({ nodeId, onChanged, onDeleted }: {
  nodeId: string; onChanged: () => void; onDeleted: () => void;
}) {
  const [op, setOp] = useState<NodeOperational | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try { setOp(await haproxyApi.operational(nodeId)); }
    catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [nodeId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const t = setInterval(() => { void load(); }, 15000);
    return () => clearInterval(t);
  }, [load]);

  const toggleHaproxy = async (enabled: boolean) => {
    setBusy("haproxy"); setErr("");
    try { await haproxyApi.setHaproxy(nodeId, enabled); await load(); onChanged(); }
    catch (e: any) { setErr(e?.message || "Не удалось изменить HAProxy"); }
    finally { setBusy(""); }
  };
  const rotate = async () => {
    setBusy("rotate"); setErr("");
    try { await haproxyApi.rotateCreds(nodeId); await load(); }
    catch (e: any) { setErr(e?.message || "Не удалось ротировать учётные данные"); }
    finally { setBusy(""); }
  };
  const del = async () => {
    setBusy("del"); setErr("");
    try { await haproxyApi.deleteNode(nodeId); onDeleted(); }
    catch (e: any) { setErr(e?.message || "Не удалось удалить ноду"); setBusy(""); }
  };

  if (loading && !op) return <div className="p-6 text-center"><Loader2 size={20} className="animate-spin mx-auto text-[var(--t-low)]" /></div>;

  const m = op?.latest_heartbeat?.metrics;
  const rt = m?.haproxy_runtime;
  const ctl = op?.haproxy_control;
  const netBps = m?.network_bytes_per_second
    ? Object.values(m.network_bytes_per_second).reduce((a, b) => a + (Number(b) || 0), 0) * 8
    : undefined;

  return (
    <div className="flex flex-col gap-4">
      {err && <p className="text-xs text-[var(--err)]">{err}</p>}

      {/* KPI grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
        <Mini icon={<Cpu size={13} />} label="CPU" value={fmtPct(m?.cpu_percent)} hint={m?.cpu_count ? `${m.cpu_count} ядер` : undefined} />
        <Mini icon={<MemoryStick size={13} />} label="Память" value={fmtPct(m?.memory_percent)} hint={m?.memory_total_bytes ? fmtBytes(m.memory_total_bytes) : undefined} />
        <Mini icon={<Activity size={13} />} label="Сеть" value={fmtBps(netBps)} hint={op?.latest_heartbeat?.agent_version ? `agent ${op.latest_heartbeat.agent_version}` : undefined} />
        <Mini icon={<Clock size={13} />} label="Аптайм" value={fmtUptime(m?.uptime_seconds)} hint={m?.os ? `${m.os}/${m.arch}` : undefined} />
      </div>

      {/* HAProxy runtime + control */}
      <div className="card p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-[var(--t-hi)] flex items-center gap-1.5"><Power size={14} /> HAProxy</p>
            <p className="text-[11px] text-[var(--t-low)] mt-0.5">
              {m?.haproxy_version ? `версия ${m.haproxy_version} · ` : ""}
              состояние: {CONTROL_LABEL[ctl?.active_state || "unknown"] || ctl?.active_state || "—"}
              {ctl?.last_error ? <span className="text-[var(--err)]"> · {ctl.last_error}</span> : null}
            </p>
          </div>
          {ctl?.supported ? (
            <div className="seg mini accent">
              <button className={ctl.desired_enabled ? "on" : ""} onClick={() => toggleHaproxy(true)} disabled={busy === "haproxy"}>Вкл</button>
              <button className={!ctl.desired_enabled ? "on" : ""} onClick={() => toggleHaproxy(false)} disabled={busy === "haproxy"}>Выкл</button>
            </div>
          ) : <span className="chip neutral">управление недоступно</span>}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <Stat label="Соединения" value={String(rt?.connections_current ?? 0)} />
          <Stat label="Всего соединений" value={String(rt?.connections_total ?? 0)} />
          <Stat label="Входящий" value={fmtBytes(rt?.bytes_in)} />
          <Stat label="Исходящий" value={fmtBytes(rt?.bytes_out)} />
        </div>
      </div>

      {/* routes + traffic summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
        <Mini icon={<RouteIcon size={13} />} label="Маршруты" value={`${op?.routes_enabled ?? 0} / ${op?.routes_total ?? 0}`} hint="включено / всего" />
        <Mini icon={<Activity size={13} />} label="Rx / Tx" value={fmtBps(op?.rx_bits_per_second)} hint={`↑ ${fmtBps(op?.tx_bits_per_second)}`} />
        <Mini icon={<Activity size={13} />} label="Трафик за месяц" value={fmtBytes(op?.traffic_used_bytes)} hint={op?.traffic_month} />
        <Mini icon={<KeyRound size={13} />} label="mTLS-креды" value={op?.credential_prefix || "—"}
          hint={op?.credential_expires_at ? `до ${new Date(op.credential_expires_at).toLocaleDateString("ru-RU")}` : undefined} />
      </div>

      {/* actions */}
      <div className="flex items-center gap-2 flex-wrap">
        <button className="btn btn-soft" onClick={rotate} disabled={!!busy}>
          {busy === "rotate" ? <Loader2 size={13} className="animate-spin" /> : <KeyRound size={13} />} Ротировать креды
        </button>
        {confirmDel ? (
          <button className="btn btn-danger" onClick={del} disabled={!!busy}>
            {busy === "del" ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />} Точно удалить?
          </button>
        ) : (
          <button className="btn btn-danger" onClick={() => setConfirmDel(true)} disabled={!!busy}><Trash2 size={13} /> Удалить ноду</button>
        )}
      </div>
    </div>
  );
}

function Mini({ icon, label, value, hint }: { icon: React.ReactNode; label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-2.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-[var(--t-low)] font-semibold">{icon}{label}</div>
      <div className="text-sm font-semibold text-[var(--t-hi)] mt-1 tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-[var(--t-faint)] mt-0.5 truncate">{hint}</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div><span className="text-[var(--t-faint)]">{label}: </span><span className="text-[var(--t-mid)] tabular-nums">{value}</span></div>;
}
