// Настройки → «Cloudflare»: подключение аккаунта (Wave-9 Plan B Ф1).
// Lives in Settings, not in the nav group — same split as HAProxy, so the group
// itself stays purely operational.
import { useEffect, useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { toast } from "../infra/Toast";
import {
  getConfig, saveConfig, testConnection, messageOf, type CfAccount,
} from "./api";

export function CfConnect() {
  const [enabled, setEnabled] = useState(false);
  const [accountId, setAccountId] = useState("");
  const [token, setToken] = useState("");
  const [reveal, setReveal] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [accounts, setAccounts] = useState<CfAccount[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    getConfig()
      .then(c => { setEnabled(c.enabled); setAccountId(c.account_id); setHasToken(c.has_token); })
      .catch(e => setErr(messageOf(e)));
  }, []);

  const save = async () => {
    setBusy(true); setErr("");
    try {
      // Blank token = keep the stored one (it never comes back to the browser).
      const cfg = await saveConfig({
        enabled, account_id: accountId.trim(),
        ...(token.trim() ? { api_token: token.trim() } : {}),
      });
      setHasToken(cfg.has_token);
      setToken("");
      toast("Настройки Cloudflare сохранены", "success");
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true); setErr("");
    try {
      const r = await testConnection();
      setAccounts(r.accounts || []);
      if (r.ok) toast("Соединение с Cloudflare работает", "success");
      else setErr(r.error || "Проверка не удалась");
    } catch (e) {
      setErr(messageOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 560 }}>
      <label className="flex items-center gap-2 text-sm" style={{ color: "var(--t-hi)" }}>
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        Использовать Cloudflare (биллинг и домены)
      </label>

      <label className="flex flex-col gap-1">
        <span className="micro">API-токен</span>
        <div className="flex gap-2">
          <input className="input" type={reveal ? "text" : "password"}
            value={token} onChange={e => setToken(e.target.value)}
            placeholder={hasToken ? "сохранён — оставьте пустым, чтобы не менять" : "cf_..."} />
          <button className="btn" type="button" title={reveal ? "Скрыть" : "Показать"}
            onClick={() => setReveal(v => !v)}>
            {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
        <span className="micro" style={{ color: "var(--t-low)" }}>
          Права токена: <b>Billing → Read</b> для балансов и подписок, <b>Registrar</b> —
          для списка и покупки доменов. Это отдельный токен от DNS-токена в «Деплой-настройках».
        </span>
      </label>

      <label className="flex flex-col gap-1">
        <span className="micro">Аккаунт Cloudflare</span>
        {accounts.length > 0 ? (
          <select className="input" value={accountId} onChange={e => setAccountId(e.target.value)}>
            <option value="">— выберите —</option>
            {accounts.map(a => (
              <option key={a.id} value={a.id}>{a.name ? `${a.name} (${a.id})` : a.id}</option>
            ))}
          </select>
        ) : (
          <input className="input" value={accountId} onChange={e => setAccountId(e.target.value)}
            placeholder="account_id — или нажмите «Проверить», чтобы получить список" />
        )}
      </label>

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}

      <div className="flex gap-2">
        <button className="btn btn-primary" disabled={busy} onClick={save}>Сохранить</button>
        <button className="btn" disabled={busy} onClick={test}>Проверить</button>
      </div>
    </div>
  );
}
