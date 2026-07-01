import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Loader2, Trash2, RefreshCw, ReceiptText, Download, Upload, Plus } from "lucide-react";
import { infraApi, type HistoryRecord, type Provider } from "./api";
import { toast } from "./Toast";

export function InfraHistory() {
  const [rows, setRows] = useState<HistoryRecord[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [adding, setAdding] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [h, p] = await Promise.all([infraApi.listHistory(), infraApi.listProviders()]);
      setRows(h); setProviders(p);
    } catch (e) { toast(String((e as Error).message), "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const pname = (uuid: string, rec?: HistoryRecord) => rec?.provider?.name ?? providers.find(p => p.uuid === uuid)?.name ?? uuid.slice(0, 8);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(r => pname(r.providerUuid, r).toLowerCase().includes(q));
  }, [rows, filter, providers]);

  const del = async (uuid: string) => {
    if (!confirm("Удалить запись из истории?")) return;
    try { await infraApi.deleteHistory(uuid); toast("Запись удалена", "success"); load(); }
    catch (e) { toast(String((e as Error).message), "error"); }
  };

  const exportCsv = () => {
    const head = "billedAt,provider,providerUuid,amount\n";
    const body = filtered.map(r => `${r.billedAt},"${pname(r.providerUuid, r)}",${r.providerUuid},${r.amount}`).join("\n");
    const blob = new Blob([head + body], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "infra-billing-history.csv"; a.click();
    URL.revokeObjectURL(a.href);
  };

  const importJson = async (file: File) => {
    try {
      const arr = JSON.parse(await file.text());
      if (!Array.isArray(arr)) throw new Error("Ожидался JSON-массив записей");
      let ok = 0;
      for (const r of arr) {
        if (!r.providerUuid || r.amount == null || !r.billedAt) continue;
        await infraApi.createHistory({ provider_uuid: r.providerUuid, amount: Number(r.amount), billed_at: r.billedAt });
        ok++;
      }
      toast(`Импортировано записей: ${ok}`, "success"); load();
    } catch (e) { toast(`Импорт не удался: ${(e as Error).message}`, "error"); }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-base font-semibold text-white flex items-center gap-2">
              <ReceiptText size={16} className="text-blue-400" /> История и Инвойсы
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">Транзакции списаний и пополнений по провайдерам</p>
          </div>
          <div className="flex items-center gap-2">
            <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Фильтр по провайдеру…"
              className="w-44 bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-1.5 text-xs text-gray-100 focus:outline-none focus:ring-1 focus:border-blue-500/70 focus:ring-blue-500/20" />
            <button onClick={exportCsv} title="Экспорт CSV" className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><Download size={13} /></button>
            <button onClick={() => fileRef.current?.click()} title="Импорт JSON" className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><Upload size={13} /></button>
            <input ref={fileRef} type="file" accept="application/json" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) importJson(f); e.target.value = ""; }} />
            <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
            <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white">
              <Plus size={13} /> Запись
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">Дата</th>
                <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                <th className="text-left font-medium px-4 py-2.5">Тип</th>
                <th className="text-right font-medium px-4 py-2.5">Сумма</th>
                <th className="text-right font-medium px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600"><Loader2 size={16} className="animate-spin inline" /></td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600 text-xs">Записей нет.</td></tr>
              ) : filtered.map(r => {
                const topup = r.amount < 0;   // Remnawave has no type field — derive from sign.
                return (
                  <tr key={r.uuid} className="hover:bg-gray-900/40">
                    <td className="px-4 py-2.5 text-gray-400 tabular-nums">{fmtDate(r.billedAt)}</td>
                    <td className="px-4 py-2.5 text-gray-200">{pname(r.providerUuid, r)}</td>
                    <td className="px-4 py-2.5">
                      <span className={`text-xs ${topup ? "text-green-400" : "text-amber-300"}`}>{topup ? "Пополнение" : "Списание"}</span>
                    </td>
                    <td className={`px-4 py-2.5 text-right tabular-nums ${topup ? "text-green-400" : "text-gray-200"}`}>
                      {Math.abs(r.amount).toLocaleString("ru-RU")}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button onClick={() => del(r.uuid)} className="p-1.5 text-gray-500 hover:text-red-400"><Trash2 size={13} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-gray-600 mt-2">
          Тип операции выводится по знаку суммы (отрицательная = пополнение). Поле «статус» Remnawave не предоставляет.
        </p>
      </div>

      {adding && <AddRecordModal providers={providers} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />}
    </div>
  );
}

function AddRecordModal({ providers, onClose, onSaved }: { providers: Provider[]; onClose: () => void; onSaved: () => void }) {
  const [providerUuid, setProviderUuid] = useState(providers[0]?.uuid ?? "");
  const [amount, setAmount] = useState("");
  const [when, setWhen] = useState(() => new Date().toISOString().slice(0, 10));
  const [saving, setSaving] = useState(false);
  const cls = "w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:border-blue-500/70 focus:ring-blue-500/20";

  const submit = async () => {
    const a = parseFloat(amount);
    if (!providerUuid) { toast("Выберите провайдера", "error"); return; }
    if (isNaN(a) || a === 0) { toast("Укажите ненулевую сумму", "error"); return; }
    setSaving(true);
    try {
      await infraApi.createHistory({ provider_uuid: providerUuid, amount: a, billed_at: new Date(when).toISOString() });
      toast("Запись добавлена", "success"); onSaved();
    } catch (e) { toast(String((e as Error).message), "error"); setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-gray-950 border border-gray-700/60 rounded-xl w-full max-w-sm p-5">
        <h2 className="text-sm font-semibold text-white mb-4">Новая запись</h2>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">Провайдер</label>
            <select value={providerUuid} onChange={e => setProviderUuid(e.target.value)} className={cls}>
              {providers.map(p => <option key={p.uuid} value={p.uuid}>{p.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">Сумма (отриц. = пополнение)</label>
            <input type="number" value={amount} onChange={e => setAmount(e.target.value)} placeholder="500" className={cls} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">Дата</label>
            <input type="date" value={when} onChange={e => setWhen(e.target.value)} className={cls} />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
          <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
            {saving && <Loader2 size={13} className="animate-spin" />} Добавить
          </button>
        </div>
      </div>
    </div>
  );
}

function fmtDate(iso: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
