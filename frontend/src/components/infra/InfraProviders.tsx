import { useState, useEffect, useCallback } from "react";
import { Plus, Loader2, Pencil, Trash2, ExternalLink, RefreshCw, CreditCard, Cloud, Download } from "lucide-react";
import { infraApi, type Provider, type ProviderAdapterInfo } from "./api";
import { VaultPicker } from "../vault/VaultPicker";
import { toast } from "./Toast";
import { getFlagEmoji } from "../../utils/format";

const EMPTY = { name: "", favicon_link: "", login_url: "", balance: "0", currency: "RUB",
                low_balance_threshold: "0", adapter_kind: "", vault_entry_id: "" };

/** «5 минут назад» for the last successful balance sync. */
function ago(ts: number): string {
  if (!ts) return "никогда";
  const m = Math.floor((Date.now() / 1000 - ts) / 60);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} ч назад` : `${Math.floor(h / 24)} дн назад`;
}

export function InfraProviders() {
  const [rows, setRows] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Provider }>(null);
  const [syncing, setSyncing] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await infraApi.listProviders()); }
    catch (e) { toast(String((e as Error).message), "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const sync = async (p: Provider) => {
    setSyncing(p.uuid);
    try {
      const r = await infraApi.syncProvider(p.uuid);
      // The route answers 200 even on a vendor failure (the adapter never raises),
      // so the ok flag — not the HTTP status — decides what the user is told.
      if (r.ok) {
        toast(r.balance !== undefined
          ? `${p.name}: баланс ${r.balance} ${r.currency ?? ""}`
          : `${p.name}: креды верны, API баланса не отдаёт`, "success");
      } else {
        toast(`${p.name}: ${r.error ?? "синхронизация не удалась"}`, "error");
      }
      load();
    } catch (e) { toast(String((e as Error).message), "error"); }
    setSyncing("");
  };

  const importServices = async (p: Provider) => {
    try {
      const r = await infraApi.importProviderServices(p.uuid);
      toast(`Импортировано услуг: ${r.created} (пропущено ${r.skipped})`, "success");
    } catch (e) { toast(String((e as Error).message), "error"); }
  };

  const del = async (p: Provider) => {
    const force = p.nodeCount > 0;
    if (force && !confirm(`К «${p.name}» привязано узлов: ${p.nodeCount}. Удалить принудительно?`)) return;
    try {
      await infraApi.deleteProvider(p.uuid, force);
      toast(`Провайдер «${p.name}» удалён`, "success");
      load();
    } catch (e) { toast(String((e as Error).message), "error"); }
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-base font-semibold text-[var(--t-hi)] flex items-center gap-2">
              <CreditCard size={16} className="text-[var(--accent-hi)]" /> Провайдеры хостинга
            </h1>
            <p className="text-xs text-[var(--t-low)] mt-0.5">Аккаунты облачных провайдеров и их балансы</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] hover:bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>
            <button onClick={() => setModal({})}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)]">
              <Plus size={13} /> Добавить провайдера
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-[var(--line-soft)] overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[var(--bg1)] text-[var(--t-low)] text-[11px] uppercase tracking-widest">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                <th className="text-right font-medium px-4 py-2.5">Баланс</th>
                <th className="text-right font-medium px-4 py-2.5">Порог алерта</th>
                <th className="text-center font-medium px-4 py-2.5">Узлов</th>
                <th className="text-right font-medium px-4 py-2.5">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--line-soft)]">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-[var(--t-faint)] text-xs">Провайдеры не добавлены.</td></tr>
              ) : rows.map(p => {
                const low = p.lowBalanceThreshold > 0 && p.balance < p.lowBalanceThreshold;
                return (
                  <tr key={p.uuid} className="hover:bg-[var(--row-hover)]">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {p.faviconLink
                          ? <img src={p.faviconLink} alt="" className="w-4 h-4 rounded" onError={e => (e.currentTarget.style.display = "none")} />
                          : <span className="w-4 h-4 rounded bg-[var(--bg3)]" />}
                        {/* Geo flag — shown only when the provider carries a location code
                            (Remnawave providers currently have none; future-ready). */}
                        {p.countryCode && <span title={p.countryCode}>{getFlagEmoji(p.countryCode)}</span>}
                        <span className="text-[var(--t-hi)]">{p.name}</span>
                        {p.loginUrl && <a href={p.loginUrl} target="_blank" rel="noreferrer" className="text-[var(--t-faint)] hover:text-[var(--accent-hi)]"><ExternalLink size={11} /></a>}
                      </div>
                      {p.adapterKind ? (
                        <p className="text-[11px] text-[var(--t-faint)] mt-0.5">
                          API: {p.adapterKind} · обновлён {ago(p.balanceSyncedAt)}
                          {p.lastError && <span className="text-[var(--err)]"> · {p.lastError}</span>}
                        </p>
                      ) : (
                        <p className="text-[11px] text-[var(--t-faint)] mt-0.5">баланс вручную</p>
                      )}
                    </td>
                    <td className={`px-4 py-2.5 text-right tabular-nums ${low ? "text-[var(--err)]" : "text-[var(--t-hi)]"}`}>
                      {p.balance.toLocaleString("ru-RU")} {p.currency}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-[var(--t-low)]">
                      {p.lowBalanceThreshold ? `${p.lowBalanceThreshold.toLocaleString("ru-RU")} ${p.currency}` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-center text-[var(--t-mid)]">{p.nodeCount}</td>
                    <td className="px-4 py-2.5 text-right">
                      {p.adapterKind && <>
                        <button onClick={() => sync(p)} disabled={syncing === p.uuid} title="Синхронизировать через API провайдера"
                          className="p-1.5 text-[var(--t-low)] hover:text-[var(--accent-hi)] disabled:opacity-50">
                          {syncing === p.uuid ? <Loader2 size={13} className="animate-spin" /> : <Cloud size={13} />}
                        </button>
                        <button onClick={() => importServices(p)} title="Импортировать услуги провайдера"
                          className="p-1.5 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Download size={13} /></button>
                      </>}
                      <button onClick={() => setModal({ edit: p })} className="p-1.5 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Pencil size={13} /></button>
                      <button onClick={() => del(p)} className="p-1.5 text-[var(--t-low)] hover:text-[var(--err)]"><Trash2 size={13} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {modal && <ProviderModal edit={modal.edit} onClose={() => setModal(null)} onSaved={() => { setModal(null); load(); }} />}
    </div>
  );
}

function ProviderModal({ edit, onClose, onSaved }: { edit?: Provider; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState(edit ? {
    name: edit.name, favicon_link: edit.faviconLink, login_url: edit.loginUrl,
    balance: String(edit.balance), currency: edit.currency, low_balance_threshold: String(edit.lowBalanceThreshold),
    adapter_kind: edit.adapterKind || "", vault_entry_id: edit.vaultEntryId || "",
  } : EMPTY);
  const [saving, setSaving] = useState(false);
  const [adapters, setAdapters] = useState<ProviderAdapterInfo[]>([]);
  useEffect(() => { infraApi.listAdapters().then(setAdapters).catch(() => setAdapters([])); }, []);
  const adapter = adapters.find(a => a.kind === f.adapter_kind);
  const set = (k: keyof typeof f, v: string) => setF(p => ({ ...p, [k]: v }));

  const submit = async () => {
    if (!f.name.trim()) { toast("Укажите имя провайдера", "error"); return; }
    const bal = parseFloat(f.balance), thr = parseFloat(f.low_balance_threshold);
    if (isNaN(bal) || bal < 0) { toast("Некорректный баланс", "error"); return; }
    setSaving(true);
    const body = {
      name: f.name.trim(), favicon_link: f.favicon_link.trim(), login_url: f.login_url.trim(),
      balance: bal, currency: f.currency.trim() || "RUB", low_balance_threshold: isNaN(thr) ? 0 : thr,
      adapter_kind: f.adapter_kind, vault_entry_id: f.vault_entry_id,
    };
    try {
      if (edit) await infraApi.updateProvider(edit.uuid, body);
      else await infraApi.createProvider(body);
      toast(edit ? "Провайдер обновлён" : "Провайдер создан", "success");
      onSaved();
    } catch (e) { toast(String((e as Error).message), "error"); setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-4" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-[var(--bg1)] border border-[var(--line)] rounded-xl w-full max-w-md p-5">
        <h2 className="text-sm font-semibold text-[var(--t-hi)] mb-4">{edit ? "Редактировать провайдера" : "Новый провайдер"}</h2>
        <div className="flex flex-col gap-3">
          <Field label="Имя *" value={f.name} onChange={v => set("name", v)} placeholder="Selectel" />
          <Field label="URL панели провайдера" value={f.login_url} onChange={v => set("login_url", v)} placeholder="https://my.selectel.ru" />
          <Field label="Favicon (URL)" value={f.favicon_link} onChange={v => set("favicon_link", v)} placeholder="https://…/favicon.ico" />
          <div className="grid grid-cols-3 gap-3">
            <Field label="Баланс" value={f.balance} onChange={v => set("balance", v)} />
            <Field label="Валюта" value={f.currency} onChange={v => set("currency", v)} />
            <Field label="Порог алерта" value={f.low_balance_threshold} onChange={v => set("low_balance_threshold", v)} />
          </div>
          <p className="text-[11px] text-[var(--t-faint)]">Баланс, валюта и порог хранятся локально — Remnawave их не хранит.</p>

          <div className="flex flex-col gap-1 pt-1 border-t border-[var(--line-soft)]">
            <label className="label">Адаптер API провайдера</label>
            <select value={f.adapter_kind} onChange={e => set("adapter_kind", e.target.value)} className="input">
              <option value="">— не использовать (баланс вручную) —</option>
              {adapters.map(a => <option key={a.kind} value={a.kind}>{a.title}</option>)}
            </select>
            {adapter && (
              <p className="text-[11px] text-[var(--t-faint)]">
                Умеет: {adapter.caps.join(", ") || "—"}
                {!adapter.caps.includes("balance") && " — баланс этот API не отдаёт, вводите вручную"}
              </p>
            )}
            {f.adapter_kind && (
              <div className="flex flex-col gap-1 mt-1">
                <label className="label">Креды из Хранилища</label>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-[var(--t-mid)] flex-1 truncate">
                    {f.vault_entry_id ? `запись выбрана (${f.vault_entry_id.slice(0, 8)}…)` : "не выбрана"}
                  </span>
                  {/* Only the entry id travels — the credential itself is read
                      server-side by the adapter and never reaches this form. */}
                  <VaultPicker kinds={["provider_creds", "api_key", "login"]}
                    onPickValue={() => { /* значение не нужно: адаптеру хватает ref */ }}
                    onPickKeyRef={ref => set("vault_entry_id", ref)}
                    pickRefOnly />
                  {f.vault_entry_id && (
                    <button type="button" onClick={() => set("vault_entry_id", "")}
                      className="text-xs text-[var(--t-low)] hover:text-[var(--err)]">сбросить</button>
                  )}
                </div>
                <p className="text-[11px] text-[var(--t-faint)]">
                  Поля кредов задаются в Справка → Хранилище (тип «Креды провайдера»):
                  {adapter ? ` ${adapter.fields.map(x => x.label).join(", ")}` : ""}
                </p>
              </div>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-[var(--t-mid)] hover:text-[var(--t-hi)]">Отмена</button>
          <button onClick={submit} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
            {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} spellCheck={false}
        className="input" />
    </div>
  );
}
