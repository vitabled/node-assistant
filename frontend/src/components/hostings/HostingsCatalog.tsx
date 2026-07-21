import { useState, useEffect, useCallback } from "react";
import {
  Server, Plus, Loader2, Pencil, Trash2, RefreshCw, ExternalLink,
  MapPin, Tag, X, Globe, Wand2,
} from "lucide-react";
import {
  hostingsApi, type Hosting, type HostingBody, type Tariff, type HostingLocation,
  CURRENCIES, PERIODS, periodLabel, minTariff,
} from "./api";
import { resolveCoords } from "./geo";
import { CountrySelect } from "../CountrySelect";
import { Page, PageHeader, Field, Modal, fmtNum } from "../infra/ui";
import { toast } from "../infra/Toast";

// Small flag chip (flag-icons SVG set), Globe fallback for XX/empty.
function Flag({ code, size = 16 }: { code: string; size?: number }) {
  const cc = (code || "").toLowerCase();
  if (!cc || cc === "xx") return <Globe size={size - 3} style={{ color: "var(--t-low)" }} />;
  return <span className={`fi fi-${cc}`} style={{
    width: size, height: Math.round(size * 0.72), borderRadius: 2, flex: "none",
    backgroundSize: "cover", boxShadow: "0 0 0 1px rgba(0,0,0,.12)",
  }} />;
}

export function HostingsCatalog() {
  const [rows, setRows] = useState<Hosting[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Hosting }>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await hostingsApi.list()); }
    catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const del = async (h: Hosting) => {
    if (!confirm(`Удалить хостинг «${h.name}»?`)) return;
    try { await hostingsApi.remove(h.id); toast("Хостинг удалён", "success"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  };

  return (
    <Page>
      <PageHeader icon={<Server size={16} className="text-[var(--accent)]" />} title="Хостинги"
        subtitle="Каталог провайдеров: тарифы, характеристики, локации"
        actions={<>
          <button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>
          <button onClick={() => setModal({})} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]"><Plus size={13} /> Хостинг</button>
        </>} />

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : rows.length === 0 ? (
        <div className="card p-8 text-center text-[var(--t-faint)] text-sm">Хостингов пока нет. Добавьте первый — его локации появятся на «Карте».</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {rows.map(h => {
            const mt = minTariff(h);
            return (
              <div key={h.id} className="card p-4 flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="text-sm font-medium text-[var(--t-hi)] truncate block">{h.name}</span>
                    {h.website && (
                      <a href={h.website} target="_blank" rel="noopener noreferrer"
                        className="text-[11px] text-[var(--t-low)] hover:text-[var(--accent-hi)] flex items-center gap-1 truncate">
                        <ExternalLink size={10} /> {h.website.replace(/^https?:\/\//, "")}
                      </a>
                    )}
                  </div>
                  <div className="flex shrink-0">
                    <button onClick={() => setModal({ edit: h })} className="p-1 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Pencil size={12} /></button>
                    <button onClick={() => del(h)} className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><Trash2 size={12} /></button>
                  </div>
                </div>

                {h.features && <p className="text-xs text-[var(--t-low)] line-clamp-2">{h.features}</p>}

                {h.locations.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {h.locations.slice(0, 6).map((l, i) => (
                      <span key={i} className="flex items-center gap-1 text-[11px] text-[var(--t-mid)] bg-[var(--bg3)] rounded px-1.5 py-0.5" title={l.city}>
                        <Flag code={l.country_code} size={13} /> {l.city || l.country_code}
                      </span>
                    ))}
                    {h.locations.length > 6 && <span className="text-[11px] text-[var(--t-faint)]">+{h.locations.length - 6}</span>}
                  </div>
                )}

                <div className="flex items-center justify-between mt-auto pt-2 text-xs border-t border-[var(--line-soft)]">
                  <span className="text-[var(--t-low)] flex items-center gap-1"><Tag size={12} /> {h.tariffs.length} тарифов</span>
                  {mt
                    ? <span className="text-[var(--t-hi)] tabular-nums">от {fmtNum(mt.price, mt.currency)}<span className="text-[var(--t-faint)]">{periodLabel(mt.period)}</span></span>
                    : <span className="text-[var(--t-faint)]">—</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {modal && <HostingModal edit={modal.edit} onClose={() => setModal(null)} onSaved={() => { setModal(null); load(); }} />}
    </Page>
  );
}

const emptyTariff = (): Tariff => ({ name: "", specs: "", price: 0, currency: "USD", period: "mo" });
const emptyLoc = (): HostingLocation => ({ city: "", country_code: "", lat: 0, lng: 0, note: "" });

function HostingModal({ edit, onClose, onSaved }: { edit?: Hosting; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(edit?.name ?? "");
  const [website, setWebsite] = useState(edit?.website ?? "");
  const [features, setFeatures] = useState(edit?.features ?? "");
  const [notes, setNotes] = useState(edit?.notes ?? "");
  const [tariffs, setTariffs] = useState<Tariff[]>(edit?.tariffs?.length ? edit.tariffs : [emptyTariff()]);
  const [locations, setLocations] = useState<HostingLocation[]>(edit?.locations ?? []);
  const [saving, setSaving] = useState(false);

  const setTariff = (i: number, patch: Partial<Tariff>) =>
    setTariffs(ts => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const setLoc = (i: number, patch: Partial<HostingLocation>) =>
    setLocations(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  // Fill lat/lng from the city+country gazetteer.
  const autoCoords = (i: number) => {
    const l = locations[i];
    const c = resolveCoords(l.country_code, l.city);
    if (!c) { toast("Координаты не найдены — введите вручную", "error"); return; }
    setLoc(i, { lng: c[0], lat: c[1] });
  };

  const submit = async () => {
    if (!name.trim()) { toast("Укажите название хостинга", "error"); return; }
    // Drop fully-empty tariff/location rows.
    const cleanTariffs = tariffs.filter(t => t.name.trim() || t.specs.trim() || t.price > 0);
    const cleanLocs = locations.filter(l => l.country_code || l.city.trim());
    const body: HostingBody = {
      name: name.trim(), website: website.trim(), features: features.trim(), notes: notes.trim(),
      tariffs: cleanTariffs, locations: cleanLocs, provider_ref: edit?.provider_ref ?? null,
    };
    setSaving(true);
    try {
      if (edit) await hostingsApi.update(edit.id, body);
      else await hostingsApi.create(body);
      toast(edit ? "Хостинг обновлён" : "Хостинг добавлен", "success"); onSaved();
    } catch (e) { toast((e as Error).message, "error"); setSaving(false); }
  };

  return (
    <Modal wide title={edit ? "Редактировать хостинг" : "Новый хостинг"} onClose={onClose}
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-[var(--t-mid)] hover:text-[var(--t-hi)]">Отмена</button>
        <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
          {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
        </button>
      </>}>
      <Field label="Название" value={name} onChange={setName} placeholder="Hetzner" />
      <Field label="Сайт" value={website} onChange={setWebsite} placeholder="https://hetzner.com" />
      <Field label="Особенности" value={features} onChange={setFeatures} placeholder="BBR, IPv6, DDoS-защита…" />
      <Field label="Примечания" value={notes} onChange={setNotes} placeholder="Личные заметки" />

      {/* Tariffs */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="label">Тарифы</label>
          <button type="button" onClick={() => setTariffs(ts => [...ts, emptyTariff()])}
            className="text-[11px] flex items-center gap-1 text-[var(--accent-hi)]"><Plus size={11} /> Добавить</button>
        </div>
        {tariffs.map((t, i) => (
          <div key={i} className="rounded-lg border border-[var(--line-soft)] p-2.5 flex flex-col gap-2 bg-[var(--bg2)]">
            <div className="flex items-center gap-2">
              <input value={t.name} onChange={e => setTariff(i, { name: e.target.value })}
                placeholder="Имя (CX22)" spellCheck={false} className="input flex-1" />
              <button type="button" onClick={() => setTariffs(ts => ts.filter((_, j) => j !== i))}
                className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><X size={13} /></button>
            </div>
            <input value={t.specs} onChange={e => setTariff(i, { specs: e.target.value })}
              placeholder="2 vCPU / 4 GB / 40 GB NVMe" spellCheck={false} className="input" />
            <div className="flex items-center gap-2">
              <input type="number" min={0} step="0.01" value={t.price || ""} onChange={e => setTariff(i, { price: parseFloat(e.target.value) || 0 })}
                placeholder="Цена" className="input w-24" />
              <select value={t.currency} onChange={e => setTariff(i, { currency: e.target.value })} className="selectbox w-24">
                {CURRENCIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <select value={t.period} onChange={e => setTariff(i, { period: e.target.value })} className="selectbox flex-1">
                {PERIODS.map(p => <option key={p.v} value={p.v}>{p.l}</option>)}
              </select>
            </div>
          </div>
        ))}
      </div>

      {/* Locations */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="label flex items-center gap-1"><MapPin size={12} /> Локации</label>
          <button type="button" onClick={() => setLocations(ls => [...ls, emptyLoc()])}
            className="text-[11px] flex items-center gap-1 text-[var(--accent-hi)]"><Plus size={11} /> Добавить</button>
        </div>
        {locations.length === 0 && <p className="text-[11px] text-[var(--t-faint)]">Локаций нет — они отмечаются на «Карте».</p>}
        {locations.map((l, i) => (
          <div key={i} className="rounded-lg border border-[var(--line-soft)] p-2.5 flex flex-col gap-2 bg-[var(--bg2)]">
            <div className="flex items-start gap-2">
              <div className="flex-1"><CountrySelect label="Страна" value={l.country_code} onChange={v => setLoc(i, { country_code: v })} /></div>
              <button type="button" onClick={() => setLocations(ls => ls.filter((_, j) => j !== i))}
                className="p-1 mt-5 text-[var(--t-low)] hover:text-[var(--err)]"><X size={13} /></button>
            </div>
            <input value={l.city} onChange={e => setLoc(i, { city: e.target.value })}
              placeholder="Город (Falkenstein)" spellCheck={false} className="input" />
            <div className="flex items-center gap-2">
              <input type="number" step="0.0001" value={l.lat || ""} onChange={e => setLoc(i, { lat: parseFloat(e.target.value) || 0 })}
                placeholder="Широта" className="input flex-1" />
              <input type="number" step="0.0001" value={l.lng || ""} onChange={e => setLoc(i, { lng: parseFloat(e.target.value) || 0 })}
                placeholder="Долгота" className="input flex-1" />
              <button type="button" onClick={() => autoCoords(i)} title="Определить координаты по городу/стране"
                className="flex items-center gap-1 px-2 py-1.5 rounded-md text-[11px] bg-[var(--bg3)] text-[var(--t-mid)] hover:text-[var(--accent-hi)]">
                <Wand2 size={12} /> Авто
              </button>
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
