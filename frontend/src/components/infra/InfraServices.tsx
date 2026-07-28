import { useState, useEffect, useCallback } from "react";
import { Server, Plus, Loader2, Pencil, Trash2, RefreshCw, CalendarClock, ShoppingCart, AlertTriangle } from "lucide-react";
import { infraApi, type Service, type Provider, type Project, type OrderOptions, type OrderPlan } from "./api";
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
  const [order, setOrder] = useState(false);
  // Adapter kinds that can actually place an order — only those providers may be
  // offered in the purchase modal.
  const [orderKinds, setOrderKinds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, pr] = await Promise.all([infraApi.listServices(), infraApi.listProviders(), infraApi.listProjects()]);
      setRows(s); setProviders(p); setProjects(pr);
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // Separate from load(): the adapter list is static, and a failure here must
    // not blank the services table.
    infraApi.listAdapters()
      .then(a => setOrderKinds(new Set(a.filter(x => x.caps.includes("order")).map(x => x.kind))))
      .catch(() => { /* нет адаптеров — кнопка «Купить» просто не появится */ });
  }, []);
  const canOrder = providers.some(p => orderKinds.has(p.adapterKind));

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
          {canOrder && <button onClick={() => setOrder(true)} className="btn btn-ghost"><ShoppingCart size={13} /> Купить</button>}
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
      {order && <OrderModal providers={providers.filter(p => orderKinds.has(p.adapterKind))}
        onClose={() => setOrder(false)} onDone={() => { setOrder(false); load(); }} />}
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

// ── Покупка сервера через API провайдера ──────────────────────
// Каждое подтверждение тратит деньги и создаёт реальный сервер, поэтому: цена
// крупно, отдельная галочка про списание и НИКАКИХ автоповторов при ошибке —
// запрос мог дойти до провайдера, и второй заказ купил бы второй сервер.

// Формы регионов и образов у вендоров разные — читаем по нескольким написаниям.
const pickStr = (o: Record<string, unknown>, keys: string[]) => {
  for (const k of keys) {
    const v = o?.[k];
    if (typeof v === "string" && v) return v;
    if (typeof v === "number") return String(v);
  }
  return "";
};
const optValue = (o: Record<string, unknown>) => pickStr(o, ["id", "slug", "value", "name"]);
const optLabel = (o: Record<string, unknown>) => pickStr(o, ["name", "label", "title", "id", "slug"]) || optValue(o);
const specsText = (s: unknown): string => {
  if (!s) return "";
  if (typeof s === "string") return s;
  if (typeof s === "object") return Object.entries(s as Record<string, unknown>).map(([k, v]) => `${k}: ${v}`).join(" · ");
  return String(s);
};
const CUSTOM_LABEL: Record<string, string> = { cpu: "vCPU", ram_gb: "RAM, ГБ", disk_gb: "Диск, ГБ" };

function OrderModal({ providers, onClose, onDone }: {
  providers: Provider[]; onClose: () => void; onDone: () => void;
}) {
  const [uuid, setUuid] = useState(providers[0]?.uuid ?? "");
  const [opts, setOpts] = useState<OrderOptions | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [planId, setPlanId] = useState("");
  const [region, setRegion] = useState("");
  const [image, setImage] = useState("");
  const [name, setName] = useState("");
  const [cust, setCust] = useState<Record<string, number>>({});
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!uuid) return;
    let alive = true;
    setLoading(true); setErr(""); setOpts(null); setPlanId(""); setAgree(false);
    infraApi.orderOptions(uuid)
      .then(o => {
        if (!alive) return;
        setOpts(o);
        setPlanId(o.plans[0]?.id ?? "");
        setRegion(o.regions[0] ? optValue(o.regions[0]) : "");
        setImage(o.images[0] ? optValue(o.images[0]) : "");
        setCust(Object.fromEntries(Object.entries(o.custom ?? {})
          .filter(([, r]) => !!r).map(([k, r]) => [k, r!.min])));
      })
      .catch(e => { if (alive) setErr((e as Error).message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [uuid]);

  const plan: OrderPlan | undefined = opts?.plans.find(p => p.id === planId);
  // Цену показываем и отправляем только ту, что пришла от провайдера: сервер
  // перечитает её заново и откажет при расхождении.
  const price = plan?.price ?? null;
  const currency = plan?.currency ?? "";
  const priceText = price === null ? "" : fmtNum(price, currency);
  const ready = !!uuid && !!name.trim() && price !== null && agree && !busy;

  const buy = async () => {
    setBusy(true);
    try {
      const res = await infraApi.createOrder(uuid, {
        plan_id: planId, region, image, name: name.trim(), period: plan?.period ?? "",
        cpu: cust.cpu ?? null, ram_gb: cust.ram_gb ?? null, disk_gb: cust.disk_gb ?? null,
        confirm: true, expected_price: price, expected_currency: currency,
      });
      toast(`Сервер «${res.name}» заказан, услуга добавлена в биллинг`, "success");
      onDone();
    } catch (e) {
      // Ошибку показываем как есть и снимаем галочку — повтор должен быть
      // осознанным действием пользователя, а не нашей ретрай-логикой.
      toast((e as Error).message, "error");
      setAgree(false); setBusy(false);
    }
  };

  return (
    <Modal title="Покупка сервера" onClose={onClose} wide
      footer={<>
        <button onClick={onClose} className="btn btn-ghost">Отмена</button>
        <button onClick={buy} disabled={!ready} className="btn btn-primary">
          {busy && <Loader2 size={13} className="animate-spin" />}
          {priceText ? `Купить за ${priceText}` : "Купить"}
        </button>
      </>}>
      <SelectField label="Провайдер" value={uuid} onChange={setUuid}
        options={providers.map(p => ({ v: p.uuid, l: p.name }))} />

      {loading && <div className="text-xs text-[var(--t-faint)] flex items-center gap-2">
        <Loader2 size={13} className="animate-spin" /> Загружаем варианты заказа…
      </div>}
      {err && <div className="text-xs text-[var(--err)]">{err}</div>}

      {opts && (opts.plans.length > 0) && (
        <div className="flex flex-col gap-1.5">
          <span className="label">Тариф</span>
          <div className="flex flex-col gap-1.5 max-h-56 overflow-y-auto">
            {opts.plans.map(p => (
              <button key={p.id} type="button" onClick={() => { setPlanId(p.id); setAgree(false); }}
                className={`text-left rounded-lg border px-3 py-2 ${p.id === planId
                  ? "border-[var(--accent-line)] bg-[var(--accent-dim)]"
                  : "border-[var(--line-soft)] hover:border-[var(--line)]"}`}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm text-[var(--t-hi)]">{p.name || p.id}</span>
                  <span className="text-xs tabular-nums text-[var(--t-mid)] shrink-0">
                    {p.price === null ? "цена не указана"
                      : `${fmtNum(p.price, p.currency)}${p.period ? " / " + p.period : ""}`}
                  </span>
                </div>
                {(specsText(p.specs) || p.region) && (
                  <div className="text-[11px] text-[var(--t-low)] mt-0.5">
                    {[specsText(p.specs), p.region].filter(Boolean).join(" · ")}
                  </div>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {opts?.custom && (
        <div className="flex flex-col gap-1.5">
          <span className="label">Конфигурация</span>
          {(Object.entries(opts.custom) as [string, { min: number; max: number; step: number } | undefined][])
            .filter(([, r]) => !!r).map(([k, r]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="text-[11px] text-[var(--t-low)] w-[90px] shrink-0">{CUSTOM_LABEL[k] ?? k}</span>
                <input type="range" min={r!.min} max={r!.max} step={r!.step || 1}
                  value={cust[k] ?? r!.min} className="flex-1 min-w-0"
                  style={{ accentColor: "var(--accent)" }}
                  onChange={e => setCust(p => ({ ...p, [k]: Number(e.target.value) }))} />
                <span className="text-[11px] tabular-nums text-[var(--t-hi)] w-12 text-right">{cust[k] ?? r!.min}</span>
              </div>
            ))}
        </div>
      )}

      {opts && (opts.regions.length > 0 || opts.images.length > 0) && (
        <div className="grid grid-cols-2 gap-3">
          {opts.regions.length > 0 && <SelectField label="Регион" value={region} onChange={setRegion}
            options={opts.regions.map(r => ({ v: optValue(r), l: optLabel(r) }))} />}
          {opts.images.length > 0 && <SelectField label="Образ" value={image} onChange={setImage}
            options={opts.images.map(i => ({ v: optValue(i), l: optLabel(i) }))} />}
        </div>
      )}

      {opts && <Field label="Имя сервера" value={name} onChange={setName} placeholder="node-ams-1" />}

      {opts && (
        <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--raised)] px-3 py-3">
          {price === null ? (
            <div className="flex items-start gap-2 text-xs text-[var(--warn)]">
              <AlertTriangle size={14} className="shrink-0 mt-px" />
              Провайдер не сообщил цену — покупка через панель недоступна, оформите заказ у провайдера.
            </div>
          ) : (
            <>
              <div className="text-[11px] text-[var(--t-low)]">К оплате</div>
              <div className="text-2xl font-semibold text-[var(--t-hi)] tabular-nums">
                {priceText}
                {plan?.period && <span className="text-sm font-normal text-[var(--t-low)]"> / {plan.period}</span>}
              </div>
            </>
          )}
        </div>
      )}

      {opts && price !== null && (
        <label className="flex items-start gap-2 text-xs text-[var(--t-mid)] cursor-pointer">
          <input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)}
            className="mt-0.5" style={{ accentColor: "var(--accent)" }} />
          Понимаю, что будет списание и создан сервер
        </label>
      )}
    </Modal>
  );
}
