import { useState, useEffect, useCallback } from "react";
import { Plus, Loader2, Trash2, RefreshCw, Boxes, CalendarClock } from "lucide-react";
import { infraApi, type NodesResp, type Provider } from "./api";
import { toast } from "./Toast";

export function InfraBillingNodes() {
  const [data, setData] = useState<NodesResp | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [n, p] = await Promise.all([infraApi.listNodes(), infraApi.listProviders()]);
      setData(n); setProviders(p);
    } catch (e) { toast(String((e as Error).message), "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const del = async (uuid: string, name: string) => {
    if (!confirm(`Отвязать узел «${name}» от биллинга?`)) return;
    try { await infraApi.deleteNode(uuid); toast("Узел отвязан", "success"); load(); }
    catch (e) { toast(String((e as Error).message), "error"); }
  };

  const stats = data?.stats ?? {};
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-base font-semibold text-white flex items-center gap-2">
              <Boxes size={16} className="text-blue-400" /> Узлы биллинга
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">Привязка серверов деплоя к провайдерам и стоимости аренды</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
            <button onClick={() => setAdding(true)} disabled={!data?.availableBillingNodes.length}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-40"
              title={data?.availableBillingNodes.length ? "" : "Нет свободных нод для привязки"}>
              <Plus size={13} /> Привязать узел
            </button>
          </div>
        </div>

        {/* Stat cards */}
        <div className="grid grid-cols-3 gap-4 mb-5">
          <StatCard label="Ближайшие списания" value={String(stats.upcomingNodesCount ?? 0)} />
          <StatCard label="Оплаты в этом месяце" value={fmtMoney(stats.currentMonthPayments)} />
          <StatCard label="Всего потрачено" value={fmtMoney(stats.totalSpent)} />
        </div>

        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">Узел</th>
                <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                <th className="text-right font-medium px-4 py-2.5">Стоимость/мес</th>
                <th className="text-left font-medium px-4 py-2.5">Следующее списание</th>
                <th className="text-right font-medium px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600"><Loader2 size={16} className="animate-spin inline" /></td></tr>
              ) : !data?.billingNodes.length ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600 text-xs">Нет привязанных узлов.</td></tr>
              ) : data.billingNodes.map(n => (
                <tr key={n.uuid} className="hover:bg-gray-900/40">
                  <td className="px-4 py-2.5 text-gray-200">{n.name}</td>
                  <td className="px-4 py-2.5 text-gray-400">{n.provider?.name ?? "—"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-300">{n.monthlyCost ? n.monthlyCost.toLocaleString("ru-RU") : "—"}</td>
                  <td className="px-4 py-2.5 text-gray-400 flex items-center gap-1.5">
                    <CalendarClock size={12} className="text-gray-600" />{fmtDate(n.nextBillingAt)}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button onClick={() => del(n.uuid, n.name)} className="p-1.5 text-gray-500 hover:text-red-400"><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {adding && data && (
        <AttachModal providers={providers} available={data.availableBillingNodes}
          onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />
      )}
    </div>
  );
}

function AttachModal({ providers, available, onClose, onSaved }: {
  providers: Provider[]; available: { uuid: string; name: string }[]; onClose: () => void; onSaved: () => void;
}) {
  const [nodeUuid, setNodeUuid] = useState(available[0]?.uuid ?? "");
  const [providerUuid, setProviderUuid] = useState(providers[0]?.uuid ?? "");
  const [cost, setCost] = useState("0");
  const [when, setWhen] = useState(() => new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10));
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!nodeUuid || !providerUuid) { toast("Выберите узел и провайдера", "error"); return; }
    const c = parseFloat(cost);
    setSaving(true);
    const node = available.find(a => a.uuid === nodeUuid);
    try {
      await infraApi.createNode({
        provider_uuid: providerUuid, node_uuid: nodeUuid, name: node?.name ?? "node",
        next_billing_at: new Date(when).toISOString(), monthly_cost: isNaN(c) ? 0 : c,
      });
      toast("Узел привязан к биллингу", "success"); onSaved();
    } catch (e) { toast(String((e as Error).message), "error"); setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-gray-950 border border-gray-700/60 rounded-xl w-full max-w-md p-5">
        <h2 className="text-sm font-semibold text-white mb-4">Привязать узел к биллингу</h2>
        <div className="flex flex-col gap-3">
          <Select label="Узел" value={nodeUuid} onChange={setNodeUuid} options={available.map(a => ({ v: a.uuid, l: a.name }))} />
          <Select label="Провайдер" value={providerUuid} onChange={setProviderUuid} options={providers.map(p => ({ v: p.uuid, l: p.name }))} />
          <div className="grid grid-cols-2 gap-3">
            <FieldInput label="Стоимость/мес" value={cost} onChange={setCost} type="number" />
            <FieldInput label="Следующее списание" value={when} onChange={setWhen} type="date" />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
          <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
            {saving && <Loader2 size={13} className="animate-spin" />} Привязать
          </button>
        </div>
      </div>
    </div>
  );
}

const inputCls = "w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:border-blue-500/70 focus:ring-blue-500/20";
function StatCard({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4">
    <p className="text-[10px] uppercase tracking-widest text-gray-500">{label}</p>
    <p className="text-xl font-semibold text-gray-100 tabular-nums mt-1">{value}</p>
  </div>;
}
function FieldInput({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (v: string) => void; type?: string }) {
  return <div className="flex flex-col gap-1">
    <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">{label}</label>
    <input type={type} value={value} onChange={e => onChange(e.target.value)} className={inputCls} />
  </div>;
}
function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return <div className="flex flex-col gap-1">
    <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">{label}</label>
    <select value={value} onChange={e => onChange(e.target.value)} className={inputCls}>
      {options.length === 0 && <option value="">— нет —</option>}
      {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  </div>;
}
function fmtMoney(v?: number): string { return v != null ? v.toLocaleString("ru-RU") : "—"; }
function fmtDate(iso: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}
