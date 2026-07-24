import { useState, useEffect, useCallback, useMemo } from "react";
import { ComposableMap, Geographies, Geography, ZoomableGroup, Marker } from "react-simple-maps";
import { feature } from "topojson-client";
import worldTopo from "world-atlas/countries-110m.json";
import { motion, AnimatePresence } from "motion/react";
import { Map as MapIcon, Plus, Minus, Globe, Loader2, X, MapPin, Search } from "lucide-react";
import { hostingsApi, periodLabel, type Hosting } from "./api";
import {
  CONTINENTS, CONTINENT_VIEW, WORLD_VIEW, continentOf, resolveCoords, alpha2OfGeo,
  type ContinentKey,
} from "./geo";
import { matchHosting, matchedCountries } from "./search";
import { CountryPanel, hostingsInCountry } from "./CountryPanel";
import { FlagChip } from "../common/FlagChip";
import { Page, PageHeader, fmtNum } from "../infra/ui";
import { toast } from "../infra/Toast";

// Pre-convert the bundled topojson to a GeoJSON feature ARRAY ONCE (module
// scope) so we render per-country shapes deterministically (objects.countries),
// not the merged `land` blob. `Geographies` accepts a Topology or a features
// array — pass the array so it never depends on object-key ordering. Fully
// offline — the topology is bundled into the build.
const GEO = (feature(worldTopo as any, (worldTopo as any).objects.countries) as any).features;

interface MarkerPt {
  key: string; hosting: string; city: string; cc: string;
  continent: ContinentKey | null; coords: [number, number];
  price: number | null; currency: string; period: string; note: string;
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

export function HostingsMap() {
  const [markers, setMarkers] = useState<MarkerPt[]>([]);
  const [hostings, setHostings] = useState<Hosting[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [view, setView] = useState<{ center: [number, number]; zoom: number }>(WORLD_VIEW);
  const [sel, setSel] = useState<MarkerPt | null>(null);
  const [country, setCountry] = useState("");   // alpha-2 of the open country panel

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const hostings = await hostingsApi.list();
      setHostings(hostings);
      const pts: MarkerPt[] = [];
      for (const h of hostings) {
        const priced = (h.tariffs || []).filter(t => t.price > 0).sort((a, b) => a.price - b.price)[0];
        for (let i = 0; i < (h.locations || []).length; i++) {
          const l = h.locations[i];
          const coords: [number, number] | null =
            l.lng !== 0 || l.lat !== 0 ? [l.lng, l.lat] as [number, number] : resolveCoords(l.country_code, l.city);
          if (!coords) continue;
          pts.push({
            key: `${h.id}:${i}`, hosting: h.name, city: l.city || l.country_code, cc: l.country_code,
            continent: continentOf(l.country_code), coords,
            price: priced ? priced.price : null, currency: priced?.currency ?? "", period: priced?.period ?? "mo",
            note: l.note || "",
          });
        }
      }
      setMarkers(pts);
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  // Names of the hostings that match — markers are filtered by membership, so a
  // hosting matched on price or tariff still shows all of its points.
  const matching = useMemo(
    () => new Set(hostings.filter(h => matchHosting(h, query)).map(h => h.name)),
    [hostings, query],
  );
  const visible = useMemo(
    () => (query ? markers.filter(m => matching.has(m.hosting)) : markers),
    [markers, matching, query],
  );
  // Precomputed set, not a lookup inside each of the 177 shape renders.
  const highlight = useMemo(
    () => (query ? matchedCountries(hostings, query) : new Set<string>()),
    [hostings, query],
  );

  const focus = (k: ContinentKey) => setView(CONTINENT_VIEW[k]);
  const zoomBy = (f: number) => setView(v => ({ ...v, zoom: clamp(v.zoom * f, 1, 8) }));
  const reset = () => { setView(WORLD_VIEW); setSel(null); setCountry(""); };

  // Opening a country that has no hostings would show an empty box — clicking
  // the ocean or an unused country simply does nothing.
  const openCountry = (cc: string) => {
    if (cc && hostingsInCountry(hostings, cc).length) { setCountry(cc); setSel(null); }
  };

  return (
    <Page>
      <PageHeader icon={<MapIcon size={16} className="text-[var(--accent)]" />} title="Карта"
        subtitle={`Локации хостингов на офлайн-карте · ${visible.length} из ${markers.length}`}
        actions={
          <button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] text-[var(--t-mid)]" title="Обновить">
            <Loader2 size={13} className={loading ? "animate-spin" : ""} />
          </button>
        } />

      {/* Search (replaced the continent toggles) + region zoom presets */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--t-faint)]" />
          <input value={query} onChange={e => setQuery(e.target.value)}
            className="input" style={{ paddingLeft: 28 }} spellCheck={false}
            placeholder="Страна, город, хостинг, тариф, канал или цена (<20, 5-30)" />
          {query && (
            <button onClick={() => setQuery("")} title="Очистить"
              className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--t-low)] hover:text-[var(--t-hi)]">
              <X size={13} />
            </button>
          )}
        </div>
        <select className="selectbox" style={{ width: "auto" }} value=""
          onChange={e => { const k = e.target.value as ContinentKey; if (k) focus(k); }}>
          <option value="">Приблизить к региону…</option>
          {CONTINENTS.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      </div>

      {/* Map */}
      <div className="relative rounded-xl overflow-hidden border border-[var(--line)]"
        style={{ height: "min(70vh, 560px)", background: "var(--bg1)" }}>
        <ComposableMap projection="geoEqualEarth" projectionConfig={{ scale: 155 }}
          width={980} height={520} style={{ width: "100%", height: "100%" }}>
          <ZoomableGroup center={view.center} zoom={view.zoom} minZoom={1} maxZoom={8}
            onMoveEnd={(p: any) => setView({ center: p.coordinates, zoom: p.zoom })}>
            <Geographies geography={GEO}>
              {({ geographies }: any) => geographies.map((geo: any) => {
                const cc = alpha2OfGeo(geo);
                const on = cc && (cc === country || highlight.has(cc));
                return (
                  <Geography key={geo.rsmKey} geography={geo}
                    onClick={() => openCountry(cc)}
                    fill={on ? "var(--accent-dim)" : "var(--bg3)"}
                    stroke={on ? "var(--accent)" : "var(--line)"}
                    strokeWidth={0.6}
                    // Keep borders crisp: without this the stroke is scaled by
                    // ZoomableGroup, so it is invisible at world view and thick
                    // when zoomed in.
                    vectorEffect="non-scaling-stroke"
                    style={{
                      default: { outline: "none" },
                      hover: { fill: "var(--accent-dim)", outline: "none" },
                      pressed: { outline: "none" },
                    }} />
                );
              })}
            </Geographies>

            {visible.map(m => {
              const active = sel?.key === m.key;
              return (
                <Marker key={m.key} coordinates={m.coords} onClick={() => setSel(m)}>
                  <motion.g initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: "spring", stiffness: 260, damping: 18 }}
                    whileHover={{ scale: 1.6 }} style={{ cursor: "pointer" }}>
                    {active && (
                      <motion.circle r={5} fill="none" stroke="var(--accent)" strokeWidth={1.2}
                        animate={{ r: [5, 13], opacity: [0.6, 0] }}
                        transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }} />
                    )}
                    <circle r={active ? 5.5 : 4} fill="var(--accent)"
                      stroke="var(--accent-ink)" strokeWidth={1} />
                  </motion.g>
                </Marker>
              );
            })}
          </ZoomableGroup>
        </ComposableMap>

        {/* Zoom controls */}
        <div className="absolute top-3 right-3 flex flex-col gap-1">
          <button onClick={() => zoomBy(1.5)} className="w-8 h-8 grid place-items-center rounded-md bg-[var(--bg2)] border border-[var(--line)] text-[var(--t-mid)] hover:text-[var(--t-hi)]"><Plus size={14} /></button>
          <button onClick={() => zoomBy(1 / 1.5)} className="w-8 h-8 grid place-items-center rounded-md bg-[var(--bg2)] border border-[var(--line)] text-[var(--t-mid)] hover:text-[var(--t-hi)]"><Minus size={14} /></button>
          <button onClick={reset} title="Весь мир" className="w-8 h-8 grid place-items-center rounded-md bg-[var(--bg2)] border border-[var(--line)] text-[var(--t-mid)] hover:text-[var(--t-hi)]"><Globe size={14} /></button>
        </div>

        {/* Empty hint */}
        {!loading && markers.length === 0 && (
          <div className="absolute inset-0 grid place-items-center pointer-events-none">
            <div className="text-center text-[var(--t-faint)] text-sm px-4">
              <MapPin size={26} className="mx-auto mb-2 opacity-40" />
              Нет локаций. Добавьте хостинг с локациями в разделе «Хостинги».
            </div>
          </div>
        )}

        {/* Selected-marker popover */}
        <AnimatePresence>
          {sel && (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}
              className="absolute bottom-3 left-3 w-64 rounded-lg border border-[var(--line)] bg-[var(--bg1)] p-3 shadow-xl"
              style={{ boxShadow: "var(--shadow-pop)" }}>
              <div className="flex items-start justify-between gap-2">
                <span className="text-sm font-medium text-[var(--t-hi)]">{sel.hosting}</span>
                <button onClick={() => setSel(null)} className="text-[var(--t-low)] hover:text-[var(--t-hi)]"><X size={13} /></button>
              </div>
              <p className="text-xs text-[var(--t-mid)] flex items-center gap-1.5 mt-1">
                <FlagChip code={sel.cc} size={15} />
                {sel.city}
              </p>
              {sel.price !== null && (
                <p className="text-xs text-[var(--t-low)] mt-1">от <span className="text-[var(--t-hi)] tabular-nums">{fmtNum(sel.price, sel.currency)}</span>{periodLabel(sel.period)}</p>
              )}
              {sel.note && <p className="text-[11px] text-[var(--t-faint)] mt-1.5">{sel.note}</p>}
              {/* Also the only way into the country panel for Singapore and Hong
                  Kong — the 110m geometry has no polygon for either. */}
              {sel.cc && hostingsInCountry(hostings, sel.cc).length > 0 && (
                <button onClick={() => openCountry(sel.cc)}
                  className="mt-2 text-[11px] text-[var(--accent-hi)] hover:underline">
                  Все хостинги страны →
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {country && (
          <CountryPanel cc={country} hostings={hostings} onClose={() => setCountry("")} />
        )}
      </div>
    </Page>
  );
}
