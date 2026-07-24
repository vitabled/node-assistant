import { useEffect, useState } from "react";
import { Loader2, X, Download } from "lucide-react";
import { FlagChip } from "./common/FlagChip";

// Wave-7 Plan B Ф3 — import nodes from a subscription into «Доступность серверов».
// Always previews first (`dry_run`), so nothing is written before the operator
// sees what a subscription actually contains.

interface Candidate {
  host: string; port: number; name: string; country: string;
  ip: string; status: "new" | "duplicate" | "unresolved";
}
interface Sub { id: string; url: string }

const STATUS_LABEL: Record<Candidate["status"], string> = {
  new: "новый",
  duplicate: "уже отслеживается",
  unresolved: "домен не разрешается",
};
const STATUS_COLOR: Record<Candidate["status"], string> = {
  new: "var(--ok)", duplicate: "var(--t-faint)", unresolved: "var(--warn)",
};

export function ImportFromSubscription({ onClose, onImported }: {
  onClose: () => void; onImported: () => void;
}) {
  const [subs, setSubs] = useState<Sub[]>([]);
  const [subId, setSubId] = useState("");
  const [url, setUrl] = useState("");
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetch("/api/subscriptions").then(r => (r.ok ? r.json() : []))
      .then(d => {
        const list: Sub[] = Array.isArray(d) ? d : (d?.subscriptions ?? []);
        setSubs(list);
        if (list.length) setSubId(list[0].id);
      })
      .catch(() => {});
  }, []);

  const key = (c: Candidate) => `${c.host}:${c.port}`;

  const call = async (dry: boolean) => {
    setBusy(true); setErr("");
    try {
      const res = await fetch("/api/server-monitor/import/subscription", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(url.trim() ? { url: url.trim(), dry_run: dry }
                                        : { subscription_id: subId, dry_run: dry }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof data.detail === "string" ? data.detail : "Ошибка импорта"); return null; }
      return data;
    } catch { setErr("Сеть недоступна"); return null; }
    finally { setBusy(false); }
  };

  const preview = async () => {
    const d = await call(true);
    if (!d) return;
    const cands: Candidate[] = d.candidates ?? [];
    setRows(cands);
    setPicked(new Set(cands.filter(c => c.status === "new").map(key)));
  };

  const doImport = async () => {
    const d = await call(false);
    if (!d) return;
    onImported();
    onClose();
  };

  const selectable = (rows ?? []).filter(c => c.status === "new");
  const count = selectable.filter(c => picked.has(key(c))).length;

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" style={{ maxWidth: 620 }}>
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--line-soft)" }}>
          <Download size={15} style={{ color: "var(--accent)" }} />
          <span className="text-sm font-semibold text-[var(--t-hi)] flex-1">Импорт серверов из подписки</span>
          <button className="iconbtn" onClick={onClose}><X size={15} /></button>
        </div>

        <div className="p-4 flex flex-col gap-3" style={{ maxHeight: "70vh", overflowY: "auto" }}>
          {subs.length > 0 && (
            <label className="flex flex-col gap-1">
              <span className="micro">Подписка</span>
              <select className="selectbox" value={subId} disabled={!!url.trim()}
                onChange={e => setSubId(e.target.value)}>
                {subs.map(s => <option key={s.id} value={s.id}>{s.url}</option>)}
              </select>
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="micro">{subs.length ? "или разовый URL" : "URL подписки"}</span>
            <input className="input" value={url} onChange={e => setUrl(e.target.value)}
              placeholder="https://…" spellCheck={false} autoComplete="off" />
          </label>

          {err && <p className="errmsg">{err}</p>}

          {rows && rows.length === 0 && (
            <p className="hint">В подписке не нашлось ни одной поддерживаемой ссылки.</p>
          )}

          {rows && rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="tbl text-xs w-full">
                <thead>
                  <tr>
                    <th style={{ width: 28 }} />
                    <th>Узел</th><th>Домен → адрес</th><th>Порт</th><th>Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(c => {
                    const k = key(c);
                    const on = picked.has(k);
                    const can = c.status === "new";
                    return (
                      <tr key={k} style={{ opacity: can ? 1 : 0.6 }}>
                        <td>
                          <input type="checkbox" checked={on && can} disabled={!can}
                            aria-label={c.name}
                            onChange={() => setPicked(p => {
                              const n = new Set(p);
                              n.has(k) ? n.delete(k) : n.add(k);
                              return n;
                            })} />
                        </td>
                        <td className="text-[var(--t-hi)]">
                          <span className="flex items-center gap-1.5">
                            <FlagChip code={c.country} size={14} />
                            <span className="trunc">{c.name}</span>
                          </span>
                        </td>
                        <td className="text-[var(--t-low)]">{c.host}{c.ip ? ` → ${c.ip}` : ""}</td>
                        <td className="tabular-nums">{c.port}</td>
                        <td style={{ color: STATUS_COLOR[c.status] }}>{STATUS_LABEL[c.status]}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="px-4 py-3 flex justify-end gap-2" style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button className="btn btn-soft" onClick={onClose}>Отмена</button>
          <button className="btn btn-soft" onClick={preview} disabled={busy || (!url.trim() && !subId)}>
            {busy && !rows ? <Loader2 size={13} className="spin" /> : null} Показать
          </button>
          <button className="btn btn-primary" onClick={doImport}
            disabled={busy || !rows || count === 0}>
            Импортировать{count > 0 ? ` (${count})` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
