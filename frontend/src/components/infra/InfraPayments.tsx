import { useState, useEffect, useCallback, useMemo } from "react";
import { ReceiptText, Plus, Loader2, Trash2, RefreshCw, Lock } from "lucide-react";
import { infraApi, type Payment, type Provider, type Project } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, SelectField, Modal, fmtNum, fmtDate } from "./ui";

const TYPES = [{ v: "charge", l: "Списание" }, { v: "topup", l: "Пополнение" }, { v: "adjustment", l: "Корректировка" }];
const STATUSES = [{ v: "success", l: "Успешно" }, { v: "pending", l: "В обработке" }, { v: "error", l: "Ошибка" }];
const typeCls: Record<string, string> = { charge: "text-[var(--warn)]", topup: "text-[var(--ok)]", adjustment: "text-[var(--accent-hi)]" };
const statusCls: Record<string, string> = { success: "text-[var(--ok)]", pending: "text-[var(--warn)]", error: "text-[var(--err)]" };

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
      <PageHeader icon={<ReceiptText size={16} className="text-[var(--accent-hi)]" />} title="Платежи" />
      <div className="rounded-xl border border-[var(--warn-line)] bg-[var(--warn-dim)] p-8 text-center text-[var(--warn)] text-sm flex flex-col items-center gap-2">
        <Lock size={20} /> Раздел защищён. Войдите в финансовый контур во вкладке «Sign-in».
      </div>
    </Page>
  );

  return (
    <Page>
      <PageHeader icon={<ReceiptText size={16} className="text-[var(--accent-hi)]" />} title="Платежи и Инвойсы"
        subtitle="Журнал транзакций, пополнений и списаний"
        actions={<>
          <SelectFilter value={filterType} onChange={setFilterType} />
          <button onClick={load} className="iconbtn"><RefreshCw size={13} /></button>
          <button onClick={() => setAdding(true)} className="btn btn-primary"><Plus size={13} /> Платёж</button>
        </>} />

      <div className="rounded-xl border border-[var(--line-soft)] overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-[var(--raised)] text-[var(--t-low)] text-[11px] uppercase tracking-widest">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Дата</th>
              <th className="text-left font-medium px-4 py-2.5">Провайдер / Проект</th>
              <th className="text-left font-medium px-4 py-2.5">Тип</th>
              <th className="text-right font-medium px-4 py-2.5">Сумма</th>
              <th className="text-left font-medium px-4 py-2.5">Статус</th>
              <th className="text-right font-medium px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--line-soft)]">
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-[var(--t-faint)] text-xs">Платежей нет.</td></tr>
            ) : filtered.map(p => (
              <tr key={p.id} className="hover:bg-[var(--row-hover)]">
                <td className="px-4 py-2.5 text-[var(--t-mid)] tabular-nums">{fmtDate(p.ts)}</td>
                <td className="px-4 py-2.5 text-[var(--t-mid)]">{pname(p.provider_uuid)}{prname(p.project_id) && <span className="text-[var(--t-faint)]"> · {prname(p.project_id)}</span>}</td>
                <td className={`px-4 py-2.5 text-xs ${typeCls[p.type] ?? "text-[var(--t-mid)]"}`}>{TYPES.find(t => t.v === p.type)?.l ?? p.type}</td>
                <td className="px-4 py-2.5 text-right tabular-nums text-[var(--t-hi)]">{fmtNum(Math.abs(p.amount), p.currency)}</td>
                <td className={`px-4 py-2.5 text-xs ${statusCls[p.status] ?? "text-[var(--t-mid)]"}`}>{STATUSES.find(s => s.v === p.status)?.l ?? p.status}</td>
                <td className="px-4 py-2.5 text-right"><button onClick={() => del(p.id)} className="p-1.5 text-[var(--t-low)] hover:text-[var(--err)]"><Trash2 size={13} /></button></td>
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
      className="bg-[var(--bg2)] border border-[var(--line)] rounded-md px-2.5 py-1.5 text-xs text-[var(--t-hi)] focus:outline-none">
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
        <button onClick={onClose} className="btn btn-ghost">Отмена</button>
        <button onClick={submit} disabled={saving} className="btn btn-primary">
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
