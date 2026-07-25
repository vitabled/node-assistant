// Wave-8 §7 — «Анализ подписки». Enter a subscription URL, a domain or an IP;
// the backend fetches (VPN-client UA, SSRF-guarded) and resolves each target IP
// to actual geo + ASN + registry geo. «Добавить в хостинги» creates one hosting
// card per ASN. Share-link secrets never reach the browser — only the analysis.
import { useState } from "react";
import { ScanSearch, Loader2, ExternalLink, Plus, AlertTriangle } from "lucide-react";
import { FlagChip } from "./common/FlagChip";
import { toast } from "./infra/Toast";

interface Asn { number: number; name: string; website: string }
interface Row {
  host: string;
  ip: string;
  asn: Asn;
  geo_actual: { cc: string; city: string };
  geo_registry: { cc: string };
}

export function SubscriptionAnalyze() {
  const [input, setInput] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);

  const analyze = async () => {
    const v = input.trim();
    if (!v) return;
    setLoading(true); setRows(null);
    try {
      const res = await fetch("/api/subscription-analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: v }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка анализа");
      setRows(data.results || []);
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  };

  const addToHostings = async () => {
    if (!rows?.length) return;
    setAdding(true);
    try {
      const res = await fetch("/api/subscription-analyze/to-hostings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ results: rows }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка добавления");
      toast(`Добавлено: ${data.created}, обновлено: ${data.updated}`, "success");
    } catch (e) { toast((e as Error).message, "error"); }
    setAdding(false);
  };

  const asnCount = rows ? new Set(rows.filter(r => r.asn.number).map(r => r.asn.number)).size : 0;

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6">
        <div className="mb-6">
          <h1 className="h1">Анализ подписки</h1>
          <p className="sub">URL подписки, домен или IP → фактическое и реестровое гео + ASN серверов</p>
        </div>

        <div className="flex items-center gap-2 mb-5">
          <input value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") analyze(); }}
            placeholder="https://sub.example.com/… · example.com · 1.2.3.4"
            spellCheck={false} autoComplete="off" className="input flex-1" />
          <button onClick={analyze} disabled={loading || !input.trim()} className="btn btn-primary">
            {loading ? <><Loader2 size={13} className="spin" /> Анализ…</> : <><ScanSearch size={14} /> Проанализировать</>}
          </button>
        </div>

        {rows && rows.length === 0 && (
          <div className="card p-8 text-center text-[var(--t-faint)] text-sm">
            Серверы не найдены. Проверьте, что URL отдаёт подписку, а домен/IP резолвится.
          </div>
        )}

        {rows && rows.length > 0 && (
          <>
            <div className="flex items-center justify-between mb-3">
              <p className="text-xs text-[var(--t-low)]">{rows.length} серверов · {asnCount} ASN</p>
              <button onClick={addToHostings} disabled={adding || asnCount === 0} className="btn btn-primary">
                {adding ? <><Loader2 size={13} className="spin" /> Добавление…</> : <><Plus size={14} /> Добавить в хостинги</>}
              </button>
            </div>
            <div className="overflow-x-auto">
              <table className="tbl text-xs w-full">
                <thead>
                  <tr><th>Хост</th><th>IP</th><th>ASN</th><th>Факт. гео</th><th>Реестр</th></tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => {
                    const mismatch = !!(r.geo_actual.cc && r.geo_registry.cc && r.geo_actual.cc !== r.geo_registry.cc);
                    return (
                      <tr key={i}>
                        <td className="text-[var(--t-mid)] trunc" style={{ maxWidth: 160 }} title={r.host}>{r.host}</td>
                        <td className="tabular-nums text-[var(--t-low)]">{r.ip}</td>
                        <td className="text-[var(--t-mid)]">
                          {r.asn.number ? (
                            <span className="flex items-center gap-1 flex-wrap">
                              <span className="text-[var(--t-hi)]">AS{r.asn.number}</span>
                              {r.asn.name && <span>{r.asn.name}</span>}
                              {r.asn.website && (
                                <a href={r.asn.website} target="_blank" rel="noopener noreferrer"
                                  className="text-[var(--accent-hi)]" title={r.asn.website}><ExternalLink size={11} /></a>
                              )}
                            </span>
                          ) : "—"}
                        </td>
                        <td>
                          <span className="flex items-center gap-1.5">
                            <FlagChip code={r.geo_actual.cc} size={14} /> {r.geo_actual.city || r.geo_actual.cc || "—"}
                          </span>
                        </td>
                        <td>
                          <span className="flex items-center gap-1.5">
                            <FlagChip code={r.geo_registry.cc} size={14} /> {r.geo_registry.cc || "—"}
                            {mismatch && <AlertTriangle size={12} style={{ color: "var(--warn)" }} aria-label="Расхождение факт./реестр" />}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
