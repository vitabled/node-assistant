import { useState, useEffect, useCallback } from "react";
import { Server, Plus, Loader2, Pencil, Trash2, RefreshCw, CalendarClock } from "lucide-react";
import { infraApi, type Service, type Provider, type Project } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, SelectField, Modal, fmtNum, fmtDateShort, loadDeployNodes } from "./ui";

const KINDS = [
  { v: "vps", l: "VDS/VPS" }, { v: "dedicated", l: "Выделенный сервер" },
  { v: "storage", l: "Облачное хранилище (S3)" }, { v: "domain", l: "Домен" },
  { v: "ip", l: "Сеть/IP" }, { v: "other", l: "Прочее" },
];
const kindLabel = (k: string) => KINDS.find(x => x.v === k)?.l ?? k;

export function InfraServices() {
  const [rows, setRows] = useState<Service[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Service }>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, pr] = await Promise.all([infraApi.listServices(), infraApi.listProviders(), infraApi.listProjects()]);
      setRows(s); setProviders(p); setProjects(pr);
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const pname = (u: string) => providers.find(p => p.uuid === u)?.name ?? "—";
  const del = async (s: Service) => {
    if (!confirm(`Удалить услугу «${s.name}»?`)) return;
    try { await infraApi.deleteService(s.id); toast("Услуга удалена", "success"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  };

  return (
    <Page>
      <PageHeader icon={<Server size={16} className="text-[var(--accent-hi)]" />} title="Услуги и Тарифы"
        subtitle="Оплачиваемые позиции инфраструктуры"
        actions={<>
          <button onClick={load} className="iconbtn"><RefreshCw size={13} /></button>
          <button onClick={() => setModal({})} className="btn btn-primary"><Plus size={13} /> Услуга</button>
        </>} />

      <div className="rounded-xl border border-[var(--line-soft)] overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-[var(--raised)] text-[var(--t-low)] text-[11px] uppercase tracking-widest">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Услуга</th>
              <th className="text-left font-medium px-4 py-2.5">Тип</th>
              <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
              <th className="text-left font-medium px-4 py-2.5">Тарификация</th>
              <th className="text-right font-medium px-4 py-2.5">Стоимость</th>
              <th className="text-left font-medium px-4 py-2.5">След. списание</th>
              <th className="text-right font-medium px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--line-soft)]">
            {loading ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={7} className="px-4 py-8 text-center text-[var(--t-faint)] text-xs">Услуг нет.</td></tr>
            ) : rows.map(s => (
              <tr key={s.id} className="hover:bg-[var(--row-hover)]">
                <td className="px-4 py-2.5 text-[var(--t-hi)]">{s.name}</td>
                <td className="px-4 py-2.5 text-[var(--t-mid)]">{kindLabel(s.kind)}</td>
                <td className="px-4 py-2.5 text-[var(--t-mid)]">{pname(s.provider_uuid)}</td>
                <td className="px-4 py-2.5">
                  <span className={`text-xs ${s.billing_type === "hourly" ? "text-[var(--warn)]" : "text-[var(--accent-hi)]"}`}>
                    {s.billing_type === "hourly" ? "почасовая" : "фиксированная"}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-[var(--t-hi)]">
                  {fmtNum(s.cost)}{s.billing_type === "hourly" ? "/ч" : "/мес"}
                </td>
                <td className="px-4 py-2.5 text-[var(--t-mid)] flex items-center gap-1.5"><CalendarClock size={12} className="text-[var(--t-faint)]" />{s.next_billing_at ? fmtDateShort(s.next_billing_at) : "—"}</td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => setModal({ edit: s })} className="p-1.5 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Pencil size={13} /></button>
                  <button onClick={() => del(s)} className="p-1.5 text-[var(--t-low)] hover:text-[var(--err)]"><Trash2 size={13} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && <ServiceModal edit={modal.edit} providers={providers} projects={projects}
        onClose={() => setModal(null)} onSaved={() => { setModal(null); load(); }} />}
    </Page>
  );
}

function ServiceModal({ edit, providers, projects, onClose, onSaved }: {
  edit?: Service; providers: Provider[]; projects: Project[]; onClose: () => void; onSaved: () => void;
}) {
  const [f, setF] = useState({
    name: edit?.name ?? "", kind: edit?.kind ?? "vps", node_uuid: edit?.node_uuid ?? "",
    provider_uuid: edit?.provider_uuid ?? (providers[0]?.uuid ?? ""), project_id: edit?.project_id ?? "",
    billing_type: edit?.billing_type ?? "fixed", cost: String(edit?.cost ?? "0"),
    next_billing_at: edit?.next_billing_at ? edit.next_billing_at.slice(0, 10) : "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: string) => setF(p => ({ ...p, [k]: v }));
  const nodes = loadDeployNodes();

  const submit = async () => {
    if (!f.name.trim()) { toast("Укажите название услуги", "error"); return; }
    const cost = parseFloat(f.cost);
    if (isNaN(cost) || cost < 0) { toast("Некорректная стоимость", "error"); return; }
    setSaving(true);
    const body = { ...f, cost, next_billing_at: f.next_billing_at ? new Date(f.next_billing_at).toISOString() : "" };
    try {
      if (edit) await infraApi.updateService(edit.id, body);
      else await infraApi.createService(body);
      toast(edit ? "Услуга обновлена" : "Услуга создана", "success"); onSaved();
    } catch (e) { toast((e as Error).message, "error"); setSaving(false); }
  };

  return (
    <Modal title={edit ? "Редактировать услугу" : "Новая услуга"} onClose={onClose} wide
      footer={<>
        <button onClick={onClose} className="btn btn-ghost">Отмена</button>
        <button onClick={submit} disabled={saving} className="btn btn-primary">
          {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
        </button>
      </>}>
      <Field label="Название" value={f.name} onChange={v => set("name", v)} placeholder="VDS Selectel #1" />
      <div className="grid grid-cols-2 gap-3">
        <SelectField label="Тип" value={f.kind} onChange={v => set("kind", v)} options={KINDS} />
        <SelectField label="Тарификация" value={f.billing_type} onChange={v => set("billing_type", v)}
          options={[{ v: "fixed", l: "Фиксированная (в мес)" }, { v: "hourly", l: "Почасовая" }]} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <SelectField label="Провайдер" value={f.provider_uuid} onChange={v => set("provider_uuid", v)}
          options={providers.map(p => ({ v: p.uuid, l: p.name }))} />
        <SelectField label="Проект" value={f.project_id} onChange={v => set("project_id", v)}
          options={[{ v: "", l: "— без проекта —" }, ...projects.map(p => ({ v: p.id, l: p.name }))]} />
      </div>
      <SelectField label="Нода деплоя" value={f.node_uuid} onChange={v => set("node_uuid", v)}
        options={[{ v: "", l: "— не привязана —" }, ...nodes.map(n => ({ v: n.value, l: n.label }))]} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Стоимость" value={f.cost} onChange={v => set("cost", v)} type="number" />
        <Field label="След. списание" value={f.next_billing_at} onChange={v => set("next_billing_at", v)} type="date" />
      </div>
    </Modal>
  );
}
