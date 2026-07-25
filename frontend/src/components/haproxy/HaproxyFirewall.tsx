import { useCallback, useEffect, useState } from "react";
import { ShieldHalf, RefreshCw, Loader2, Save } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi, asList } from "./api";
import { useHaproxyReady, NotConnected } from "./HaproxyConnect";
import type { NodeRecord, NodeFirewallPolicy, FirewallMode } from "./contracts";

const MODES: { v: FirewallMode; l: string; hint: string }[] = [
  { v: "off", l: "Выключен", hint: "UFW не трогается" },
  { v: "observe", l: "Наблюдение", hint: "план правил, без применения" },
  { v: "apply", l: "Применять", hint: "открывать listener-порты в UFW" },
];

export function HaproxyFirewall() {
  const { ready, loading: gate } = useHaproxyReady();
  const [nodes, setNodes] = useState<NodeRecord[]>([]);
  const [nodeId, setNodeId] = useState("");
  const [pol, setPol] = useState<NodeFirewallPolicy | null>(null);
  const [mode, setMode] = useState<FirewallMode>("off");
  const [ports, setPorts] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!ready) return;
    haproxyApi.nodes().then(v => {
      const list = asList<NodeRecord>(v, "nodes");
      setNodes(list); setNodeId(prev => prev || list[0]?.id || "");
    }).catch(() => {});
  }, [ready]);

  const load = useCallback(async () => {
    if (!ready || !nodeId) { setPol(null); return; }
    setLoading(true); setErr(""); setSaved(false);
    try {
      const p = await haproxyApi.firewall(nodeId);
      setPol(p); setMode(p.mode); setPorts((p.tcp_ports || []).join(", "));
    } catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready, nodeId]);
  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setBusy(true); setErr(""); setSaved(false);
    const tcp_ports = ports.split(/[\s,]+/).map(s => Number(s.trim())).filter(n => Number.isInteger(n) && n > 0 && n < 65536);
    try {
      const p = await haproxyApi.setFirewall(nodeId, { mode, tcp_ports });
      setPol(p); setPorts((p.tcp_ports || []).join(", ")); setSaved(true);
    } catch (e: any) { setErr(e?.message || "Не удалось сохранить"); }
    finally { setBusy(false); }
  };

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  return (
    <Page>
      <PageHeader
        icon={<ShieldHalf size={18} />}
        title="Файрвол HAProxy"
        subtitle="Политика UFW для listener-портов ноды"
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

      {!nodeId ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">Добавьте ноду в разделе «Ноды».</div>
      ) : (
        <div className="card p-5 flex flex-col gap-4 max-w-xl">
          <div className="flex flex-col gap-1.5">
            <label className="label">Режим</label>
            <div className="seg accent">
              {MODES.map(m => (
                <button key={m.v} className={mode === m.v ? "on" : ""} onClick={() => setMode(m.v)}>{m.l}</button>
              ))}
            </div>
            <p className="text-[11px] text-[var(--t-low)]">{MODES.find(m => m.v === mode)?.hint}</p>
          </div>

          <div className="flex flex-col gap-1">
            <label className="label">TCP-порты (через запятую)</label>
            <input className="input" value={ports} onChange={e => setPorts(e.target.value)} placeholder="443, 8443" spellCheck={false} />
            <p className="text-[11px] text-[var(--t-low)]">
              Служебный mTLS-порт наружу не открывается. {pol && !pol.plan_complete && <span className="text-[var(--warn)]">План ещё формируется агентом.</span>}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button className="btn btn-primary" onClick={save} disabled={busy}>
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Сохранить
            </button>
            {saved && <span className="chip ok">сохранено</span>}
          </div>
        </div>
      )}
    </Page>
  );
}
