import { useState, useEffect, useCallback } from "react";
import { Headphones, Save, Loader2, CheckCircle2 } from "lucide-react";
import { bedolagaApi, type BedolagaConfig } from "../bedolaga/api";
import { toast } from "../infra/Toast";

function Fld({ label, value, onChange, type = "text", placeholder }: {
  label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        autoComplete="off" spellCheck={false} className="input" />
    </div>
  );
}

const card = "rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] p-4 flex flex-col gap-3";
const btn = "flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50";

export function BedolagaTab() {
  const [cfg, setCfg] = useState<BedolagaConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [authHeader, setAuthHeader] = useState("X-API-Key");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const c = await bedolagaApi.getConfig();
      setCfg(c);
      setBaseUrl(c.base_url);
      setAuthHeader(c.auth_header || "X-API-Key");
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!baseUrl.trim()) { toast("Укажите URL webapi бедолаги", "error"); return; }
    setSaving(true);
    try {
      await bedolagaApi.saveConfig(baseUrl.trim(), token || undefined, authHeader.trim() || "X-API-Key");
      toast("Настройки Bedolaga сохранены", "success");
      setToken("");
      await load();
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  };

  const test = async () => {
    setTesting(true);
    try {
      const r = await bedolagaApi.testConnection();
      if (r.ok) toast("Соединение установлено ✓", "success");
      else toast(r.error || "Ошибка соединения", "error");
    } catch (e) { toast((e as Error).message, "error"); }
    setTesting(false);
  };

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="flex items-center gap-2">
        <Headphones size={15} className="text-[var(--accent)]" />
        <h3 className="text-sm font-semibold text-[var(--t-hi)]">Bedolaga — саппорт-бот</h3>
      </div>
      <p className="text-xs text-[var(--t-low)]">
        Подключение к Web API бота remnawave-bedolaga-telegram-bot. Сервисный токен — в самом боте:
        <code className="mx-1 px-1 py-0.5 rounded bg-[var(--bg3)] text-[var(--t-mid)]">.env</code> →
        <code className="mx-1 px-1 py-0.5 rounded bg-[var(--bg3)] text-[var(--t-mid)]">WEB_API_DEFAULT_TOKEN</code>,
        либо новый — в самом боте (Web API → Токены). Используется разделами BEDOLAGA → Поддержка.
      </p>

      {loading ? (
        <div className="py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></div>
      ) : (
        <div className={card}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            <Fld label="URL webapi" value={baseUrl} onChange={setBaseUrl} placeholder="https://mydoza.com/api" />
            <Fld label="Заголовок авторизации" value={authHeader} onChange={setAuthHeader} placeholder="X-API-Key" />
          </div>
          <Fld label={cfg?.has_token ? `Токен (сохранён, …${cfg.token_hint.replace("…", "")})` : "Токен"}
            value={token} onChange={setToken} type="password"
            placeholder={cfg?.has_token ? "оставьте пустым, чтобы не менять" : "сервисный токен бота"} />

          <div className="flex items-center gap-2">
            <button onClick={save} disabled={saving} className={btn}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} Сохранить
            </button>
            <button onClick={test} disabled={testing || !cfg?.has_token}
              className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--bg3)] text-[var(--t-mid)] disabled:opacity-50">
              {testing ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />} Проверить соединение
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
