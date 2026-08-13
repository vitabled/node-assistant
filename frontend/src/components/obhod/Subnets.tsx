import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check, FolderKanban, GripVertical, Loader2, Pencil, Plus, Table2, Trash2, X,
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
 */

interface Col { key: string; title: string }
interface Op { key: string; label: string }
interface Row { id: string; values: Record<string, string>; operators: Record<string, boolean> }
interface Lst { id: string; name: string; columns: Col[]; rows: Row[] }
interface Prov { id: string; name: string; lists: Lst[] }

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

  const load = useCallback(() => {
    api("").then(r => r.json()).then(d => {
      setProviders(d.providers || []);
      setOperators(d.operators || []);
    }).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

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
              <button className={`btn btn-sm ${editMode ? "btn-primary" : "btn-soft"}`}
                style={{ marginLeft: "auto", fontSize: 11 }}
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

            <div style={{ overflowX: "auto" }}>
              <table className="tbl colborders text-xs">
                <thead>
                  <tr>
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
                    <tr><td colSpan={current.columns.length + 1}
                      style={{ textAlign: "center", color: "var(--t-faint)", padding: 16 }}>
                      Пусто — добавьте подсеть ниже.
                    </td></tr>
                  )}
                  {current.rows.map(r => (
                    <tr key={r.id}>
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
