import { useState, useEffect, useCallback, useMemo } from "react";
import { ReceiptText, Plus, Loader2, Trash2, RefreshCw, Lock } from "lucide-react";
import { infraApi, type Payment, type Provider, type Project } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, SelectField, Modal, fmtNum, fmtDate } from "./ui";

const TYPES = [{ v: "charge", l: "Списание" }, { v: "topup", l: "Пополнение" }, { v: "adjustment", l: "Корректировка" }];
const STATUSES = [{ v: "success", l: "Успешно" }, { v: "pending", l: "В обработке" }, { v: "error", l: "Ошибка" }];
const typeCls: Record<string, string> = { charge: "text-amber-300", topup: "text-green-400", adjustment: "text-blue-300" };
const statusCls: Record<string, string> = { success: "text-green-400", pending: "text-yellow-400", error: "text-red-400" };

export function InfraPayments() {
  const [rows, setRows] = useState<Payment[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [locked, setLocked] = useState(false);
  const [filterType, setFilterType] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setLocked(false);
    try {
      const [pays, prov, proj] = await Promise.all([infraApi.listPayments(), infraApi.listProviders(), infraApi.listProjects()]);
      setRows(pays); setProviders(prov); setProjects(proj);
    } catch (e) {
      if ((e as { status?: number }).status === 401) setLocked(true);
      else toast((e as Error).message, "error");
    }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const pname = (u: string) => providers.find(p => p.uuid === u)?.name ?? "—";
  const prname = (id: string) => projects.find(p => p.id === id)?.name ?? "";
  const filtered = useMemo(() => filterType ? rows.filter(r => r.type === filterType) : rows, [rows, filterType]);

  const del = async (id: string) => {
    if (!confirm("Удалить запись платежа?")) return;
    try { await infraApi.deletePayment(id); toast("Запись удалена", "success"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  };

  if (locked) return (
    <Page>
      <PageHeader icon={<ReceiptText size={16} className="text-blue-400" />} title="Платежи" />
      <div className="rounded-xl border border-amber-900/40 bg-amber-950/30 p-8 text-center text-amber-300 text-sm flex flex-col items-center gap-2">
        <Lock size={20} /> Раздел защищён. Войдите в финансовый контур во вкладке «Sign-in».
      </div>
    </Page>
  );

  return (
    <Page>
      <PageHeader icon={<ReceiptText size={16} className="text-blue-400" />} title="Платежи и Инвойсы"
        subtitle="Журнал транзакций, пополнений и списаний"
        actions={<>
          <SelectFilter value={filterType} onChange={setFilterType} />
          <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
          <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white"><Plus size={13} /> Платёж</button>
        </>} />

      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Дата</th>
              <th className="text-left font-medium px-4 py-2.5">Провайдер / Проект</th>
              <th className="text-left font-medium px-4 py-2.5">Тип</th>
              <th className="text-right font-medium px-4 py-2.5">Сумма</th>
              <th className="text-left font-medium px-4 py-2.5">Статус</th>
              <th className="text-right font-medium px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-600"><Loader2 size={16} className="animate-spin inline" /></td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-600 text-xs">Платежей нет.</td></tr>
            ) : filtered.map(p => (
              <tr key={p.id} className="hover:bg-gray-900/40">
                <td className="px-4 py-2.5 text-gray-400 tabular-nums">{fmtDate(p.ts)}</td>
                <td className="px-4 py-2.5 text-gray-300">{pname(p.provider_uuid)}{prname(p.project_id) && <span className="text-gray-600"> · {prname(p.project_id)}</span>}</td>
                <td className={`px-4 py-2.5 text-xs ${typeCls[p.type] ?? "text-gray-400"}`}>{TYPES.find(t => t.v === p.type)?.l ?? p.type}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-gray-200">{fmtNum(Math.abs(p.amount), p.currency)}</td>
                <td className={`px-4 py-2.5 text-xs ${statusCls[p.status] ?? "text-gray-400"}`}>{STATUSES.find(s => s.v === p.status)?.l ?? p.status}</td>
                <td className="px-4 py-2.5 text-right"><button onClick={() => del(p.id)} className="p-1.5 text-gray-500 hover:text-red-400"><Trash2 size={13} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {adding && <PaymentModal providers={providers} projects={projects} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />}
    </Page>
  );
}

function SelectFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}
      className="bg-gray-900/80 border border-gray-700/80 rounded-md px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none">
      <option value="">Все типы</option>
      {TYPES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
    </select>
  );
}

function PaymentModal({ providers, projects, onClose, onSaved }: {
  providers: Provider[]; projects: Project[]; onClose: () => void; onSaved: () => void;
}) {
  const [f, setF] = useState({
    provider_uuid: providers[0]?.uuid ?? "", project_id: "", type: "charge",
    amount: "", currency: "RUB", status: "success", note: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: string) => setF(p => ({ ...p, [k]: v }));

  const submit = async () => {
    const a = parseFloat(f.amount);
    if (isNaN(a) || a === 0) { toast("Укажите ненулевую сумму", "error"); return; }
    setSaving(true);
    try { await infraApi.createPayment({ ...f, amount: a }); toast("Платёж добавлен", "success"); onSaved(); }
    catch (e) { toast((e as Error).message, "error"); setSaving(false); }
  };

  return (
    <Modal title="Новый платёж" onClose={onClose}
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
        <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
          {saving && <Loader2 size={13} className="animate-spin" />} Добавить
        </button>
      </>}>
      <div className="grid grid-cols-2 gap-3">
        <SelectField label="Провайдер" value={f.provider_uuid} onChange={v => set("provider_uuid", v)} options={providers.map(p => ({ v: p.uuid, l: p.name }))} />
        <SelectField label="Проект" value={f.project_id} onChange={v => set("project_id", v)} options={[{ v: "", l: "— нет —" }, ...projects.map(p => ({ v: p.id, l: p.name }))]} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <SelectField label="Тип" value={f.type} onChange={v => set("type", v)} options={TYPES.map(t => ({ v: t.v, l: t.l }))} />
        <SelectField label="Статус" value={f.status} onChange={v => set("status", v)} options={STATUSES.map(s => ({ v: s.v, l: s.l }))} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Сумма" value={f.amount} onChange={v => set("amount", v)} type="number" />
        <Field label="Валюта" value={f.currency} onChange={v => set("currency", v)} />
      </div>
      <Field label="Примечание" value={f.note} onChange={v => set("note", v)} placeholder="Оплата счёта №…" />
    </Modal>
  );
}
