// Wave-8 §7 — «Анализ подписки». Enter a subscription URL, a domain or an IP;
// the backend fetches (VPN-client UA, SSRF-guarded) and resolves each target IP
// to actual geo + ASN + registry geo. «Добавить в хостинги» creates one hosting
// card per ASN. Share-link secrets never reach the browser — only the analysis.
import { useState, type MouseEvent as ReactMouseEvent } from "react";
import { ScanSearch, Loader2, ExternalLink, Plus, AlertTriangle, X } from "lucide-react";
import { FlagChip } from "./common/FlagChip";
import { toast } from "./infra/Toast";

interface Asn { number: number; name: string; website: string; website_source: string }
interface Net { org: string; isp: string; ptr: string; hosting: boolean; proxy: boolean }
interface Egress { ip: string; cc: string; city: string; org: string; isp: string; as: string; hosting: boolean; proxy: boolean }
interface Row {
  host: string;
  hosts: string[];
  names: string[];
  ip: string;
  asn: Asn;
  geo_actual: { cc: string; city: string };
  geo_registry: { cc: string };
  net: Net;
}

// User-Agent пресеты (Wave-4): панель отдаёт РАЗНЫЕ форматы под разные UA.
// "auto" — серверная цепочка (текущее поведение).
const UA_PRESETS: { v: string; l: string }[] = [
  { v: "",                    l: "UA: Авто (цепочка)" },
  { v: "v2rayNG/1.9.39",      l: "v2rayNG" },
  { v: "Streisand/1.6.0",     l: "Streisand" },
  { v: "sing-box/1.11.4",     l: "sing-box" },
  { v: "mihomo/v1.18.7",      l: "Mihomo / Clash" },
  { v: "Shadowrocket/2.2.9",  l: "Shadowrocket" },
  { v: "Happ/1.16.0",         l: "Happ" },
];

export function SubscriptionAnalyze() {
  const [input, setInput] = useState("");
  const [ua, setUa] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  // Выходной IP по строкам (Wave-4 PR-8): проверка через xray-туннель на backend'е.
  const [egress, setEgress] = useState<Record<number, { loading?: boolean; data?: Egress; error?: string }>>({});
  const [lastQuery, setLastQuery] = useState<{ input: string; ua: string } | null>(null);

  // Resizable columns: table-layout:fixed + <colgroup>; drag a header's right
  // edge to resize. Last column (delete ✕) is fixed and not resizable.
  const COLS = ["Название", "Хост", "IP (вход)", "IP (выход)", "Сеть", "ASN", "Факт. гео", "Реестр", "Website"];
  const [widths, setWidths] = useState<number[]>([150, 130, 105, 120, 130, 150, 115, 70, 140]);
  const DEL_W = 40;
  const startResize = (i: number, e: ReactMouseEvent) => {
    e.preventDefault();
    const startX = e.clientX, startW = widths[i];
    const move = (ev: MouseEvent) =>
      setWidths(w => w.map((x, j) => (j === i ? Math.max(48, startW + ev.clientX - startX) : x)));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  const analyze = async () => {
    const v = input.trim();
    if (!v) return;
    setLoading(true); setRows(null); setEgress({});
    try {
      const res = await fetch("/api/subscription-analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: v, user_agent: ua }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка анализа");
      setRows(data.results || []);
      setLastQuery({ input: v, ua });
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  };

  const checkEgress = async (i: number, host: string) => {
    if (!lastQuery) return;
    setEgress(e => ({ ...e, [i]: { loading: true } }));
    try {
      const res = await fetch("/api/subscription-analyze/egress", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: lastQuery.input, user_agent: lastQuery.ua, host }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`);
      setEgress(e => ({ ...e, [i]: { data: d.egress as Egress } }));
    } catch (err) {
      setEgress(e => ({ ...e, [i]: { error: (err as Error).message } }));
    }
  };

  const checkAllEgress = async () => {
    if (!rows) return;
    for (let i = 0; i < rows.length; i++) {
      if (!egress[i]?.data && !egress[i]?.loading) {
        await checkEgress(i, rows[i].host);
      }
    }
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
      <div className="max-w-6xl mx-auto px-6 py-6">
        <div className="mb-6">
          <h1 className="h1">Анализ подписки</h1>
          <p className="sub">URL подписки, домен или IP → фактическое и реестровое гео + ASN серверов</p>
        </div>

        <div className="flex flex-wrap items-center gap-2 mb-5">
          <input value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") analyze(); }}
            placeholder="https://sub.example.com/… · example.com · 1.2.3.4"
            spellCheck={false} autoComplete="off" className="input flex-1" />
          <select value={ua} onChange={e => setUa(e.target.value)} className="selectbox"
            style={{ width: 170, flex: "none" }}
            title="User-Agent для загрузки подписки — панели отдают разные форматы под разные UA">
            {UA_PRESETS.map(p => <option key={p.l} value={p.v}>{p.l}</option>)}
          </select>
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
              <div className="flex items-center gap-2">
                <button onClick={checkAllEgress} className="btn btn-soft"
                  disabled={!lastQuery || rows.every((_, i) => egress[i]?.data)}>
                  {Object.values(egress).some(e => e.loading)
                    ? <><Loader2 size={13} className="spin" /> Проверка…</>
                    : "Проверить все выходы"}
                </button>
                <button onClick={addToHostings} disabled={adding || asnCount === 0} className="btn btn-primary">
                  {adding ? <><Loader2 size={13} className="spin" /> Добавление…</> : <><Plus size={14} /> Добавить в хостинги</>}
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="tbl text-xs colborders" style={{ tableLayout: "fixed", width: widths.reduce((a, b) => a + b, 0) + DEL_W }}>
                <colgroup>
                  {widths.map((w, i) => <col key={i} style={{ width: w }} />)}
                  <col style={{ width: DEL_W }} />
                </colgroup>
                <thead>
                  <tr>
                    {COLS.map((label, i) => (
                      <th key={i} style={{ position: "relative" }}>
                        <span className="trunc" style={{ display: "block", paddingRight: 6 }}>{label}</span>
                        <span onMouseDown={e => startResize(i, e)} title="Потянуть, чтобы изменить ширину"
                          style={{ position: "absolute", right: 0, top: 0, height: "100%", width: 7, cursor: "col-resize", userSelect: "none" }} />
                      </th>
                    ))}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => {
                    const mismatch = !!(r.geo_actual.cc && r.geo_registry.cc && r.geo_actual.cc !== r.geo_registry.cc);
                    const names = (r.names || []).join(", ");
                    const site = r.asn.website;
                    return (
                      <tr key={r.ip + i}>
                        <td className="text-[var(--t-hi)] trunc" title={names}>{names || "—"}</td>
                        <td className="text-[var(--t-mid)] trunc" title={(r.hosts || [r.host]).join(", ")}>{r.host}</td>
                        <td className="tabular-nums text-[var(--t-low)] trunc"
                          title={r.net?.ptr ? `PTR: ${r.net.ptr}` : undefined}>{r.ip}</td>
                        <td className="trunc">
                          {(() => {
                            const eg = egress[i];
                            if (eg?.loading) return <Loader2 size={12} className="spin" style={{ color: "var(--t-faint)" }} />;
                            if (eg?.data) {
                              const g = eg.data;
                              return (
                                <span className="flex items-center gap-1.5"
                                  title={[g.org || g.isp, g.as, g.city && `${g.city}, ${g.cc}`].filter(Boolean).join("\n")}>
                                  <FlagChip code={g.cc} size={14} />
                                  <span className="tabular-nums text-[var(--t-hi)] trunc">{g.ip}</span>
                                  {g.ip !== r.ip && (
                                    <span className="tag" title="Выход отличается от входа — релей">relay</span>
                                  )}
                                </span>
                              );
                            }
                            if (eg?.error) return <span className="text-[var(--warn)] trunc" title={eg.error}>ошибка</span>;
                            return (
                              <button type="button" className="btn btn-soft" style={{ padding: "2px 8px", fontSize: 11 }}
                                disabled={!lastQuery}
                                title="Проверить выходной IP через туннель этой ноды"
                                onClick={() => checkEgress(i, r.host)}>
                                Выход
                              </button>
                            );
                          })()}
                        </td>
                        <td className="trunc" title={[r.net?.org, r.net?.isp, r.net?.ptr && `PTR: ${r.net.ptr}`].filter(Boolean).join("\n")}>
                          <span className="flex items-center gap-1.5">
                            <span className="trunc text-[var(--t-mid)]">{r.net?.org || r.net?.isp || "—"}</span>
                            {r.net?.hosting && <span className="tag" title="Дата-центр (hosting)">DC</span>}
                            {r.net?.proxy && <span className="tag" title="Прокси/VPN выход">proxy</span>}
                          </span>
                        </td>
                        <td className="text-[var(--t-mid)] trunc" title={r.asn.name}>
                          {r.asn.number
                            ? <><span className="text-[var(--t-hi)]">AS{r.asn.number}</span>{r.asn.name ? ` ${r.asn.name}` : ""}</>
                            : "—"}
                        </td>
                        <td className="trunc">
                          <span className="flex items-center gap-1.5">
                            <FlagChip code={r.geo_actual.cc} size={14} /> {r.geo_actual.city || r.geo_actual.cc || "—"}
                          </span>
                        </td>
                        <td className="trunc">
                          <span className="flex items-center gap-1.5">
                            <FlagChip code={r.geo_registry.cc} size={14} /> {r.geo_registry.cc || "—"}
                            {mismatch && <AlertTriangle size={12} style={{ color: "var(--warn)" }} aria-label="Расхождение факт./реестр" />}
                          </span>
                        </td>
                        <td className="trunc">
                          {site
                            ? <a href={site} target="_blank" rel="noopener noreferrer"
                                title={`${site}\nИсточник: ${r.asn.website_source || "?"}`}
                                className="text-[var(--accent-hi)] inline-flex items-center gap-1">
                                <ExternalLink size={11} className="shrink-0" />
                                <span className="trunc">{site.replace(/^https?:\/\//, "").replace(/\/$/, "")}</span>
                              </a>
                            : <span className="text-[var(--t-faint)]" title="Сайт ASN не найден ни в RDAP, ни в PeeringDB">—</span>}
                        </td>
                        <td>
                          <button onClick={() => setRows(rs => (rs || []).filter((_, j) => j !== i))}
                            title="Убрать из выдачи" className="p-1 text-[var(--t-low)] hover:text-[var(--err)]">
                            <X size={13} />
                          </button>
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
