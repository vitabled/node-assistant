// Вход в провайдеров LLM через CLIProxyAPI по OAuth — вместо API-ключа.
//
// Шлюз — ОБЩАЯ инфраструктура на инсталляцию (как xray-checker): кто включил, тот
// и управляет пулом аккаунтов; остальные могут им пользоваться, но не менять.
// Бэкенд это и enforce'ит (403), здесь мы только честно показываем.
//
// Headless-флоу провайдера: получаем ссылку → человек входит в свой аккаунт →
// его редиректит на несуществующий loopback → он копирует URL из адресной строки
// целиком → мы отдаём его шлюзу. Kimi — device-flow: URL сам по себе и есть
// подтверждение, копировать нечего.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  KeyRound, Play, Square, RefreshCw, Trash2, ExternalLink, Loader2, AlertTriangle,
} from "lucide-react";
import { toast } from "../infra/Toast";

interface GatewayConfig {
  enabled: boolean;
  image: string;
  container: string;
  owner_is_me: boolean;
  base_url: string;
  has_keys: boolean;
  master_key?: string;
}

interface ProviderAccount {
  name: string;
  provider: string;
  label: string;
  status: string | null;
  disabled: boolean;
  unavailable: boolean;
  last_refresh: string | null;
}

// Только те, у кого шлюз реально умеет OAuth (см. cliproxy_management.AUTH_URLS).
const PROVIDERS: { id: string; label: string; device?: boolean }[] = [
  { id: "anthropic", label: "Anthropic (Claude)" },
  { id: "codex", label: "OpenAI (Codex)" },
  { id: "xai", label: "xAI (Grok)" },
  { id: "antigravity", label: "Google (Antigravity)" },
  { id: "kimi", label: "Kimi", device: true },
];

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/cliproxy${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* не-JSON от прокси */ }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : `Ошибка ${res.status}`);
  }
  return body as T;
}

const msg = (e: unknown) => (e instanceof Error ? e.message : "Неизвестная ошибка");

export function CliProxyAuth() {
  const [cfg, setCfg] = useState<GatewayConfig | null>(null);
  const [accounts, setAccounts] = useState<ProviderAccount[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const [provider, setProvider] = useState(PROVIDERS[0].id);
  const [login, setLogin] = useState<{ url: string; state: string; device: boolean } | null>(null);
  const [redirectUrl, setRedirectUrl] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [confirmDel, setConfirmDel] = useState("");
  const poll = useRef<number | null>(null);

  const loadCfg = useCallback(() => {
    call<GatewayConfig>("/config")
      .then(c => { setCfg(c); setErr(""); })
      .catch(e => setErr(msg(e)));
  }, []);

  const loadAccounts = useCallback(() => {
    // Аккаунты доступны только когда шлюз поднят — иначе Management API молчит,
    // и пустой список тут ничего не значит.
    call<{ accounts: ProviderAccount[] }>("/accounts")
      .then(r => setAccounts(r.accounts || []))
      .catch(() => setAccounts([]));
  }, []);

  useEffect(() => { loadCfg(); }, [loadCfg]);
  useEffect(() => { if (cfg?.container === "running") loadAccounts(); }, [cfg?.container, loadAccounts]);
  useEffect(() => () => { if (poll.current) window.clearInterval(poll.current); }, []);

  const toggleGateway = async (enabled: boolean) => {
    setBusy(true);
    try {
      const r = await call<{ ok: boolean; warning?: string }>("/config", {
        method: "POST",
        body: JSON.stringify({ enabled, image: cfg?.image || "" }),
      });
      if (r.warning) toast(r.warning, "info");
      loadCfg();
    } catch (e) { toast(msg(e), "error"); } finally { setBusy(false); }
  };

  const lifecycle = async (what: "start" | "stop") => {
    setBusy(true);
    try {
      const r = await call<{ ok: boolean; warning?: string }>(`/${what}`, { method: "POST" });
      if (r.warning) toast(r.warning, "info");
      loadCfg();
    } catch (e) { toast(msg(e), "error"); } finally { setBusy(false); }
  };

  const startLogin = async () => {
    setBusy(true); setRedirectUrl("");
    try {
      const def = PROVIDERS.find(p => p.id === provider);
      const r = await call<{ url: string; state: string }>("/oauth/start", {
        method: "POST", body: JSON.stringify({ provider }),
      });
      setLogin({ url: r.url, state: r.state, device: !!def?.device });
      window.open(r.url, "_blank", "noopener,noreferrer");
      if (def?.device) watch(r.state);   // device-flow: ждём подтверждения сразу
    } catch (e) { toast(msg(e), "error"); } finally { setBusy(false); }
  };

  const watch = (state: string) => {
    setWaiting(true);
    if (poll.current) window.clearInterval(poll.current);
    const started = Date.now();
    poll.current = window.setInterval(async () => {
      // Шлюз держит ожидание колбэка 5 минут — дольше поллить бессмысленно.
      if (Date.now() - started > 5 * 60_000) {
        window.clearInterval(poll.current!);
        setWaiting(false);
        toast("Время ожидания входа истекло — начните заново", "error");
        return;
      }
      try {
        const r = await call<{ status: string; error?: string }>(
          `/oauth/status?state=${encodeURIComponent(state)}`);
        if (r.status === "ok") {
          window.clearInterval(poll.current!);
          setWaiting(false); setLogin(null); setRedirectUrl("");
          toast("Аккаунт подключён", "success");
          loadAccounts();
        } else if (r.status === "error") {
          window.clearInterval(poll.current!);
          setWaiting(false);
          toast(r.error || "Шлюз отклонил вход", "error");
        }
      } catch {
        // Разовый сбой опроса — не повод бросать ожидание.
      }
    }, 2000);
  };

  const finishLogin = async () => {
    if (!login) return;
    setBusy(true);
    try {
      await call("/oauth/callback", {
        method: "POST",
        body: JSON.stringify({ state: login.state, redirect_url: redirectUrl.trim() }),
      });
      watch(login.state);
    } catch (e) { toast(msg(e), "error"); } finally { setBusy(false); }
  };

  const setDisabled = async (a: ProviderAccount, disabled: boolean) => {
    try {
      await call(`/accounts/${encodeURIComponent(a.name)}`, {
        method: "PATCH", body: JSON.stringify({ disabled }),
      });
      loadAccounts();
    } catch (e) { toast(msg(e), "error"); }
  };

  const remove = async (a: ProviderAccount) => {
    if (confirmDel !== a.name) { setConfirmDel(a.name); return; }
    try {
      await call(`/accounts/${encodeURIComponent(a.name)}`, { method: "DELETE" });
      setConfirmDel("");
      loadAccounts();
    } catch (e) { toast(msg(e), "error"); }
  };

  const running = cfg?.container === "running";
  const owner = cfg?.owner_is_me !== false;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <KeyRound size={15} style={{ color: "var(--accent)" }} />
        <h3 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
          Вход через CLIProxyAPI (OAuth)
        </h3>
      </div>
      <p className="micro" style={{ color: "var(--t-low)" }}>
        Шлюз ходит к провайдеру не по API-ключу, а под вашим аккаунтом: хранит refresh-токен,
        сам его обновляет и раскидывает запросы по пулу аккаунтов. Чтобы ассистент этим
        пользовался, выберите выше шлюз «CLIProxyAPI».
      </p>

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}

      {!owner && (
        <div className="rounded-lg p-3 text-xs"
          style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t-hi)" }}>
          <AlertTriangle size={13} style={{ display: "inline", marginRight: 6, verticalAlign: "-2px" }} />
          Шлюз включил другой аккаунт панели — пользоваться им можно, менять настройки и аккаунты
          провайдеров нельзя. Пул аккаунтов общий: лимиты расходуются совместно.
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <label className="flex items-center gap-2 text-sm" style={{ color: "var(--t-hi)" }}>
          <input type="checkbox" checked={!!cfg?.enabled} disabled={busy || !owner}
            onChange={e => toggleGateway(e.target.checked)} />
          Поднять шлюз
        </label>
        <span className="micro" style={{ color: "var(--t-low)" }}>
          контейнер: {cfg?.container || "…"}
          {cfg?.base_url ? ` · ${cfg.base_url}` : ""}
        </span>
        {owner && (
          <div className="flex gap-1">
            <button className="btn" disabled={busy || running} onClick={() => lifecycle("start")}>
              <Play size={13} /> Запустить
            </button>
            <button className="btn" disabled={busy || !running} onClick={() => lifecycle("stop")}>
              <Square size={13} /> Остановить
            </button>
            <button className="btn" disabled={busy} onClick={() => { loadCfg(); loadAccounts(); }}>
              <RefreshCw size={13} />
            </button>
          </div>
        )}
      </div>

      {!running ? (
        <p className="micro" style={{ color: "var(--t-low)" }}>
          Аккаунты провайдеров появятся, когда контейнер шлюза запустится.
        </p>
      ) : (
        <>
          {owner && (
            <div className="rounded-lg p-3 flex flex-col gap-2"
              style={{ background: "var(--panel)", border: "1px solid var(--line-soft)" }}>
              <p className="micro">Подключить аккаунт провайдера</p>
              <div className="flex gap-2 flex-wrap items-center">
                <select className="selectbox" value={provider} disabled={busy || waiting}
                  onChange={e => { setProvider(e.target.value); setLogin(null); }}>
                  {PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
                <button className="btn btn-primary" disabled={busy || waiting} onClick={startLogin}>
                  {busy ? <Loader2 size={13} className="animate-spin" /> : <ExternalLink size={13} />}
                  Войти
                </button>
              </div>

              {login && (
                <div className="flex flex-col gap-2" style={{ marginTop: 4 }}>
                  <p className="micro" style={{ color: "var(--t-low)" }}>
                    Открылась вкладка входа. Если нет —{" "}
                    <a href={login.url} target="_blank" rel="noreferrer"
                      style={{ color: "var(--accent)", textDecoration: "underline" }}>
                      откройте ссылку вручную
                    </a>.
                  </p>
                  {login.device ? (
                    <p className="micro" style={{ color: "var(--t-low)" }}>
                      Подтвердите вход на открытой странице — здесь ничего вставлять не нужно.
                    </p>
                  ) : (
                    <>
                      <p className="micro" style={{ color: "var(--t-low)" }}>
                        После входа браузер уйдёт на несуществующий адрес — это нормально.
                        Скопируйте URL из адресной строки целиком и вставьте сюда:
                      </p>
                      <div className="flex gap-2">
                        <input className="input" value={redirectUrl} placeholder="http://localhost:1455/?code=…"
                          onChange={e => setRedirectUrl(e.target.value)} disabled={waiting} />
                        <button className="btn btn-primary" disabled={busy || waiting || !redirectUrl.trim()}
                          onClick={finishLogin}>Готово</button>
                      </div>
                    </>
                  )}
                  {waiting && (
                    <p className="micro" style={{ color: "var(--t-low)" }}>
                      <Loader2 size={12} className="animate-spin" style={{ display: "inline", marginRight: 4 }} />
                      Ждём подтверждения от шлюза…
                    </p>
                  )}
                </div>
              )}

              <p className="micro" style={{ color: "var(--t-low)" }}>
                У Gemini входа по OAuth нет — только API-ключ или Vertex. Модели Gemini даёт
                вход Google-аккаунтом через Antigravity.
              </p>
            </div>
          )}

          <div className="flex flex-col gap-2">
            <p className="micro">Подключённые аккаунты ({accounts.length})</p>
            {accounts.length === 0 ? (
              <p className="micro" style={{ color: "var(--t-low)" }}>
                Пока ни одного. Несколько аккаунтов одного провайдера шлюз использует по кругу.
              </p>
            ) : accounts.map(a => (
              <div key={a.name} className="rounded-lg p-2 flex items-center gap-3 flex-wrap"
                style={{ background: "var(--panel)", border: "1px solid var(--line-soft)" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <p className="text-sm" style={{ color: "var(--t-hi)" }}>
                    {a.label || a.name}
                    <span className="micro" style={{ color: "var(--t-low)", marginLeft: 6 }}>
                      {a.provider}
                    </span>
                  </p>
                  <p className="micro" style={{ color: "var(--t-low)" }}>
                    {a.disabled ? "выключен" : a.unavailable ? "недоступен" : (a.status || "активен")}
                    {a.last_refresh ? ` · обновлён ${a.last_refresh}` : ""}
                  </p>
                </div>
                {owner && (
                  <div className="flex gap-1 flex-none">
                    <button className="btn" onClick={() => setDisabled(a, !a.disabled)}>
                      {a.disabled ? "Включить" : "Выключить"}
                    </button>
                    <button className="btn" onClick={() => remove(a)}>
                      <Trash2 size={13} />
                      {confirmDel === a.name && <span style={{ marginLeft: 4 }}>ещё раз?</span>}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
