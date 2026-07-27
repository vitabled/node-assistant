// «Справка → Хранилище» (Wave-9 Plan A Ф6).
//
// A revealed secret is held in component state only, dropped after REVEAL_TTL_MS
// and whenever the tab loses visibility — so a copied-and-forgotten screen does
// not keep a password on display.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Lock, Plus, Eye, EyeOff, Download, Pencil, Trash2, Search, AlertTriangle } from "lucide-react";
import { Page, PageHeader, fmtDate } from "../infra/ui";
import { toast } from "../infra/Toast";
import { EntryModal } from "./EntryModal";
import { SecretField } from "./SecretField";
import {
  listEntries, deleteEntry, revealEntry, downloadKey, KIND_LABELS, REVEAL_TTL_MS,
  type VaultEntry, type VaultKind,
} from "./api";

const KIND_ORDER: VaultKind[] = [
  "ssh_key", "ssh_password", "api_key", "provider_creds", "login", "note",
];

export function Vault() {
  const [rows, setRows] = useState<VaultEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<VaultEntry | null>(null);
  const [creating, setCreating] = useState(false);
  const [confirmId, setConfirmId] = useState("");
  const [shown, setShown] = useState<{ id: string; fields: Record<string, string> } | null>(null);
  const hideTimer = useRef<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listEntries()
      .then(r => { setRows(r); setErr(""); })
      .catch(e => setErr(e instanceof Error ? e.message : "Не удалось загрузить"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  const hide = useCallback(() => {
    if (hideTimer.current) { window.clearTimeout(hideTimer.current); hideTimer.current = null; }
    setShown(null);
  }, []);

  // Leaving the tab hides the secret: shoulder-surfing is the threat this screen
  // actually has to answer for.
  useEffect(() => {
    const onHidden = () => { if (document.hidden) hide(); };
    document.addEventListener("visibilitychange", onHidden);
    return () => { document.removeEventListener("visibilitychange", onHidden); hide(); };
  }, [hide]);

  const reveal = async (e: VaultEntry) => {
    if (shown?.id === e.id) { hide(); return; }
    try {
      const fields = await revealEntry(e.id);
      setShown({ id: e.id, fields });
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(() => setShown(null), REVEAL_TTL_MS);
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Не удалось показать секрет", "error");
    }
  };

  const remove = async (e: VaultEntry) => {
    if (confirmId !== e.id) { setConfirmId(e.id); return; }
    try {
      await deleteEntry(e.id);
      setConfirmId("");
      if (shown?.id === e.id) hide();
      load();
    } catch (ex) {
      toast(ex instanceof Error ? ex.message : "Не удалось удалить", "error");
    }
  };

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const match = (e: VaultEntry) => !needle
      || [e.name, e.resource, e.username, ...e.tags].join(" ").toLowerCase().includes(needle);
    const kept = rows.filter(match);
    return KIND_ORDER
      .map(kind => ({ kind, items: kept.filter(e => e.kind === kind) }))
      .filter(g => g.items.length > 0);
  }, [rows, q]);

  return (
    <Page>
      <PageHeader
        icon={<Lock size={18} />}
        title="Хранилище"
        subtitle="Пароли, API-ключи и SSH-ключи от внешних ресурсов"
        actions={
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            <Plus size={14} /> Добавить
          </button>
        }
      />

      <div className="rounded-lg p-3 mb-4 text-xs"
        style={{ background: "var(--warn-dim, var(--bg-soft))", border: "1px solid var(--line)", color: "var(--t-hi)" }}>
        <AlertTriangle size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "-2px" }} />
        Секреты шифруются ключом <code>ENCRYPTION_KEY</code> из <code>.env</code>.
        Потеря или смена ключа = потеря Хранилища — сделайте резервную копию файла.
        В экспорт аккаунта секреты не попадают (только перечень записей).
      </div>

      <label className="flex items-center gap-2 mb-3" style={{ maxWidth: 320 }}>
        <Search size={14} style={{ color: "var(--t-low)", flex: "none" }} />
        <input className="input" placeholder="Поиск по названию, ресурсу, тегу"
          value={q} onChange={e => setQ(e.target.value)} />
      </label>

      {loading && <p className="micro">Загрузка…</p>}
      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}
      {!loading && !err && rows.length === 0 && (
        <p className="micro" style={{ color: "var(--t-low)" }}>
          Пока пусто. Добавьте пароль, API-ключ или SSH-ключ — потом его можно будет
          выбрать прямо в формах деплоя и в биллинге провайдеров.
        </p>
      )}
      {!loading && !err && rows.length > 0 && groups.length === 0 && (
        <p className="micro" style={{ color: "var(--t-low)" }}>Ничего не найдено.</p>
      )}

      {groups.map(g => (
        <section key={g.kind} className="mb-5">
          <p className="micro" style={{ marginBottom: 6 }}>{KIND_LABELS[g.kind]}</p>
          <div className="flex flex-col gap-2">
            {g.items.map(e => (
              <div key={e.id} className="rounded-lg p-3"
                style={{ background: "var(--panel)", border: "1px solid var(--line-soft)" }}>
                <div className="flex items-start gap-3 flex-wrap">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <p className="text-sm font-medium" style={{ color: "var(--t-hi)" }}>{e.name}</p>
                    <p className="micro" style={{ color: "var(--t-low)" }}>
                      {[e.resource, e.username].filter(Boolean).join(" · ") || "—"}
                    </p>
                    <p className="micro" style={{ color: "var(--t-low)", marginTop: 2 }}>
                      {e.broken
                        ? <span style={{ color: "var(--err, var(--accent))" }}>
                            секрет не расшифровывается — вероятно, сменился ENCRYPTION_KEY
                          </span>
                        : e.has_secret
                          ? <>секрет: <code>{e.hint}</code></>
                          : "секрет не задан"}
                      {e.updated_at ? ` · изменён ${fmtDate(e.updated_at * 1000)}` : ""}
                    </p>
                    {e.tags.length > 0 && (
                      <div className="flex gap-1 flex-wrap" style={{ marginTop: 4 }}>
                        {e.tags.map(t => (
                          <span key={t} className="micro px-1.5 py-0.5 rounded"
                            style={{ background: "var(--bg-soft)", border: "1px solid var(--line-soft)" }}>{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1 flex-none">
                    <button className="btn" title="Показать" disabled={!e.has_secret || e.broken}
                      onClick={() => reveal(e)}>
                      {shown?.id === e.id ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                    {e.kind === "ssh_key" && (
                      <button className="btn" title="Скачать ключ" disabled={!e.has_secret || e.broken}
                        onClick={() => downloadKey(e).catch(ex =>
                          toast(ex instanceof Error ? ex.message : "Не удалось скачать", "error"))}>
                        <Download size={14} />
                      </button>
                    )}
                    <button className="btn" title="Изменить" onClick={() => setEditing(e)}>
                      <Pencil size={14} />
                    </button>
                    <button className="btn" title="Удалить" onClick={() => remove(e)}>
                      <Trash2 size={14} />
                      {confirmId === e.id && <span style={{ marginLeft: 4 }}>ещё раз?</span>}
                    </button>
                  </div>
                </div>

                {shown?.id === e.id && (
                  <div className="flex flex-col gap-2" style={{ marginTop: 10 }}>
                    {Object.entries(shown.fields).map(([k, v]) => (
                      <SecretField key={k} label={k} value={v} readOnly defaultVisible
                        kind={v.includes("\n") ? "textarea" : "password"} />
                    ))}
                    <p className="micro" style={{ color: "var(--t-low)" }}>
                      Скроется автоматически через 30 секунд.
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}

      {(creating || editing) && (
        <EntryModal
          entry={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); load(); }}
        />
      )}
    </Page>
  );
}
