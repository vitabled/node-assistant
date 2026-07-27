import { useState, useEffect, useCallback } from "react";
import {
  Server, Plus, Loader2, Pencil, Trash2, RefreshCw, ExternalLink,
  MapPin, Tag, X, Globe, Wand2, Network, FileText, Images,
} from "lucide-react";
import {
  hostingsApi, type Hosting, type HostingBody, type Tariff, type HostingLocation, type AsnRef,
  CURRENCIES, PERIODS, periodLabel, minTariff,
} from "./api";
import { TagInput } from "./TagInput";
import { resolveCoords } from "./geo";
import { CountrySelect } from "../CountrySelect";
import {
  MediaDrop, Lightbox, MediaImg, downloadMedia, fetchMediaMeta, fmtSize, type MediaItem,
} from "../common/MediaDrop";
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

/** Thumbnails for a record's attachments, resolved from a metadata map the page
 *  loads once (never per render — `fetchMediaMeta` pulls the whole index).
 *
 *  Only raster images get an `<img>`: the backend serves everything else as an
 *  opaque attachment, so an SVG/PDF/video is a download link instead.
 *
 *  Every click is stopped here — the hosting card behind this strip is itself a
 *  button that opens the full view, and opening a picture must not also open it. */
function MediaStrip({ ids, meta, size = 56, max }: {
  ids: string[]; meta: Map<string, MediaItem>; size?: number; max?: number;
}) {
  const [zoom, setZoom] = useState<MediaItem | null>(null);
  const items = ids.map(id => meta.get(id)).filter((m): m is MediaItem => !!m);
  if (items.length === 0) return null;
  const shown = max ? items.slice(0, max) : items;
  const rest = items.length - shown.length;

  return (
    <div className="flex flex-wrap items-center gap-1.5" onClick={e => e.stopPropagation()}>
      {shown.map(m => (m.inline ? (
        <MediaImg key={m.id} item={m} title={`${m.name} · ${fmtSize(m.size)}`}
          onClick={e => { e.stopPropagation(); setZoom(m); }}
          style={{
            width: size, height: size, objectFit: "cover", display: "block", cursor: "zoom-in",
            borderRadius: 8, border: "1px solid var(--line-soft)",
          }} />
      ) : (
        <button key={m.id} type="button" title={`${m.name} · ${fmtSize(m.size)} — скачать`}
          onClick={e => { e.stopPropagation(); void downloadMedia(m); }}
          className="flex flex-col items-center justify-center gap-0.5 text-[10px]"
          style={{
            width: size, height: size, borderRadius: 8, padding: 4,
            border: "1px solid var(--line-soft)", background: "var(--bg3)", color: "var(--t-low)",
          }}>
          <FileText size={15} />
          <span className="trunc" style={{ maxWidth: size - 10 }}>{m.name}</span>
        </button>
      )))}
      {rest > 0 && <span className="text-[11px] text-[var(--t-faint)]">+{rest}</span>}
      {zoom && <Lightbox item={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

export function HostingsCatalog() {
  const [rows, setRows] = useState<Hosting[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Hosting }>(null);
  const [details, setDetails] = useState<Hosting | null>(null);
  const [tagFilter, setTagFilter] = useState<string>("");
  const [media, setMedia] = useState<Map<string, MediaItem>>(new Map());

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await hostingsApi.list()); }
    catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  // Resolve every attachment of every card in ONE request (fetchMediaMeta reads
  // the whole index anyway — doing it per card would be N requests per page).
  // `rows` only changes on load/save, so this is not a per-render fetch.
  useEffect(() => {
    const ids = Array.from(new Set(rows.flatMap(h => h.media || [])));
    if (ids.length === 0) { setMedia(new Map()); return; }
    let alive = true;
    void fetchMediaMeta(ids).then(items => {
      if (alive) setMedia(new Map(items.map(m => [m.id, m])));
    });
    return () => { alive = false; };
  }, [rows]);

  const shown = tagFilter ? rows.filter(h => (h.tags || []).includes(tagFilter)) : rows;

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

      {tagFilter && (
        <div className="flex items-center gap-2 mb-3 text-xs text-[var(--t-mid)]">
          <span>Фильтр по тегу:</span>
          <span className="flex items-center gap-1 rounded-full px-2 py-0.5 bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)]">
            <Tag size={11} /> {tagFilter}
            <button onClick={() => setTagFilter("")} className="hover:text-[var(--t-hi)]" title="Сбросить"><X size={11} /></button>
          </span>
          <span className="text-[var(--t-faint)]">{shown.length} из {rows.length}</span>
        </div>
      )}

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : rows.length === 0 ? (
        <div className="card p-8 text-center text-[var(--t-faint)] text-sm">Хостингов пока нет. Добавьте первый — его локации появятся на «Карте».</div>
      ) : shown.length === 0 ? (
        <div className="card p-8 text-center text-[var(--t-faint)] text-sm">Нет хостингов с тегом «{tagFilter}».</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {shown.map(h => {
            const mt = minTariff(h);
            // The card itself opens the full view; the icon buttons keep their
            // own actions and must stopPropagation so they don't also trigger it.
            return (
              <div key={h.id} className="card p-4 flex flex-col gap-2.5 cursor-pointer"
                onClick={() => setDetails(h)} role="button" tabIndex={0}
                onKeyDown={e => { if (e.key === "Enter") setDetails(h); }}
                title="Открыть полные данные">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="text-sm font-medium text-[var(--t-hi)] truncate block">{h.name}</span>
                    {h.website && (
                      <a href={h.website} target="_blank" rel="noopener noreferrer"
                        onClick={e => e.stopPropagation()}
                        className="text-[11px] text-[var(--t-low)] hover:text-[var(--accent-hi)] flex items-center gap-1 truncate">
                        <ExternalLink size={10} /> {h.website.replace(/^https?:\/\//, "")}
                      </a>
                    )}
                  </div>
                  <div className="flex shrink-0">
                    <button title="Изменить" onClick={e => { e.stopPropagation(); setModal({ edit: h }); }} className="p-1 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Pencil size={12} /></button>
                    <button title="Удалить" onClick={e => { e.stopPropagation(); del(h); }} className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><Trash2 size={12} /></button>
                  </div>
                </div>

                {h.features && <p className="text-xs text-[var(--t-low)] line-clamp-2">{h.features}</p>}

                {(h.tags || []).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {(h.tags || []).map(t => (
                      <button key={t} onClick={e => { e.stopPropagation(); setTagFilter(t); }}
                        title={`Показать хостинги с тегом «${t}»`}
                        className="flex items-center gap-1 text-[10px] rounded-full px-1.5 py-0.5 bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)] hover:bg-[var(--accent)] hover:text-[var(--primary-ink)]">
                        <Tag size={9} /> {t}
                      </button>
                    ))}
                  </div>
                )}

                <MediaStrip ids={h.media || []} meta={media} size={56} max={4} />

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
      {details && (
        <HostingDetails h={details} meta={media} onClose={() => setDetails(null)}
          onEdit={() => { setModal({ edit: details }); setDetails(null); }} />
      )}
    </Page>
  );
}

/** Read-only full view of one hosting: every tariff (with channel width) and
 *  every location. Editing stays in `HostingModal`.
 *
 *  `meta` is the page-level media index — reused rather than re-fetched, so
 *  opening a card costs no extra request. */
function HostingDetails({ h, meta, onClose, onEdit }: {
  h: Hosting; meta: Map<string, MediaItem>; onClose: () => void; onEdit: () => void;
}) {
  return (
    <Modal title={h.name} onClose={onClose} wide
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-[var(--t-mid)] hover:text-[var(--t-hi)]">Закрыть</button>
        <button onClick={onEdit} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]">
          <Pencil size={12} /> Изменить
        </button>
      </>}>
      <div className="flex flex-col gap-4">
        {h.website && (
          <a href={h.website} target="_blank" rel="noopener noreferrer"
            className="text-xs text-[var(--accent-hi)] flex items-center gap-1 truncate">
            <ExternalLink size={11} /> {h.website}
          </a>
        )}
        {h.features && <p className="text-xs text-[var(--t-mid)] whitespace-pre-wrap">{h.features}</p>}
        {h.notes && <p className="text-xs text-[var(--t-low)] whitespace-pre-wrap">{h.notes}</p>}

        {(h.tags || []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {(h.tags || []).map(t => (
              <span key={t} className="flex items-center gap-1 text-[11px] rounded-full px-2 py-0.5 bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)]">
                <Tag size={10} /> {t}
              </span>
            ))}
          </div>
        )}

        {/* Guarded on RESOLVED ids, not on the raw list: an id whose file was
            removed from the shared store would otherwise leave a bare header. */}
        {(h.media || []).some(id => meta.has(id)) && (
          <div>
            <p className="label mb-1 flex items-center gap-1"><Images size={12} /> Медиа</p>
            <MediaStrip ids={h.media || []} meta={meta} size={72} />
          </div>
        )}

        {(h.asns || []).length > 0 && (
          <div>
            <p className="label mb-1 flex items-center gap-1"><Network size={12} /> ASN</p>
            <div className="flex flex-col gap-1">
              {(h.asns || []).map((a, i) => (
                <p key={i} className="text-xs text-[var(--t-mid)] flex items-center gap-1.5 flex-wrap">
                  <span className="tabular-nums text-[var(--t-hi)]">AS{a.number}</span>
                  {a.name && <span>· {a.name}</span>}
                  {a.website && (
                    <a href={a.website} target="_blank" rel="noopener noreferrer"
                      className="text-[var(--accent-hi)] flex items-center gap-1">
                      <ExternalLink size={10} /> {a.website.replace(/^https?:\/\//, "")}
                    </a>
                  )}
                </p>
              ))}
            </div>
          </div>
        )}

        <div>
          <p className="label mb-1 flex items-center gap-1"><Tag size={12} /> Тарифы</p>
          {h.tariffs.length === 0 ? (
            <p className="text-xs text-[var(--t-faint)]">Тарифов нет.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="tbl text-xs w-full">
                <thead>
                  <tr><th>Тариф</th><th>Характеристики</th><th>Канал</th><th className="text-right">Цена</th></tr>
                </thead>
                <tbody>
                  {h.tariffs.map((t, i) => (
                    <tr key={i}>
                      <td className="text-[var(--t-hi)]">{t.name || "—"}</td>
                      <td className="text-[var(--t-low)]">{t.specs || "—"}</td>
                      <td className="text-[var(--t-low)]">{t.bandwidth || "—"}</td>
                      <td className="text-right tabular-nums whitespace-nowrap">
                        {t.price > 0
                          ? <>{fmtNum(t.price, t.currency)}<span className="text-[var(--t-faint)]">{periodLabel(t.period)}</span></>
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div>
          <p className="label mb-1 flex items-center gap-1"><MapPin size={12} /> Локации</p>
          {h.locations.length === 0 ? (
            <p className="text-xs text-[var(--t-faint)]">Локаций нет.</p>
          ) : (
            <div className="flex flex-col gap-1">
              {h.locations.map((l, i) => (
                <p key={i} className="text-xs text-[var(--t-mid)] flex items-center gap-1.5">
                  <Flag code={l.country_code} size={14} />
                  {l.city || l.country_code || "без страны"}
                  {l.note && <span className="text-[11px] text-[var(--t-faint)]">· {l.note}</span>}
                </p>
              ))}
            </div>
          )}
        </div>

      </div>
    </Modal>
  );
}

const emptyTariff = (): Tariff => ({ name: "", specs: "", bandwidth: "", price: 0, currency: "USD", period: "mo" });
const emptyLoc = (): HostingLocation => ({ city: "", country_code: "", lat: 0, lng: 0, note: "" });
const emptyAsn = (): AsnRef => ({ number: 0, name: "", website: "" });

function HostingModal({ edit, onClose, onSaved }: { edit?: Hosting; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(edit?.name ?? "");
  const [website, setWebsite] = useState(edit?.website ?? "");
  const [features, setFeatures] = useState(edit?.features ?? "");
  const [notes, setNotes] = useState(edit?.notes ?? "");
  const [tags, setTags] = useState<string[]>(edit?.tags ?? []);
  // Records saved before the field existed come back without the key.
  const [media, setMedia] = useState<string[]>(edit?.media ?? []);
  const [tariffs, setTariffs] = useState<Tariff[]>(edit?.tariffs?.length ? edit.tariffs : [emptyTariff()]);
  const [locations, setLocations] = useState<HostingLocation[]>(edit?.locations ?? []);
  const [asns, setAsns] = useState<AsnRef[]>(edit?.asns ?? []);
  const [saving, setSaving] = useState(false);

  const setTariff = (i: number, patch: Partial<Tariff>) =>
    setTariffs(ts => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const setLoc = (i: number, patch: Partial<HostingLocation>) =>
    setLocations(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const setAsn = (i: number, patch: Partial<AsnRef>) =>
    setAsns(as => as.map((a, j) => (j === i ? { ...a, ...patch } : a)));

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
    // `bandwidth` counts as content too — otherwise a tariff that only records a
    // channel width would be silently discarded on save.
    const cleanTariffs = tariffs.filter(
      t => t.name.trim() || t.specs.trim() || (t.bandwidth || "").trim() || t.price > 0);
    const cleanLocs = locations.filter(l => l.country_code || l.city.trim());
    const cleanAsns = asns.filter(a => a.number > 0 || a.name.trim() || a.website.trim());
    const body: HostingBody = {
      name: name.trim(), website: website.trim(), features: features.trim(), notes: notes.trim(),
      tags, media, tariffs: cleanTariffs, locations: cleanLocs, asns: cleanAsns,
      provider_ref: edit?.provider_ref ?? null,
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

      <TagInput label="Теги" value={tags} onChange={setTags} />

      <MediaDrop value={media} onChange={setMedia}
        hint="Скриншоты панели, прайс, схема сети. До 15 МБ на файл." />

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
            <input value={t.bandwidth ?? ""} onChange={e => setTariff(i, { bandwidth: e.target.value })}
              placeholder="Канал: 1 Гбит/с, 20 ТБ" spellCheck={false} className="input" />
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

      {/* ASN */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="label flex items-center gap-1"><Network size={12} /> ASN</label>
          <button type="button" onClick={() => setAsns(as => [...as, emptyAsn()])}
            className="text-[11px] flex items-center gap-1 text-[var(--accent-hi)]"><Plus size={11} /> Добавить</button>
        </div>
        {asns.length === 0 && <p className="text-[11px] text-[var(--t-faint)]">Автономные системы провайдера (можно заполнить из «Анализа подписки»).</p>}
        {asns.map((a, i) => (
          <div key={i} className="rounded-lg border border-[var(--line-soft)] p-2.5 flex flex-col gap-2 bg-[var(--bg2)]">
            <div className="flex items-center gap-2">
              <span className="text-[var(--t-low)] text-xs">AS</span>
              <input type="number" min={0} value={a.number || ""} onChange={e => setAsn(i, { number: parseInt(e.target.value) || 0 })}
                placeholder="12345" className="input w-28" />
              <input value={a.name} onChange={e => setAsn(i, { name: e.target.value })}
                placeholder="Имя (Selectel)" spellCheck={false} className="input flex-1" />
              <button type="button" onClick={() => setAsns(as => as.filter((_, j) => j !== i))}
                className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><X size={13} /></button>
            </div>
            <input value={a.website} onChange={e => setAsn(i, { website: e.target.value })}
              placeholder="Сайт ASN (https://…)" spellCheck={false} className="input" />
          </div>
        ))}
      </div>
    </Modal>
  );
}
