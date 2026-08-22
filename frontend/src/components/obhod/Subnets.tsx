import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity, Check, FolderKanban, GripVertical, Loader2, Pencil, Plus, Table2, Trash2, X,
} from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";
import { toast } from "../infra/Toast";

/**
 * «Подсети» (Обходы БС, Wave-5 PR-5): справочник подсетей/IP.
 * Дерево: Провайдер → Списки. Список — таблица (подсеть, версия IP, ASN,
 * название ASN, дата, Операторы + пользовательские столбцы). Кнопка
 * «Редактировать таблицу» включает режим правки: операции со столбцами
 * (добавить/переименовать/удалить/перетащить) и чекбоксы иконок операторов.
 * ASN дозаполняется автоматически через backend (ip-api).
 *
 * Latency Lab: кнопка «Скан Latency» (видна, если интеграция включена в
 * настройках) открывает панель — выбор строк/оператора, асинхронный job с
 * поллингом статуса и выводом результата.
 */

interface Col { key: string; title: string }
interface Op { key: string; label: string }
interface Row { id: string; values: Record<string, string>; operators: Record<string, boolean> }
interface Lst { id: string; name: string; columns: Col[]; rows: Row[] }
interface Prov { id: string; name: string; lists: Lst[] }

interface LatOp { id: string; label: string; online: boolean; configured: boolean }
interface ScanItem {
  row_id?: string; subnet?: string; operator?: string;
  alive_count?: number; available?: boolean; status_text?: string; reachable_ips?: string[];
}
interface ScanResult extends ScanItem { rows?: ScanItem[] }
type ScanStatus = "pending" | "done" | "cancelled" | "error";

const api = (path: string, init?: RequestInit) =>
  fetch(`/api/subnets${path}`, init ? { headers: { "Content-Type": "application/json" }, ...init } : init);

export function Subnets() {
  const [providers, setProviders] = useState<Prov[]>([]);
  const [operators, setOperators] = useState<Op[]>([]);
  const [sel, setSel] = useState<{ pid: string; lid: string } | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [newSubnet, setNewSubnet] = useState("");
  const [busy, setBusy] = useState(false);
  const dragCol = useRef<string | null>(null);

  // ── Latency Lab ──
  const [latEnabled, setLatEnabled] = useState(false);
  const [latOps, setLatOps] = useState<LatOp[]>([]);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanOp, setScanOp] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [reqId, setReqId] = useState<string | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanStatus | null>(null);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [scanBusy, setScanBusy] = useState(false);

  const load = useCallback(() => {
    api("").then(r => r.json()).then(d => {
      setProviders(d.providers || []);
      setOperators(d.operators || []);
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  // Интеграция может быть выключена — тогда UI скана не показываем вовсе.
  useEffect(() => {
    fetch("/api/latency/config").then(r => r.json()).then(d => {
      setLatEnabled(!!d?.enabled);
      if (d?.default_operator) setScanOp(String(d.default_operator));
    }).catch(() => {});
  }, []);

  const current: Lst | null = sel
    ? providers.find(p => p.id === sel.pid)?.lists.find(l => l.id === sel.lid) ?? null
    : null;

  const mutate = async (path: string, method: string, body?: unknown, msg?: string) => {
    const res = await api(path, { method, body: body !== undefined ? JSON.stringify(body) : undefined });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
      return false;
    }
    if (msg) toast(msg, "success");
    load();
    return true;
  };

  const addProvider = () => {
    const name = window.prompt("Название провайдера", "Новый провайдер");
    if (name) void mutate("/providers", "POST", { name });
  };
  const addList = (pid: string) => {
    const name = window.prompt("Название списка", "Новый список");
    if (name) void mutate(`/providers/${pid}/lists`, "POST", { name });
  };
  const rename = (kind: "providers" | "lists", pid: string, lid: string | null, current: string) => {
    const name = window.prompt("Новое название", current);
    if (!name || name === current) return;
    void mutate(lid ? `/providers/${pid}/lists/${lid}` : `/providers/${pid}`, "PATCH", { name });
  };
  const remove = (kind: "providers" | "lists", pid: string, lid: string | null, name: string) => {
    if (!window.confirm(`Удалить «${name}»${lid ? "" : " со всеми списками"}?`)) return;
    void mutate(lid ? `/providers/${pid}/lists/${lid}` : `/providers/${pid}`, "DELETE")
      .then(ok => { if (ok && lid && sel?.lid === lid) setSel(null); });
  };

  const addRows = async () => {
    const subnets = newSubnet.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
    if (!subnets.length || !sel) return;
    setBusy(true);
    try {
      const res = await api(`/providers/${sel.pid}/lists/${sel.lid}/rows`, {
        method: "POST", body: JSON.stringify({ subnets }),
      });
      const d = await res.json();
      if (!res.ok) { toast(typeof d.detail === "string" ? d.detail : "Ошибка", "error"); return; }
      if (d.errors?.length) toast(`Пропущено: ${d.errors.join("; ")}`, "error");
      setNewSubnet("");
      load();
      // автообогащение ASN свежими строками
      const after = await api("").then(r => r.json());
      const rows: Row[] = after.providers
        ?.find((p: Prov) => p.id === sel.pid)?.lists
        ?.find((l: Lst) => l.id === sel.lid)?.rows ?? [];
      const missing = rows.filter(r => d.added?.includes(r.values?.subnet) && !r.values?.asn);
      if (missing.length) {
        await api(`/providers/${sel.pid}/lists/${sel.lid}/enrich`, {
          method: "POST", body: JSON.stringify({ row_ids: missing.map(r => r.id) }),
        });
        load();
      }
    } finally { setBusy(false); }
  };

  // ── Latency-скан ──────────────────────────────────────────────
  // Панель открывается по кнопке; список операторов тянем лениво, чтобы не
  // дёргать внешний сервис на каждом заходе в раздел.
  const openScan = async () => {
    setScanOpen(true);
    setScanResult(null);
    setScanStatus(null);
    try {
      const d = await fetch("/api/latency/operators").then(r => r.json());
      setLatOps(Array.isArray(d?.operators) ? d.operators : []);
    } catch { setLatOps([]); }
  };

  const closeScan = () => {
    setScanOpen(false);
    setReqId(null);
    setScanStatus(null);
    setScanResult(null);
    setPicked([]);
  };

  const togglePick = (id: string) =>
    setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]));
  const toggleAll = () =>
    setPicked(p => (current && p.length === current.rows.length ? [] : (current?.rows ?? []).map(r => r.id)));

  // Поллинг job'а: единственный источник статуса — GET по req_id.
  useEffect(() => {
    if (!reqId || scanStatus !== "pending") return;
    let stop = false;
    const tick = async () => {
      try {
        const res = await api(`/latency-scan/${reqId}`);
        const d = await res.json().catch(() => ({}));
        if (stop) return;
        const st: ScanStatus = d?.status ?? "error";
        setScanStatus(st);
        if (st === "done") {
          setScanResult(d?.result ?? null);
          setScanBusy(false);
          toast("Скан завершён", "success");
        } else if (st === "error") {
          setScanBusy(false);
          toast(typeof d?.error === "string" ? d.error : "Скан завершился с ошибкой", "error");
        } else if (st === "cancelled") {
          setScanBusy(false);
        }
      } catch { /* сеть моргнула — следующий тик повторит */ }
    };
    const t = setInterval(() => void tick(), 1500);
    return () => { stop = true; clearInterval(t); };
  }, [reqId, scanStatus]);

  const startScan = async () => {
    if (!sel) return;
    const all = picked.length === 0;
    setScanBusy(true);
    setScanResult(null);
    try {
      const res = await api("/latency-scan", {
        method: "POST",
        body: JSON.stringify({
          provider_id: sel.pid,
          list_id: sel.lid,
          ...(all ? { all: true } : { row_ids: picked }),
          ...(scanOp ? { operator: scanOp } : {}),
          async_: true,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d.ok === false) {
        toast(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`, "error");
        setScanBusy(false);
        return;
      }
      setReqId(d.req_id ?? null);
      setScanStatus(d.status ?? "pending");
    } catch (e) {
      toast((e as Error).message, "error");
      setScanBusy(false);
    }
  };

  const cancelScan = async () => {
    if (!reqId) return;
    await api("/latency-scan/cancel", { method: "POST", body: JSON.stringify({ req_id: reqId }) })
      .catch(() => {});
    setScanStatus("cancelled");
    setScanBusy(false);
  };

  return (
    <Page max={1100}>
      <PageHeader icon={<Table2 size={16} />} title="Подсети"
        subtitle="Справочник подсетей/IP по провайдерам — разметка для обходов БС" />

      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "240px 1fr", alignItems: "start" }}>
        {/* ── дерево ── */}
        <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="flex items-center gap-2">
            <FolderKanban size={13} style={{ color: "var(--t-low)" }} />
            <span className="micro">Провайдеры</span>
            <button className="iconbtn" style={{ marginLeft: "auto", width: 22, height: 22 }}
              title="Добавить провайдера" onClick={addProvider}><Plus size={13} /></button>
          </div>
          {providers.length === 0 && (
            <p className="hint">Создайте провайдера, а в нём — список подсетей.</p>
          )}
          {providers.map(p => (
            <div key={p.id}>
              <div className="flex items-center gap-1 group" style={{ padding: "2px 4px" }}>
                <span className="text-xs font-semibold trunc" style={{ color: "var(--t-hi)", flex: 1 }}>{p.name}</span>
                <button className="iconbtn" style={{ width: 20, height: 20 }} title="Новый список"
                  onClick={() => addList(p.id)}><Plus size={11} /></button>
                <button className="iconbtn" style={{ width: 20, height: 20 }} title="Переименовать"
                  onClick={() => rename("providers", p.id, null, p.name)}><Pencil size={11} /></button>
                <button className="iconbtn danger" style={{ width: 20, height: 20 }} title="Удалить"
                  onClick={() => remove("providers", p.id, null, p.name)}><Trash2 size={11} /></button>
              </div>
              {p.lists.map(l => (
                <div key={l.id}
                  className="flex items-center gap-1 pl-4 group rounded-md"
                  style={{
                    padding: "4px 4px 4px 16px", cursor: "pointer",
                    background: sel?.lid === l.id ? "var(--accent-dim)" : "transparent",
                  }}
                  onClick={() => { setSel({ pid: p.id, lid: l.id }); setEditMode(false); }}>
                  <span className="text-xs trunc" style={{ color: "var(--t-mid)", flex: 1 }}>{l.name}</span>
                  <button className="iconbtn" style={{ width: 20, height: 20 }}
                    onClick={e => { e.stopPropagation(); rename("lists", p.id, l.id, l.name); }}><Pencil size={10} /></button>
                  <button className="iconbtn danger" style={{ width: 20, height: 20 }}
                    onClick={e => { e.stopPropagation(); remove("lists", p.id, l.id, l.name); }}><Trash2 size={10} /></button>
                </div>
              ))}
            </div>
          ))}
        </div>

        {/* ── таблица ── */}
        {!current ? (
          <div className="card card-p" style={{ textAlign: "center", color: "var(--t-faint)", fontSize: 13 }}>
            Выберите список слева или создайте нового провайдера.
          </div>
        ) : (
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
              <Table2 size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">{current.name}</span>
              <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>{current.rows.length} строк</span>
              {latEnabled && (
                <button className={`btn btn-sm ${scanOpen ? "btn-primary" : "btn-soft"}`}
                  style={{ marginLeft: "auto", fontSize: 11 }}
                  data-testid="latency-scan-toggle"
                  onClick={() => (scanOpen ? closeScan() : void openScan())}>
                  <Activity size={11} /> Скан Latency
                </button>
              )}
              <button className={`btn btn-sm ${editMode ? "btn-primary" : "btn-soft"}`}
                style={{ marginLeft: latEnabled ? undefined : "auto", fontSize: 11 }}
                data-testid="table-edit-toggle"
                onClick={() => setEditMode(m => !m)}>
                {editMode ? <Check size={11} /> : <Pencil size={11} />}
                {editMode ? "Готово" : "Редактировать таблицу"}
              </button>
            </div>

            {editMode && (
              <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
                <span className="hint" style={{ margin: 0 }}>Столбцы: перетаскивайте заголовки.</span>
                <button className="btn btn-soft" style={{ padding: "3px 10px", fontSize: 11 }}
                  onClick={() => {
                    const title = window.prompt("Название столбца", "Комментарий");
                    if (title) void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns`, "POST", { title });
                  }}>
                  <Plus size={11} /> Столбец
                </button>
              </div>
            )}

            {scanOpen && (
              <div className="flex flex-col gap-2 px-3 py-2.5" data-testid="latency-scan-panel"
                style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="micro" style={{ margin: 0 }}>Скан Latency</span>
                  <span className="text-[10px]" style={{ color: "var(--t-faint)" }}>
                    {picked.length ? `выбрано ${picked.length}` : "все строки"}
                  </span>
                  <select className="selectbox text-xs" style={{ width: 180 }}
                    data-testid="latency-operator" value={scanOp}
                    onChange={e => setScanOp(e.target.value)}>
                    <option value="">Все операторы (multiscan)</option>
                    {latOps.map(o => (
                      <option key={o.id} value={o.id} disabled={!o.configured}>
                        {o.label}{o.online ? "" : " (offline)"}
                      </option>
                    ))}
                  </select>
                  {scanBusy || scanStatus === "pending" ? (
                    <button className="btn btn-soft btn-sm" style={{ fontSize: 11 }}
                      data-testid="latency-cancel" onClick={cancelScan}>
                      <X size={11} /> Отменить
                    </button>
                  ) : (
                    <button className="btn btn-primary btn-sm" style={{ fontSize: 11 }}
                      data-testid="latency-start" onClick={startScan}
                      disabled={current.rows.length === 0}>
                      <Activity size={11} /> Запустить
                    </button>
                  )}
                  {scanStatus === "pending" && (
                    <span className="chip accent" style={{ fontSize: 10 }} data-testid="latency-progress">
                      <Loader2 size={10} className="spin" /> Сканирование…
                    </span>
                  )}
                  {scanStatus === "cancelled" && (
                    <span className="chip warn" style={{ fontSize: 10 }}>Отменено</span>
                  )}
                  {scanStatus === "error" && (
                    <span className="chip err" style={{ fontSize: 10 }}>Ошибка</span>
                  )}
                </div>

                {scanStatus === "done" && (
                  <div data-testid="latency-result" className="flex flex-col gap-1">
                    {(scanResult?.rows?.length ? scanResult.rows : scanResult ? [scanResult] : []).map((it, i) => (
                      <div key={it.row_id ?? i} className="flex items-center gap-2 flex-wrap text-xs"
                        style={{ color: "var(--t-mid)" }}>
                        <span style={{ color: "var(--t-hi)" }}>
                          {it.subnet ?? current.rows.find(r => r.id === it.row_id)?.values?.subnet ?? "—"}
                        </span>
                        {it.operator && <span className="chip neutral" style={{ fontSize: 10 }}>{it.operator}</span>}
                        <span className={`chip ${it.available ? "ok" : "err"}`} style={{ fontSize: 10 }}>
                          {it.available ? "доступна" : "недоступна"}
                        </span>
                        <span>живых IP: {it.alive_count ?? 0}</span>
                        {it.status_text && <span style={{ color: "var(--t-low)" }}>{it.status_text}</span>}
                        {!!it.reachable_ips?.length && (
                          <span className="font-mono trunc" style={{ color: "var(--t-faint)", maxWidth: 320 }}
                            title={it.reachable_ips.join(", ")}>
                            {it.reachable_ips.join(", ")}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div style={{ overflowX: "auto" }}>
              <table className="tbl colborders text-xs">
                <thead>
                  <tr>
                    {scanOpen && (
                      <th style={{ width: 28 }}>
                        <input type="checkbox" data-testid="latency-pick-all"
                          aria-label="Выбрать все строки"
                          checked={current.rows.length > 0 && picked.length === current.rows.length}
                          onChange={toggleAll} />
                      </th>
                    )}
                    {current.columns.map(c => (
                      <th key={c.key}
                        draggable={editMode}
                        onDragStart={() => { dragCol.current = c.key; }}
                        onDragOver={editMode ? e => e.preventDefault() : undefined}
                        onDrop={editMode ? e => {
                          e.preventDefault();
                          const from = dragCol.current;
                          if (!from || from === c.key) return;
                          const order = current.columns.map(x => x.key);
                          order.splice(order.indexOf(from), 1);
                          order.splice(order.indexOf(c.key), 0, from);
                          void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns-order`, "PUT", { order });
                        } : undefined}
                        style={{ cursor: editMode ? "grab" : undefined }}>
                        <span className="flex items-center gap-1">
                          {editMode && <GripVertical size={10} style={{ color: "var(--t-faint)" }} />}
                          {c.title}
                          {editMode && c.key !== "subnet" && (
                            <>
                              <button className="iconbtn" style={{ width: 16, height: 16 }} title="Переименовать"
                                onClick={() => {
                                  const title = window.prompt("Название столбца", c.title);
                                  if (title) void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns/${c.key}`, "PATCH", { title });
                                }}><Pencil size={9} /></button>
                              <button className="iconbtn danger" style={{ width: 16, height: 16 }} title="Удалить столбец"
                                onClick={() => {
                                  if (window.confirm(`Удалить столбец «${c.title}»?`))
                                    void mutate(`/providers/${sel!.pid}/lists/${sel!.lid}/columns/${c.key}`, "DELETE");
                                }}><X size={9} /></button>
                            </>
                          )}
                        </span>
                      </th>
                    ))}
                    <th style={{ width: 32 }} />
                  </tr>
                </thead>
                <tbody>
                  {current.rows.length === 0 && (
                    <tr><td colSpan={current.columns.length + (scanOpen ? 2 : 1)}
                      style={{ textAlign: "center", color: "var(--t-faint)", padding: 16 }}>
                      Пусто — добавьте подсеть ниже.
                    </td></tr>
                  )}
                  {current.rows.map(r => (
                    <tr key={r.id}>
                      {scanOpen && (
                        <td>
                          <input type="checkbox" data-testid={`latency-pick-${r.id}`}
                            aria-label={`Выбрать ${r.values?.subnet ?? r.id}`}
                            checked={picked.includes(r.id)}
                            onChange={() => togglePick(r.id)} />
                        </td>
                      )}
                      {current.columns.map(c => (
                        <td key={c.key}>
                          {c.key === "operators" ? (
                            <span className="flex items-center gap-1.5">
                              {operators.map(op => (
                                editMode ? (
                                  <label key={op.key} className="flex items-center gap-0.5"
                                    title={`${op.label}: показывать иконку`}
                                    style={{ cursor: "pointer" }}>
                                    <input type="checkbox"
                                      checked={r.operators?.[op.key] !== false}
                                      onChange={e => void mutate(
                                        `/providers/${sel!.pid}/lists/${sel!.lid}/rows/${r.id}/operator/${op.key}`,
                                        "PATCH", { on: e.target.checked })} />
                                    <OpIcon op={op.key} dim={r.operators?.[op.key] === false} />
                                  </label>
                                ) : (
                                  r.operators?.[op.key] !== false && <OpIcon key={op.key} op={op.key} />
                                )
                              ))}
                            </span>
                          ) : (
                            <span className="trunc" title={r.values?.[c.key] || ""}
                              style={{ color: c.key === "subnet" ? "var(--t-hi)" : "var(--t-mid)" }}>
                              {r.values?.[c.key] || "—"}
                            </span>
                          )}
                        </td>
                      ))}
                      <td>
                        <button className="iconbtn danger" title="Удалить строку"
                          onClick={() => void mutate(
                            `/providers/${sel!.pid}/lists/${sel!.lid}/rows/${r.id}`, "DELETE")}>
                          <Trash2 size={12} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex gap-2 px-3 py-2.5" style={{ borderTop: "1px solid var(--line-soft)" }}>
              <textarea className="input font-mono text-xs" rows={1} value={newSubnet}
                onChange={e => setNewSubnet(e.target.value)}
                placeholder="203.0.113.0/24 — можно несколько, через запятую или с новой строки"
                style={{ resize: "vertical", minHeight: 34 }} />
              <button className="btn btn-primary" style={{ flex: "none" }} onClick={addRows}
                disabled={busy || !newSubnet.trim()}>
                {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Добавить
              </button>
            </div>
          </div>
        )}
      </div>
    </Page>
  );
}

/** Иконка оператора из /operators/<key>.png — файлы заменяемы без правок кода. */
function OpIcon({ op, dim }: { op: string; dim?: boolean }) {
  return (
    <img src={`/operators/${op}.png`} alt={op} width={16} height={16}
      style={{ borderRadius: 4, opacity: dim ? 0.25 : 1, flex: "none", objectFit: "contain" }} />
  );
}
