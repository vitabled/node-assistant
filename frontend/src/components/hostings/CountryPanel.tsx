import { X, Globe } from "lucide-react";
import { FlagChip } from "../common/FlagChip";
import { fmtNum } from "../infra/ui";
import { periodLabel, type Hosting } from "./api";
import { COUNTRIES } from "../CountrySelect";

/** Hostings that have at least one location in `cc`, with only those locations. */
export function hostingsInCountry(hostings: Hosting[], cc: string) {
  const up = (cc || "").toUpperCase();
  if (!up) return [];
  return hostings
    .map(h => ({
      hosting: h,
      locations: (h.locations || []).filter(l => (l.country_code || "").toUpperCase() === up),
    }))
    .filter(x => x.locations.length > 0);
}

export function countryName(cc: string): string {
  return COUNTRIES.find(c => c.code === (cc || "").toUpperCase())?.name || cc.toUpperCase();
}

/**
 * Side panel listing every hosting present in one country: name, cities (with
 * flag), channel width, price and tariff — the fields the operator asked for.
 *
 * A side panel rather than a modal so the map stays visible and neighbouring
 * countries can be clicked one after another.
 */
export function CountryPanel({ cc, hostings, onClose }: {
  cc: string; hostings: Hosting[]; onClose: () => void;
}) {
  const rows = hostingsInCountry(hostings, cc);
  // An empty country never opens the panel (the caller checks), so this is only
  // a guard against a race with a reload.
  if (rows.length === 0) return null;

  return (
    <div className="absolute top-3 left-3 bottom-3 w-80 max-w-[calc(100%-1.5rem)] rounded-lg border
                    border-[var(--line)] bg-[var(--bg1)] flex flex-col overflow-hidden"
      style={{ boxShadow: "var(--shadow-pop)" }}>
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-[var(--line-soft)]">
        <FlagChip code={cc} size={18} />
        <span className="text-sm font-medium text-[var(--t-hi)] flex-1 truncate">{countryName(cc)}</span>
        <span className="text-[11px] text-[var(--t-faint)]">{rows.length}</span>
        <button onClick={onClose} className="text-[var(--t-low)] hover:text-[var(--t-hi)]"><X size={13} /></button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
        {rows.map(({ hosting: h, locations }) => (
          <div key={h.id} className="rounded-md border border-[var(--line-soft)] bg-[var(--bg2)] p-2.5">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-medium text-[var(--t-hi)] truncate">{h.name}</span>
              {h.website && (
                <a href={h.website} target="_blank" rel="noreferrer"
                  className="text-[var(--t-low)] hover:text-[var(--accent-hi)]" title={h.website}>
                  <Globe size={11} />
                </a>
              )}
            </div>

            <div className="mt-1 flex flex-col gap-0.5">
              {locations.map((l, i) => (
                <p key={i} className="text-xs text-[var(--t-mid)] flex items-center gap-1.5">
                  <FlagChip code={l.country_code} size={13} />
                  {l.city || countryName(l.country_code)}
                  {l.note && <span className="text-[10px] text-[var(--t-faint)]">· {l.note}</span>}
                </p>
              ))}
            </div>

            {(h.tariffs || []).length > 0 && (
              <table className="mt-1.5 w-full text-[11px]">
                <tbody>
                  {h.tariffs.map((t, i) => (
                    <tr key={i} className="align-top">
                      <td className="py-0.5 pr-2 text-[var(--t-mid)]">{t.name || "—"}</td>
                      <td className="py-0.5 pr-2 text-[var(--t-low)]">{t.bandwidth || "—"}</td>
                      <td className="py-0.5 text-right tabular-nums text-[var(--t-hi)] whitespace-nowrap">
                        {t.price > 0 ? <>{fmtNum(t.price, t.currency)}{periodLabel(t.period)}</> : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
