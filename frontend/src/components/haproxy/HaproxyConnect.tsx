import { useCallback, useEffect, useState } from "react";
import { Plug, ShieldAlert, CheckCircle2, XCircle, Loader2, ServerCog } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi, type HaproxyTestResult } from "./api";
import type { HaproxyConnState } from "./contracts";

// ── shared readiness hook + gate ────────────────────────────────
// Every HAPROXY page mounts this to decide whether to show its content or a
// «connect first» prompt. One GET /api/haproxy/config, cheap.
export function useHaproxyReady() {
  const [state, setState] = useState<HaproxyConnState | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try { setState(await haproxyApi.getConfig()); }
    catch { setState(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { state, ready: !!state?.configured && !!state?.enabled, loading, reload };
}

export function NotConnected({ loading }: { loading?: boolean }) {
  return (
    <div className="card p-8 text-center max-w-md mx-auto mt-10">
      {loading ? (
        <Loader2 size={26} className="animate-spin mx-auto text-[var(--t-low)]" />
      ) : (
        <>
          <ServerCog size={30} className="mx-auto text-[var(--t-low)] mb-3" />
          <p className="text-sm font-medium text-[var(--t-hi)] mb-1">Панель NodeFlow не подключена</p>
          <p className="text-xs text-[var(--t-low)]">
            Откройте раздел <b>«Настройки»</b> группы <b>HAPROXY</b>, укажите URL панели и
            admin-токен, затем включите интеграцию.
          </p>
        </>
      )}
    </div>
  );
}

// ── the «Настройки» / connect page ──────────────────────────────
export function HaproxyConnect() {
  const [cfg, setCfg] = useState<HaproxyConnState | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [test, setTest] = useState<HaproxyTestResult | null>(null);

  const load = useCallback(async () => {
    try {
      const c = await haproxyApi.getConfig();
      setCfg(c);
      setBaseUrl(c.base_url);
      setEnabled(c.enabled);
    } catch (e: any) { setErr(e?.message || "Ошибка загрузки"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setBusy(true); setErr(""); setTest(null);
    try {
      await haproxyApi.saveConfig({ enabled, base_url: baseUrl.trim(), admin_token: token.trim() });
      setToken("");
      await load();
    } catch (e: any) { setErr(e?.message || "Не удалось сохранить"); }
    finally { setBusy(false); }
  };

  const runTest = async () => {
    setBusy(true); setErr(""); setTest(null);
    try { setTest(await haproxyApi.test()); }
    catch (e: any) { setErr(e?.message || "Проверка не удалась"); }
    finally { setBusy(false); }
  };

  return (
    <Page>
      <PageHeader
        icon={<Plug size={18} />}
        title="Подключение к NodeFlow"
        subtitle="HAProxy-панель: node-installer проксирует её функции в разделы группы HAPROXY"
      />

      <div className="card p-5 flex flex-col gap-4 max-w-xl">
        <div className="flex flex-col gap-1">
          <label className="label">URL панели</label>
          <input className="input" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
            placeholder="https://haproxy.example.com" spellCheck={false} autoCapitalize="off" />
          <p className="text-[11px] text-[var(--t-low)]">
            Публичный http(s)-адрес панели NodeFlow (порт 443 за reverse-proxy).
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label className="label">Admin-токен (PANEL_ADMIN_TOKEN)</label>
          <input className="input" type="password" value={token} onChange={e => setToken(e.target.value)}
            placeholder={cfg?.has_token ? "•••••• (сохранён — оставьте пустым, чтобы не менять)" : "вставьте PANEL_ADMIN_TOKEN"}
            spellCheck={false} autoComplete="off" />
          <p className="text-[11px] text-[var(--t-low)] flex items-center gap-1">
            <ShieldAlert size={12} /> Хранится в зашифрованном виде (Fernet), не возвращается обратно.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          Включить интеграцию (показывать данные панели в разделах HAPROXY)
        </label>

        {err && <p className="text-xs text-[var(--err)]">{err}</p>}

        {test && (
          <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-3 flex flex-col gap-1.5 text-xs">
            <Row ok={test.reachable} label="Панель доступна" />
            <Row ok={test.authenticated} label="Токен принят" />
            {test.version && <p className="text-[var(--t-low)]">Версия: {test.version}</p>}
            {test.detail && !test.authenticated && <p className="text-[var(--warn)]">{test.detail}</p>}
          </div>
        )}

        <div className="flex items-center gap-2">
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
          </button>
          <button className="btn btn-soft" onClick={runTest} disabled={busy || !cfg?.configured}>
            Проверить соединение
          </button>
          {cfg?.configured && (
            <span className="chip accent ml-auto">
              {cfg.enabled ? "включено" : "настроено, выключено"}
            </span>
          )}
        </div>
      </div>

      <div className="mt-5 max-w-xl text-xs text-[var(--t-low)] leading-relaxed">
        <p className="font-medium text-[var(--t-mid)] mb-1">Как получить панель NodeFlow</p>
        Разверните NodeFlow из install-kit на отдельном сервере (панель + Postgres + агент по mTLS),
        затем впишите её URL и <code>PANEL_ADMIN_TOKEN</code> из <code>/opt/nodeflow/.env</code>. После
        подключения разделы «Обзор», «Ноды», «Маршруты», «Трафик», «Файрвол» и «Релизы» управляют
        реальным HAProxy-движком NodeFlow.
      </div>
    </Page>
  );
}

function Row({ ok, label }: { ok: boolean; label: string }) {
  return (
    <p className="flex items-center gap-1.5" style={{ color: ok ? "var(--ok)" : "var(--err)" }}>
      {ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />} {label}
    </p>
  );
}
