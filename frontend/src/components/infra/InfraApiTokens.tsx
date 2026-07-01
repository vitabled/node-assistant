import { useState, useEffect, useCallback } from "react";
import { KeyRound, Plus, Loader2, Trash2, RefreshCw, Lock, Plug, ShieldCheck } from "lucide-react";
import { infraApi, type ApiToken } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, Field, SelectField, Modal, fmtDate } from "./ui";

const KINDS = [
  { v: "selectel", l: "Selectel API Key" }, { v: "hetzner", l: "Hetzner Token" },
  { v: "digitalocean", l: "DigitalOcean Token" }, { v: "cloudflare", l: "Cloudflare Token" },
  { v: "datacheap", l: "Datacheap" }, { v: "generic", l: "Прочее" },
];
const kindLabel = (k: string) => KINDS.find(x => x.v === k)?.l ?? k;

export function InfraApiTokens() {
  const [rows, setRows] = useState<ApiToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [locked, setLocked] = useState(false);
  const [adding, setAdding] = useState(false);
  const [verifying, setVerifying] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setLocked(false);
    try { setRows(await infraApi.listTokens()); }
    catch (e) {
      if ((e as { status?: number }).status === 401) setLocked(true);
      else toast((e as Error).message, "error");
    }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const del = async (t: ApiToken) => {
    if (!confirm(`Удалить ключ «${t.name}»?`)) return;
    try { await infraApi.deleteToken(t.id); toast("Ключ удалён", "success"); load(); }
    catch (e) { toast((e as Error).message, "error"); }
  };

  const verify = async (t: ApiToken) => {
    setVerifying(t.id);
    try {
      const r = await infraApi.verifyToken(t.id);
      toast(r.detail, r.verifiedAgainstProvider ? "success" : "info");
    } catch (e) { toast((e as Error).message, "error"); }
    setVerifying(null);
  };

  if (locked) return (
    <Page>
      <PageHeader icon={<KeyRound size={16} className="text-blue-400" />} title="API токены" />
      <div className="rounded-xl border border-amber-900/40 bg-amber-950/30 p-8 text-center text-amber-300 text-sm flex flex-col items-center gap-2">
        <Lock size={20} /> Хранилище защищено. Войдите в финансовый контур во вкладке «Sign-in».
      </div>
    </Page>
  );

  return (
    <Page>
      <PageHeader icon={<KeyRound size={16} className="text-blue-400" />} title="API токены хостингов"
        subtitle="Зашифрованное хранилище ключей интеграции"
        actions={<>
          <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
          <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white"><Plus size={13} /> Ключ</button>
        </>} />

      <div className="mb-4 flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-800 bg-gray-900/40 text-[11px] text-gray-500">
        <ShieldCheck size={13} className="text-green-500" /> Секреты шифруются (Fernet) и никогда не возвращаются на фронтенд — показывается только маска.
      </div>

      <div className="rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
            <tr>
              <th className="text-left font-medium px-4 py-2.5">Название</th>
              <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
              <th className="text-left font-medium px-4 py-2.5">Ключ</th>
              <th className="text-left font-medium px-4 py-2.5">Добавлен</th>
              <th className="text-right font-medium px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {loading ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600"><Loader2 size={16} className="animate-spin inline" /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-600 text-xs">Ключей нет.</td></tr>
            ) : rows.map(t => (
              <tr key={t.id} className="hover:bg-gray-900/40">
                <td className="px-4 py-2.5 text-gray-200">{t.name}</td>
                <td className="px-4 py-2.5 text-gray-400">{kindLabel(t.providerKind)}</td>
                <td className="px-4 py-2.5 font-mono text-gray-500 text-xs">{t.masked}</td>
                <td className="px-4 py-2.5 text-gray-500 tabular-nums">{fmtDate(t.createdAt)}</td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  <button onClick={() => verify(t)} disabled={verifying === t.id} title="Проверить соединение"
                    className="p-1.5 text-gray-500 hover:text-blue-400 disabled:opacity-50">
                    {verifying === t.id ? <Loader2 size={13} className="animate-spin" /> : <Plug size={13} />}
                  </button>
                  <button onClick={() => del(t)} className="p-1.5 text-gray-500 hover:text-red-400"><Trash2 size={13} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {adding && <TokenModal onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />}
    </Page>
  );
}

function TokenModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("selectel");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim()) { toast("Укажите название связки", "error"); return; }
    if (!secret.trim()) { toast("Введите Secret Key", "error"); return; }
    setSaving(true);
    try { await infraApi.createToken({ name: name.trim(), provider_kind: kind, secret }); toast("Ключ сохранён (зашифрован)", "success"); onSaved(); }
    catch (e) { toast((e as Error).message, "error"); setSaving(false); }
  };

  return (
    <Modal title="Новый API ключ" onClose={onClose}
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-gray-400 hover:text-gray-200">Отмена</button>
        <button onClick={submit} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
          {saving && <Loader2 size={13} className="animate-spin" />} Сохранить
        </button>
      </>}>
      <Field label="Название связки" value={name} onChange={setName} placeholder="Selectel прод" />
      <SelectField label="Провайдер" value={kind} onChange={setKind} options={KINDS} />
      <Field label="Secret Key" value={secret} onChange={setSecret} type="password" placeholder="вставьте токен" />
      <p className="text-[11px] text-gray-600">Секрет будет зашифрован на сервере и больше не отобразится в открытом виде.</p>
    </Modal>
  );
}
