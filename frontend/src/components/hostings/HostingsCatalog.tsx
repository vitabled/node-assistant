import { Fragment, useState, useEffect, useCallback, useMemo, type ReactNode } from "react";
import {
  Server, Plus, Loader2, Pencil, Trash2, RefreshCw, ExternalLink,
  MapPin, Tag, X, Globe, Wand2, Network, FileText, Images,
  SlidersHorizontal, ChevronDown, RotateCcw, Gauge, StickyNote, Plug, Activity,
} from "lucide-react";
import {
  hostingsApi, type Hosting, type HostingBody, type Tariff, type HostingLocation, type AsnRef,
  type HostingMetrics, type MetricKey, type NoteField, type BsSubnet,
  CURRENCIES, PERIODS, periodLabel, minTariff,
} from "./api";
import {
  METRIC_DEFS, metricColor, avgScore, scoreOf, metricsOf, fmtScore, type MetricDef,
} from "./metrics";
import { parseChannel } from "./channel";
import { ChannelBar, ChannelStrip } from "./ChannelBar";
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

/** Компактная строка оценок для карточки: только заполненные, цифра в цвете. */
/**
 * Общая оценка хостинга — среднее по ЗАПОЛНЕННЫМ метрикам (скрытый fair use не
 * учитывается, см. avgScore). Не показывается вовсе, когда не оценено ничего:
 * «0.0» читалось бы как плохая оценка, а не как отсутствие данных.
 */
function ScoreBadge({ m }: { m: HostingMetrics }) {
  const avg = avgScore(m);
  if (avg === null) return null;
  return (
    <span className="text-[11px] font-semibold tabular-nums rounded px-1.5 py-0.5 shrink-0"
      style={{ color: metricColor(avg), background: "var(--bg3)" }}
      title="Общая оценка — среднее по заполненным метрикам">
      {fmtScore(avg)}
    </span>
  );
}

function MetricsRow({ m }: { m: HostingMetrics }) {
  const items = METRIC_DEFS
    .map(d => ({ d, v: scoreOf(m, d.key) }))
    .filter((x): x is { d: MetricDef; v: number } => x.v !== null);
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
      {items.map(({ d, v }) => (
        <span key={d.key} className="flex items-baseline gap-1 text-[10px] text-[var(--t-low)]">
          <span className="text-[12px] font-semibold tabular-nums" style={{ color: metricColor(v) }}>{fmtScore(v)}</span>
          {d.short}
        </span>
      ))}
    </div>
  );
}

/** Тариф с самым широким РАСПОЗНАННЫМ каналом. Им карточка и представляет
 *  провайдера, по нему же работает фильтр «минимум Мбит/с» — иначе фильтр и
 *  показанная полоска говорили бы о разных тарифах. */
function widestTariff(h: Hosting): { tariff: Tariff; mbps: number } | null {
  let best: { tariff: Tariff; mbps: number } | null = null;
  for (const t of h.tariffs || []) {
    const mbps = parseChannel(t.bandwidth || "");
    if (mbps !== null && (best === null || mbps > best.mbps)) best = { tariff: t, mbps };
  }
  return best;
}

// ── Фильтр и сортировка ───────────────────────────────────────

const NO_MINS: Record<MetricKey, number> = {
  price: 0, quality: 0, loyalty: 0, fairuse: 0, panel: 0, ru_access: 0,
};

/** «все / есть / нет» — общая форма для признаков-флагов. */
type Tri = "any" | "yes" | "no";
const TRI_OPTS: { v: Tri; l: string }[] = [
  { v: "any", l: "все" }, { v: "yes", l: "есть" }, { v: "no", l: "нет" },
];
const triOk = (t: Tri, has: boolean) => t === "any" || (t === "yes") === has;

/** У API-фильтра свой четвёртый вариант: «неизвестно» — это отдельное значение
 *  поля, а не отсутствие фильтра. */
type ApiFilter = "any" | "yes" | "no" | "unknown";
const API_FILTER_OPTS: { v: ApiFilter; l: string }[] = [
  { v: "any", l: "все" }, { v: "yes", l: "есть" }, { v: "no", l: "нет" }, { v: "unknown", l: "неизвестно" },
];

/** Признаки карточки, не связанные с оценками. */
interface Facets {
  media: Tri; tariffs: Tri; notes: Tri; api: ApiFilter; minMbps: number;
}
const NO_FACETS: Facets = { media: "any", tariffs: "any", notes: "any", api: "any", minMbps: 0 };
const facetCount = (f: Facets) =>
  (f.media !== "any" ? 1 : 0) + (f.tariffs !== "any" ? 1 : 0) + (f.notes !== "any" ? 1 : 0)
  + (f.api !== "any" ? 1 : 0) + (f.minMbps > 0 ? 1 : 0);

/** Ступени канала логарифмические — как и шкала полоски: между 100 Мбит и
 *  10 Гбит разница в 100 раз, равномерный шаг здесь бесполезен. */
const CHANNEL_OPTS: { v: number; l: string }[] = [
  { v: 0, l: "любой" }, { v: 100, l: "100+" }, { v: 500, l: "500+" },
  { v: 1000, l: "1 Гбит+" }, { v: 2500, l: "2.5 Гбит+" }, { v: 10000, l: "10 Гбит+" },
];

/** Чипсет «вариант „все“ + значения»; активный подсвечен акцентом (`.seg accent`). */
function ChipSet<T extends string | number>({ label, icon, value, options, onChange }: {
  label: string; icon?: ReactNode;
  value: T; options: { v: T; l: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="label flex items-center gap-1">{icon} {label}</span>
      <div className="seg seg-wrap mini accent">
        {options.map(o => (
          <button key={String(o.v)} type="button" className={value === o.v ? "on" : ""}
            onClick={() => onChange(o.v)}>{o.l}</button>
        ))}
      </div>
    </div>
  );
}

const SORT_GENERAL: { v: string; l: string }[] = [
  // «По умолчанию» = порядок добавления с сервера: это состояние каталога до
  // появления фильтра, и сбрасываться надо именно в него.
  { v: "default", l: "По умолчанию" },
  { v: "name", l: "По названию" },
  { v: "avg:desc", l: "Средний балл ↓" },
  { v: "avg:asc", l: "Средний балл ↑" },
  { v: "tags:desc", l: "Совпадение тегов ↓" },
  { v: "tariff:asc", l: "Цена тарифа ↑" },
  { v: "tariff:desc", l: "Цена тарифа ↓" },
];

/** Значение сортировки; `null` — «не оценено»/«без цены».
 *  ⚠️ Цены тарифов не приводятся к одной валюте (курсов здесь нет) — сортировка
 *  по цене честна лишь внутри одной валюты. */
function sortValue(h: Hosting, key: string, picked: string[]): number | null {
  if (key === "avg") return avgScore(metricsOf(h));
  // Совпадение с выбранным набором тегов. Считается ЗДЕСЬ, а не в metrics.ts:
  // это свойство пары «карточка + текущий фильтр», а не самой карточки.
  if (key === "tags") return picked.length ? (h.tags || []).filter(t => picked.includes(t)).length : null;
  if (key === "tariff") return minTariff(h)?.price ?? null;
  return scoreOf(metricsOf(h), key as MetricKey);
}

export function sortHostings(list: Hosting[], sort: string, picked: string[] = []): Hosting[] {
  const byName = (a: Hosting, b: Hosting) => a.name.localeCompare(b.name, "ru");
  if (sort === "default") return list;
  if (sort === "name") return [...list].sort(byName);
  const [key, dir] = sort.split(":");
  return [...list].sort((a, b) => {
    const av = sortValue(a, key, picked), bv = sortValue(b, key, picked);
    // Незаполненные всегда внизу, независимо от направления — иначе сортировка
    // «по возрастанию» начиналась бы с карточек вообще без оценок.
    if (av === null || bv === null) return av === bv ? byName(a, b) : (av === null ? 1 : -1);
    return (dir === "asc" ? av - bv : bv - av) || byName(a, b);
  });
}

function FilterBar({
  mins, onMin, sort, onSort, onlyScored, onOnlyScored,
  tags, allTags, onToggleTag, onClearTags, tagMode, onTagMode, facets, onFacet,
  onReset, shown, total,
}: {
  mins: Record<MetricKey, number>; onMin: (k: MetricKey, v: number) => void;
  sort: string; onSort: (v: string) => void;
  onlyScored: boolean; onOnlyScored: (v: boolean) => void;
  tags: string[]; allTags: string[]; onToggleTag: (t: string) => void; onClearTags: () => void;
  tagMode: "all" | "any"; onTagMode: (v: "all" | "any") => void;
  facets: Facets; onFacet: (patch: Partial<Facets>) => void;
  onReset: () => void; shown: number; total: number;
}) {
  const [open, setOpen] = useState(false);
  const active = METRIC_DEFS.filter(d => mins[d.key] > 0).length
    + (onlyScored ? 1 : 0) + (sort !== "default" ? 1 : 0)
    + tags.length + facetCount(facets);

  return (
    <div className="card mb-4">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-[var(--bg3)] rounded-[inherit]">
        <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-[var(--t-low)]">
          <SlidersHorizontal size={13} /> Фильтр и сортировка
          {active > 0 && (
            <span className="rounded-full px-1.5 py-0.5 text-[10px] normal-case tracking-normal bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)]">
              {active}
            </span>
          )}
        </span>
        <span className="flex items-center gap-2 text-[11px] text-[var(--t-low)]">
          {shown} из {total}
          <ChevronDown size={14} className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--line-soft)] px-3 pt-3 pb-3 flex flex-col gap-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
              <label className="label">Сортировка</label>
              <select value={sort} onChange={e => onSort(e.target.value)} className="selectbox">
                <optgroup label="Общее">
                  {SORT_GENERAL.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
                </optgroup>
                <optgroup label="Оценки">
                  {METRIC_DEFS.flatMap(d => [
                    <option key={`${d.key}:desc`} value={`${d.key}:desc`}>{d.label} ↓</option>,
                    <option key={`${d.key}:asc`} value={`${d.key}:asc`}>{d.label} ↑</option>,
                  ])}
                </optgroup>
              </select>
            </div>
            <label className="flex items-center gap-2 py-2 text-xs text-[var(--t-mid)] cursor-pointer">
              <input type="checkbox" checked={onlyScored} onChange={e => onOnlyScored(e.target.checked)}
                style={{ accentColor: "var(--accent)" }} />
              Только с оценками
            </label>
            <button type="button" onClick={onReset}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] bg-[var(--bg3)] text-[var(--t-mid)] hover:text-[var(--accent-hi)]">
              <RotateCcw size={12} /> Сбросить
            </button>
          </div>

          {allTags.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="label flex items-center gap-1">
                <Tag size={12} /> Теги
                {tags.length > 1 && (
                  <span className="normal-case tracking-normal font-normal text-[10px] text-[var(--t-faint)]">
                    · {tagMode === "all" ? "показываем со всеми выбранными" : "достаточно одного из выбранных"}
                  </span>
                )}
                {tags.length > 1 && (
                  <span className="seg mini accent" style={{ marginLeft: 6 }}>
                    <button type="button" className={tagMode === "all" ? "on" : ""}
                      onClick={() => onTagMode("all")}>все</button>
                    <button type="button" className={tagMode === "any" ? "on" : ""}
                      onClick={() => onTagMode("any")}>любой</button>
                  </span>
                )}
              </span>
              <div className="seg seg-wrap mini accent">
                <button type="button" className={tags.length === 0 ? "on" : ""} onClick={onClearTags}>все</button>
                {allTags.map(t => (
                  <button key={t} type="button" className={tags.includes(t) ? "on" : ""}
                    onClick={() => onToggleTag(t)}>{t}</button>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-3">
            <ChipSet label="Канал (минимум)" icon={<Activity size={12} />}
              value={facets.minMbps} options={CHANNEL_OPTS}
              onChange={v => onFacet({ minMbps: v })} />
            <ChipSet label="API провайдера" icon={<Plug size={12} />}
              value={facets.api} options={API_FILTER_OPTS}
              onChange={v => onFacet({ api: v })} />
            <ChipSet label="Вложения" icon={<Images size={12} />}
              value={facets.media} options={TRI_OPTS} onChange={v => onFacet({ media: v })} />
            <ChipSet label="Тарифы" icon={<Tag size={12} />}
              value={facets.tariffs} options={TRI_OPTS} onChange={v => onFacet({ tariffs: v })} />
            <ChipSet label="Заметки" icon={<StickyNote size={12} />}
              value={facets.notes} options={TRI_OPTS} onChange={v => onFacet({ notes: v })} />
          </div>

          {/* Подпись появилась вместе со вторым блоком чипсетов: без неё ползунки
              читались бы как продолжение фильтров по признакам. */}
          <span className="label flex items-center gap-1"><Gauge size={12} /> Минимальные оценки</span>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 -mt-1.5">
            {METRIC_DEFS.map(d => (
              <div key={d.key} className="flex items-center gap-2">
                <span className="text-[11px] text-[var(--t-low)] w-[124px] shrink-0">{d.label}</span>
                <input type="range" min={0} max={100} step={1} value={mins[d.key]}
                  onChange={e => onMin(d.key, parseInt(e.target.value, 10))}
                  title="Минимальная оценка" className="flex-1 min-w-0"
                  style={{ accentColor: "var(--accent)" }} />
                <span className="text-[11px] tabular-nums w-9 text-right"
                  style={{ color: mins[d.key] ? metricColor(mins[d.key]) : "var(--t-faint)" }}>
                  {mins[d.key] ? `${mins[d.key]}+` : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function HostingsCatalog() {
  const [rows, setRows] = useState<Hosting[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Hosting }>(null);
  const [details, setDetails] = useState<Hosting | null>(null);
  // Мультивыбор; клик по тегу на карточке добавляет его к набору (повторный —
  // снимает), поэтому прежний сценарий «кликнул тег → увидел его хостинги» цел.
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  // «все» (И) — прежнее поведение и дефолт; «любой» (ИЛИ) нужен, чтобы
  // сортировка по совпадению тегов не вырождалась: при И у всех совпадение
  // одинаковое и ранжировать нечего.
  const [tagMode, setTagMode] = useState<"all" | "any">("all");
  const [media, setMedia] = useState<Map<string, MediaItem>>(new Map());
  const [mins, setMins] = useState<Record<MetricKey, number>>({ ...NO_MINS });
  const [sort, setSort] = useState("default");
  const [onlyScored, setOnlyScored] = useState(false);
  const [facets, setFacets] = useState<Facets>({ ...NO_FACETS });

  const toggleTag = (t: string) =>
    setTagFilter(p => (p.includes(t) ? p.filter(x => x !== t) : [...p, t]));
  const allTags = useMemo(
    () => Array.from(new Set(rows.flatMap(h => h.tags || []))).sort((a, b) => a.localeCompare(b, "ru")),
    [rows]);

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

  // Все фильтры ложатся друг на друга (И), порядок проверок значения не имеет.
  const shown = useMemo(() => {
    const list = rows.filter(h => {
      // «все» — карточка обязана иметь каждый выбранный тег; «любой» —
      // достаточно одного, а порядок задаёт сортировка по совпадению.
      if (tagFilter.length) {
        const has = (t: string) => (h.tags || []).includes(t);
        if (!(tagMode === "all" ? tagFilter.every(has) : tagFilter.some(has))) return false;
      }
      if (facets.minMbps > 0) {
        // Нераспознанный канал порог НЕ проходит: домысливать ширину из строки,
        // которую парсер не понял, — хуже, чем скрыть карточку под фильтром.
        const w = widestTariff(h);
        if (!w || w.mbps < facets.minMbps) return false;
      }
      if (!triOk(facets.media, (h.media || []).length > 0)) return false;
      if (!triOk(facets.tariffs, (h.tariffs || []).length > 0)) return false;
      if (!triOk(facets.notes, (h.note_fields || []).length > 0)) return false;
      if (facets.api !== "any") {
        const a = h.has_api ?? null;
        if (facets.api === "unknown" ? a !== null : a !== (facets.api === "yes")) return false;
      }
      const m = metricsOf(h);
      if (onlyScored && avgScore(m) === null) return false;
      return METRIC_DEFS.every(d => {
        const min = mins[d.key];
        if (!min) return true;
        const v = scoreOf(m, d.key);
        return v !== null && v >= min;
      });
    });
    return sortHostings(list, sort, tagFilter);
  }, [rows, tagFilter, tagMode, facets, mins, onlyScored, sort]);

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

      {tagFilter.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-3 text-xs text-[var(--t-mid)]">
          <span>Фильтр по тегам:</span>
          {tagFilter.map(t => (
            <span key={t} className="flex items-center gap-1 rounded-full px-2 py-0.5 bg-[var(--accent-dim)] text-[var(--accent-hi)] border border-[var(--accent-line)]">
              <Tag size={11} /> {t}
              <button onClick={() => toggleTag(t)} className="hover:text-[var(--t-hi)]" title="Снять тег"><X size={11} /></button>
            </span>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <FilterBar mins={mins} onMin={(k, v) => setMins(p => ({ ...p, [k]: v }))}
          sort={sort} onSort={setSort} onlyScored={onlyScored} onOnlyScored={setOnlyScored}
          tags={tagFilter} allTags={allTags} onToggleTag={toggleTag} onClearTags={() => setTagFilter([])}
          tagMode={tagMode} onTagMode={setTagMode}
          facets={facets} onFacet={patch => setFacets(p => ({ ...p, ...patch }))}
          onReset={() => {
            setMins({ ...NO_MINS }); setSort("default"); setOnlyScored(false);
            setTagFilter([]); setTagMode("all"); setFacets({ ...NO_FACETS });
          }}
          shown={shown.length} total={rows.length} />
      )}

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : rows.length === 0 ? (
        <div className="card p-8 text-center text-[var(--t-faint)] text-sm">Хостингов пока нет. Добавьте первый — его локации появятся на «Карте».</div>
      ) : shown.length === 0 ? (
        <div className="card p-8 text-center text-[var(--t-faint)] text-sm">
          Ничего не найдено — ослабьте фильтр{tagFilter.length ? ` или снимите теги: ${tagFilter.join(", ")}` : ""}.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {shown.map(h => {
            const mt = minTariff(h);
            const wide = widestTariff(h);
            // The card itself opens the full view; the icon buttons keep their
            // own actions and must stopPropagation so they don't also trigger it.
            return (
              <div key={h.id} className="card p-4 flex flex-col gap-2.5 cursor-pointer"
                onClick={() => setDetails(h)} role="button" tabIndex={0}
                onKeyDown={e => { if (e.key === "Enter") setDetails(h); }}
                title="Открыть полные данные">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="flex items-center gap-2 min-w-0">
                      <span className="text-sm font-medium text-[var(--t-hi)] truncate">{h.name}</span>
                      <ScoreBadge m={metricsOf(h)} />
                    </span>
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

                <MetricsRow m={metricsOf(h)} />

                {(h.tags || []).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {(h.tags || []).map(t => (
                      <button key={t} onClick={e => { e.stopPropagation(); toggleTag(t); }}
                        title={tagFilter.includes(t) ? `Снять тег «${t}»` : `Показать хостинги с тегом «${t}»`}
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

                {/* Полоска — по одному сегменту на тариф, цвет сегмента по его
                    каналу. Фильтр «минимум Мбит/с» по-прежнему смотрит на самый
                    широкий канал (`widestTariff`) — он и есть самый яркий сегмент. */}
                {wide && (
                  <div className="flex items-center gap-2 mt-auto">
                    <span className="text-[10px] uppercase tracking-widest text-[var(--t-faint)] shrink-0">канал</span>
                    <div className="flex-1 min-w-0">
                      <ChannelStrip tariffs={h.tariffs} />
                    </div>
                  </div>
                )}

                <div className={`flex items-center justify-between ${wide ? "" : "mt-auto"} pt-2 text-xs border-t border-[var(--line-soft)]`}>
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
  const m = metricsOf(h);
  const avg = avgScore(m);
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

        {/* «Неизвестно» не показываем: строка без информации только шумит. */}
        {h.has_api != null && (
          <p className="text-xs text-[var(--t-mid)] flex items-center gap-1.5">
            <Plug size={12} /> API провайдера: {h.has_api ? "есть" : "нет"}
          </p>
        )}

        {(h.note_fields || []).length > 0 && (
          <div>
            <p className="label mb-1 flex items-center gap-1"><StickyNote size={12} /> Заметки</p>
            <div className="flex flex-col gap-2">
              {(h.note_fields || []).map((n, i) => (
                <div key={i}>
                  {n.topic && <p className="text-xs font-semibold text-[var(--t-hi)]">{n.topic}</p>}
                  {n.text && <p className="text-xs text-[var(--t-mid)] whitespace-pre-wrap">{n.text}</p>}
                </div>
              ))}
            </div>
          </div>
        )}

        {(h.bs_subnets || []).length > 0 && (
          <div>
            <p className="label mb-1 flex items-center gap-1"><Network size={12} /> БС подсети</p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs" style={{ minWidth: 520 }}>
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-[var(--t-low)]">
                    <th className="text-left font-medium pb-1">Сеть</th>
                    <th className="text-left font-medium pb-1">ASN</th>
                    <th className="text-left font-medium pb-1">Организация</th>
                    <th className="text-left font-medium pb-1">Проверка</th>
                    <th className="text-left font-medium pb-1">Отклик</th>
                  </tr>
                </thead>
                <tbody className="text-[var(--t-mid)]">
                  {(h.bs_subnets || []).map((r, i) => (
                    <tr key={i} className="border-t border-[var(--line-soft)]">
                      <td className="py-1 pr-2 font-mono text-[var(--t-hi)]">{r.network || "—"}</td>
                      <td className="py-1 pr-2">{r.asn || "—"}</td>
                      <td className="py-1 pr-2">{r.org || "—"}</td>
                      <td className="py-1 pr-2 tabular-nums">{r.checked_at || "—"}</td>
                      <td className="py-1">{r.response || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div>
          <p className="label mb-1 flex items-center gap-1">
            <Gauge size={12} /> Оценки
            {avg !== null && (
              <span className="normal-case tracking-normal text-[11px] text-[var(--t-low)]">
                · средний <span className="tabular-nums font-semibold" style={{ color: metricColor(avg) }}>{fmtScore(avg)}</span>
              </span>
            )}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1">
            {METRIC_DEFS.filter(d => !(d.key === "fairuse" && m.fairuse_hidden)).map(d => {
              const v = scoreOf(m, d.key);
              return (
                <p key={d.key} className="flex items-baseline justify-between gap-2 text-xs text-[var(--t-low)]">
                  {d.label}
                  <span className="tabular-nums font-semibold" style={{ color: metricColor(v) }}>
                    {v === null ? "—" : fmtScore(v)}
                  </span>
                </p>
              );
            })}
          </div>
        </div>

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
                    <Fragment key={i}>
                      <tr>
                        <td className="text-[var(--t-hi)]">{t.name || "—"}</td>
                        <td className="text-[var(--t-low)]">{t.specs || "—"}</td>
                        {/* ChannelBar сам отдаёт исходную строку, если скорость не
                            разобрана, — поэтому текст канала здесь не теряется. */}
                        <td className="text-[var(--t-low)] min-w-[120px]">
                          {t.bandwidth ? <ChannelBar text={t.bandwidth} /> : "—"}
                        </td>
                        <td className="text-right tabular-nums whitespace-nowrap">
                          {t.price > 0
                            ? <>{fmtNum(t.price, t.currency)}<span className="text-[var(--t-faint)]">{periodLabel(t.period)}</span></>
                            : "—"}
                        </td>
                      </tr>
                      {(t.note || "").trim() && (
                        <tr>
                          <td colSpan={4} className="text-[11px] text-[var(--t-low)] whitespace-pre-wrap">{t.note}</td>
                        </tr>
                      )}
                    </Fragment>
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

const emptyTariff = (): Tariff => ({ name: "", specs: "", bandwidth: "", price: 0, currency: "USD", period: "mo", note: "" });
const emptyLoc = (): HostingLocation => ({ city: "", country_code: "", lat: 0, lng: 0, note: "" });
const emptyAsn = (): AsnRef => ({ number: 0, name: "", website: "" });
const emptyNote = (): NoteField => ({ topic: "", text: "" });

/** Тот же предел, что и на бэкенде: он режет молча, поэтому кнопку гасим сами. */
const MAX_NOTE_FIELDS = 30;

const API_OPTS: { v: "unknown" | "yes" | "no"; l: string }[] = [
  { v: "unknown", l: "неизвестно" }, { v: "yes", l: "есть" }, { v: "no", l: "нет" },
];

function HostingModal({ edit, onClose, onSaved }: { edit?: Hosting; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(edit?.name ?? "");
  const [website, setWebsite] = useState(edit?.website ?? "");
  const [features, setFeatures] = useState(edit?.features ?? "");
  const [notes, setNotes] = useState(edit?.notes ?? "");
  const [tags, setTags] = useState<string[]>(edit?.tags ?? []);
  // Records saved before the field existed come back without the key.
  const [media, setMedia] = useState<string[]>(edit?.media ?? []);
  const [metrics, setMetrics] = useState<HostingMetrics>(edit?.metrics ?? {});
  const [tariffs, setTariffs] = useState<Tariff[]>(edit?.tariffs?.length ? edit.tariffs : [emptyTariff()]);
  const [locations, setLocations] = useState<HostingLocation[]>(edit?.locations ?? []);
  const [asns, setAsns] = useState<AsnRef[]>(edit?.asns ?? []);
  const [noteFields, setNoteFields] = useState<NoteField[]>(edit?.note_fields ?? []);
  const [bsSubnets, setBsSubnets] = useState<BsSubnet[]>(edit?.bs_subnets ?? []);
  // Храним трёхсостоянийным значением, а не строкой из чипсета: `null` уезжает
  // на бэкенд как «неизвестно» и обязан отличаться от `false`.
  const [hasApi, setHasApi] = useState<boolean | null>(edit?.has_api ?? null);
  const [saving, setSaving] = useState(false);

  const setNote = (i: number, patch: Partial<NoteField>) =>
    setNoteFields(ns => ns.map((n, j) => (j === i ? { ...n, ...patch } : n)));
  const setTariff = (i: number, patch: Partial<Tariff>) =>
    setTariffs(ts => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const setLoc = (i: number, patch: Partial<HostingLocation>) =>
    setLocations(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const setAsn = (i: number, patch: Partial<AsnRef>) =>
    setAsns(as => as.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  const setMetric = (k: MetricKey, v: number | null) =>
    setMetrics(p => { const next: HostingMetrics = { ...p }; next[k] = v; return next; });

  // Fill lat/lng from the city+country gazetteer.
  const autoCoords = (i: number) => {
    const l = locations[i];
    const c = resolveCoords(l.country_code, l.city);
    if (!c) { toast("Координаты не найдены — введите вручную", "error"); return; }
    setLoc(i, { lng: c[0], lat: c[1] });
  };

  const submit = async () => {
    if (!name.trim()) { toast("Укажите название хостинга", "error"); return; }
    // Бэкенд отвечает 422 на выход из диапазона — ловим здесь, чтобы человек
    // увидел, КАКАЯ метрика виновата.
    for (const d of METRIC_DEFS) {
      const v = metrics[d.key];
      if (v != null && !(v >= 1 && v <= 100)) {
        toast(`«${d.label}»: оценка должна быть от 1.0 до 100.0`, "error"); return;
      }
    }
    // Drop fully-empty tariff/location rows.
    // `bandwidth` counts as content too — otherwise a tariff that only records a
    // channel width would be silently discarded on save.
    const cleanTariffs = tariffs.filter(
      t => t.name.trim() || t.specs.trim() || (t.bandwidth || "").trim()
        || (t.note || "").trim() || t.price > 0);
    const cleanLocs = locations.filter(l => l.country_code || l.city.trim());
    const cleanAsns = asns.filter(a => a.number > 0 || a.name.trim() || a.website.trim());
    const cleanNotes = noteFields.filter(n => n.topic.trim() || n.text.trim());
    const cleanBs = bsSubnets.filter(
      r => r.network.trim() || r.asn.trim() || r.org.trim() || r.checked_at.trim() || r.response.trim());
    // PUT заменяет запись целиком — новые поля обязаны уезжать всегда, иначе
    // сохранение из формы обнулило бы их.
    const body: HostingBody = {
      name: name.trim(), website: website.trim(), features: features.trim(), notes: notes.trim(),
      tags, media, metrics, tariffs: cleanTariffs, locations: cleanLocs, asns: cleanAsns,
      note_fields: cleanNotes, bs_subnets: cleanBs, has_api: hasApi,
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

      <ChipSet label="Есть API" icon={<Plug size={12} />}
        value={hasApi === null ? "unknown" : hasApi ? "yes" : "no"} options={API_OPTS}
        onChange={v => setHasApi(v === "unknown" ? null : v === "yes")} />

      {/* Заметки по темам */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="label flex items-center gap-1"><StickyNote size={12} /> Заметки</label>
          <button type="button" disabled={noteFields.length >= MAX_NOTE_FIELDS}
            onClick={() => setNoteFields(ns => [...ns, emptyNote()])}
            title={noteFields.length >= MAX_NOTE_FIELDS ? `Не больше ${MAX_NOTE_FIELDS} полей` : undefined}
            className="text-[11px] flex items-center gap-1 text-[var(--accent-hi)] disabled:opacity-40 disabled:cursor-not-allowed">
            <Plus size={11} /> Поле заметок
          </button>
        </div>
        {noteFields.length === 0 && (
          <p className="text-[11px] text-[var(--t-faint)]">Отдельные заметки по темам: оплата, поддержка, ограничения.</p>
        )}
        {noteFields.map((n, i) => (
          <div key={i} className="rounded-lg border border-[var(--line-soft)] p-2.5 flex flex-col gap-2 bg-[var(--bg2)]">
            <div className="flex items-center gap-2">
              <input value={n.topic} onChange={e => setNote(i, { topic: e.target.value })}
                placeholder="Тема (Оплата)" maxLength={80} spellCheck={false} className="input flex-1" />
              <button type="button" onClick={() => setNoteFields(ns => ns.filter((_, j) => j !== i))}
                className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><X size={13} /></button>
            </div>
            <textarea value={n.text} onChange={e => setNote(i, { text: e.target.value })}
              placeholder="Текст заметки" rows={3} className="input" />
          </div>
        ))}
      </div>

      {/* Метрики */}
      <div className="flex flex-col gap-2">
        <label className="label flex items-center gap-1"><Gauge size={12} /> Метрики</label>
        <p className="text-[11px] text-[var(--t-faint)]">Субъективные оценки от 1.0 до 100.0. Пустое поле — «не оценено».</p>
        {METRIC_DEFS.map(d => {
          const hidden = d.key === "fairuse" && !!metrics.fairuse_hidden;
          const v = metrics[d.key] ?? null;
          return (
            <div key={d.key} className="flex items-center gap-2">
              <span className="text-xs text-[var(--t-mid)] w-[136px] shrink-0">{d.label}</span>
              {hidden ? (
                <span className="flex-1 text-[11px] text-[var(--t-faint)]">Не применимо у этого провайдера</span>
              ) : (
                <>
                  <input type="number" min={1} max={100} step="0.1"
                    value={v ?? ""} placeholder="—" className="input w-24"
                    onChange={e => {
                      const n = parseFloat(e.target.value);
                      setMetric(d.key, Number.isFinite(n) ? n : null);
                    }} />
                  <span className="text-xs tabular-nums font-semibold w-10" style={{ color: metricColor(v) }}>
                    {v === null ? "—" : fmtScore(v)}
                  </span>
                </>
              )}
              {d.key === "fairuse" && (
                <label className="flex items-center gap-1.5 text-[11px] text-[var(--t-low)] cursor-pointer ml-auto">
                  <input type="checkbox" checked={hidden} style={{ accentColor: "var(--accent)" }}
                    onChange={e => setMetrics(p => ({ ...p, fairuse_hidden: e.target.checked }))} />
                  Не применимо
                </label>
              )}
            </div>
          );
        })}
      </div>

      <MediaDrop value={media} onChange={setMedia}
        hint="Скриншоты панели, прайс, схема сети. До 15 МБ на файл." />

      {/* БС подсети */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <label className="label flex items-center gap-1"><Network size={12} /> БС подсети</label>
          <button type="button"
            onClick={() => setBsSubnets(rs => [...rs, { network: "", asn: "", org: "", checked_at: "", response: "" }])}
            className="text-[11px] flex items-center gap-1 text-[var(--accent-hi)]"><Plus size={11} /> Строка</button>
        </div>
        {bsSubnets.length === 0 && (
          <p className="text-[11px] text-[var(--t-faint)]">Строк нет.</p>
        )}
        {bsSubnets.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 560 }}>
              <thead>
                <tr className="text-[10px] uppercase tracking-widest text-[var(--t-low)]">
                  <th className="text-left font-medium pb-1">Сеть</th>
                  <th className="text-left font-medium pb-1">ASN</th>
                  <th className="text-left font-medium pb-1">Организация</th>
                  <th className="text-left font-medium pb-1">Дата проверки</th>
                  <th className="text-left font-medium pb-1">Отклик</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {bsSubnets.map((r, i) => {
                  const set = (patch: Partial<BsSubnet>) =>
                    setBsSubnets(rs => rs.map((x, j) => (j === i ? { ...x, ...patch } : x)));
                  return (
                    <tr key={i}>
                      <td className="pr-1 pb-1"><input value={r.network} spellCheck={false} className="input"
                        placeholder="10.0.0.0/24" onChange={e => set({ network: e.target.value })} /></td>
                      <td className="pr-1 pb-1"><input value={r.asn} spellCheck={false} className="input"
                        placeholder="AS12345" onChange={e => set({ asn: e.target.value })} /></td>
                      <td className="pr-1 pb-1"><input value={r.org} spellCheck={false} className="input"
                        placeholder="Организация" onChange={e => set({ org: e.target.value })} /></td>
                      {/* type=date дал бы календарь, но выписки приносят и «~май 2026» — не сужаем. */}
                      <td className="pr-1 pb-1"><input value={r.checked_at} spellCheck={false} className="input"
                        placeholder="2026-07-01" onChange={e => set({ checked_at: e.target.value })} /></td>
                      <td className="pr-1 pb-1"><input value={r.response} spellCheck={false} className="input"
                        placeholder="отвечает, 20 ms" onChange={e => set({ response: e.target.value })} /></td>
                      <td className="pb-1">
                        <button type="button" onClick={() => setBsSubnets(rs => rs.filter((_, j) => j !== i))}
                          className="p-1 text-[var(--t-low)] hover:text-[var(--err)]"><X size={13} /></button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

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
            <textarea value={t.note ?? ""} onChange={e => setTariff(i, { note: e.target.value })}
              placeholder="Заметка о тарифе" rows={2} className="input" />
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
