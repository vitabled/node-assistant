import { useState, useEffect, useCallback } from "react";
import { Plus, Loader2, Pencil, Trash2, ExternalLink, RefreshCw, CreditCard } from "lucide-react";
import { infraApi, type Provider } from "./api";
import { toast } from "./Toast";
import { getFlagEmoji } from "../../utils/format";

const EMPTY = { name: "", favicon_link: "", login_url: "", balance: "0", currency: "RUB", low_balance_threshold: "0" };

export function InfraProviders() {
  const [rows, setRows] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<null | { edit?: Provider }>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await infraApi.listProviders()); }
    catch (e) { toast(String((e as Error).message), "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

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
            <h1 className="text-base font-semibold text-white flex items-center gap-2">
              <CreditCard size={16} className="text-blue-400" /> Провайдеры хостинга
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">Аккаунты облачных провайдеров и их балансы</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
            <button onClick={() => setModal({})}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white">
              <Plus size={13} /> Добавить провайдера
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
              <tr>
                <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                <th className="text-right font-medium px-4 py-2.5">Баланс</th>
                <th className="text-right font-medium px-4 py-2.5">Порог алерта</th>
                <th className="text-center font-medium px-4 py-2.5">Узлов</th>
                <th className="text-right font-medium px-4 py-2.5">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600"><Loader2 size={16} className="animate-spin inline" /></td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600 text-xs">Провайдеры не добавлены.</td></tr>
              ) : rows.map(p => {
                const low = p.lowBalanceThreshold > 0 && p.balance < p.lowBalanceThreshold;
                return (
                  <tr key={p.uuid} className="hover:bg-gray-900/40">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {p.faviconLink
                          ? <img src={p.faviconLink} alt="" className="w-4 h-4 rounded" onError={e => (e.currentTarget.style.display = "none")} />
                          : <span className="w-4 h-4 rounded bg-gray-700" />}
                        {/* Geo flag — shown only when the provider carries a location code
                            (Remnawave providers currently have none; future-ready). */}
                        {p.countryCode && <span title={p.countryCode}>{getFlagEmoji(p.countryCode)}</span>}
                        <span className="text-gray-200">{p.name}</span>
                        {p.loginUrl && <a href={p.loginUrl} target="_blank" rel="noreferrer" className="text-gray-600 hover:text-blue-400"><ExternalLink size={11} /></a>}
                      </div>
                    </td>
                    <td className={`px-4 py-2.5 text-right tabular-nums ${low ? "text-red-400" : "text-gray-200"}`}>
                      {p.balance.toLocaleString("ru-RU")} {p.currency}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">
                      {p.lowBalanceThreshold ? `${p.lowBalanceThreshold.toLocaleString("ru-RU")} ${p.currency}` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-center text-gray-400">{p.nodeCount}</td>
                    <td className="px-4 py-2.5 text-right">
                      <button onClick={() => setModal({ edit: p })} className="p-1.5 text-gray-500 hover:text-blue-400"><Pencil size={13} /></button>
                      <button onClick={() => del(p)} className="p-1.5 text-gray-500 hover:text-red-400"><Trash2 size={13} /></button>
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
  } : EMPTY);
  const [saving, setSaving] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF(p => ({ ...p, [k]: v }));

  const submit = async () => {
    if (!f.name.trim()) { toast("Укажите имя провайдера", "error"); return; }
    const bal = parseFloat(f.balance), thr = parseFloat(f.low_balance_threshold);
    if (isNaN(bal) || bal < 0) { toast("Некорректный баланс", "error"); return; }
    setSaving(true);
    const body = {
      name: f.name.trim(), favicon_link: f.favicon_link.trim(), login_url: f.login_url.trim(),
      balance: bal, currency: f.currency.trim() || "RUB", low_balance_threshold: isNaN(thr) ? 0 : thr,
    };
    try {
      if (edit) await infraApi.updateProvider(edit.uuid, body);
      else await infraApi.createProvider(body);
      toast(edit ? "Провайдер обновлён" : "Провайдер создан", "success");
      onSaved();
    } catch (e) { toast(String((e as Error).message), "error"); setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="bg-gray-950 border border-gray-700/60 rounded-xl w-full max-w-md p-5">
        <h2 className="text-sm font-semibold text-white mb-4">{edit ? "Редактировать провайдера" : "Новый провайдер"}</h2>
        <div className="flex flex-col gap-3">
          <Field label="Имя *" value={f.name} onChange={v => set("name", v)} placeholder="Selectel" />
          <Field label="URL панели провайдера" value={f.login_url} onChange={v => set("login_url", v)} placeholder="https://my.selectel.ru" />
          <Field label="Favicon (URL)" value={f.favicon_link} onChange={v => set("favicon_link", v)} placeholder="https://…/favicon.ico" />
          <div className="grid grid-cols-3 gap-3">
            <Field label="Баланс" value={f.balance} onChange={v => set("balance", v)} />
            <Field label="Валюта" value={f.currency} onChange={v => set("currency", v)} />
            <Field label="Порог алерта" value={f.low_balance_threshold} onChange={v => set("low_balance_threshold", v)} />
          </div>
          <p className="text-[11px] text-gray-600">Баланс, валюта и порог хранятся локально (Remnawave их не хранит). API-токен провайдера не сохраняется.</p>
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
          <button onClick={submit} disabled={saving}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
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
      <label className="text-[11px] font-medium text-gray-500 uppercase tracking-widest">{label}</label>
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} spellCheck={false}
        className="w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2 text-sm text-gray-100
                   placeholder:text-gray-700 focus:outline-none focus:ring-1 focus:border-blue-500/70 focus:ring-blue-500/20" />
    </div>
  );
}
