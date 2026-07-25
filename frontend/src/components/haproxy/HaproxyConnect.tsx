import { useCallback, useEffect, useRef, useState } from "react";
import {
  Plug, ShieldAlert, CheckCircle2, XCircle, Loader2, ServerCog, Rocket,
  Square, RefreshCw, AlertTriangle,
} from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { haproxyApi, type HaproxyTestResult } from "./api";
import type { HaproxyConnState, HaproxyLocalStatus } from "./contracts";

// ── shared readiness hook + gate ────────────────────────────────
// Every HAPROXY page mounts this to decide whether to show its content or a
// «connect first» prompt. `ready` = configured (local deployed / remote registered)
// AND enabled. One GET /api/haproxy/config, cheap.
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
          <p className="text-sm font-medium text-[var(--t-hi)] mb-1">Панель NodeFlow не готова</p>
          <p className="text-xs text-[var(--t-low)]">
            Откройте раздел <b>«Настройки»</b> группы <b>HAPROXY</b>: по умолчанию локальная панель
            разворачивается автоматически. Либо подключите существующую панель.
          </p>
        </>
      )}
    </div>
  );
}

// ── the «Настройки» / connect page ──────────────────────────────
export function HaproxyConnect() {
  const [cfg, setCfg] = useState<HaproxyConnState | null>(null);
  const [mode, setMode] = useState<"local" | "remote">("local");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const c = await haproxyApi.getConfig();
      setCfg(c);
      setMode(c.mode);
    } catch (e: any) { setErr(e?.message || "Ошибка загрузки"); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const switchMode = async (m: "local" | "remote") => {
    setMode(m);
    // Persist the mode switch (keeps enabled/base_url; token untouched).
    try {
      await haproxyApi.saveConfig({ enabled: cfg?.enabled ?? (m === "local"), mode: m,
        base_url: cfg?.base_url ?? "", admin_token: "" });
      await load();
    } catch (e: any) { setErr(e?.message || "Не удалось переключить режим"); }
  };

  return (
    <Page>
      <PageHeader
        icon={<Plug size={18} />}
        title="Подключение к NodeFlow"
        subtitle="HAProxy-панель: локальный авто-деплой или существующая панель"
      />

      <div className="seg accent max-w-md mb-5">
        <button className={mode === "local" ? "on" : ""} onClick={() => switchMode("local")}>Локальная (авто)</button>
        <button className={mode === "remote" ? "on" : ""} onClick={() => switchMode("remote")}>Существующая панель</button>
      </div>

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      {mode === "local" ? <LocalPanel initial={cfg?.local} onChange={load} />
        : <RemotePanel cfg={cfg} onChange={load} />}
    </Page>
  );
}

// ── local auto-deploy panel ─────────────────────────────────────
function LocalPanel({ initial, onChange }: { initial?: HaproxyLocalStatus; onChange: () => void }) {
  const [st, setSt] = useState<HaproxyLocalStatus | null>(initial ?? null);
  const [san, setSan] = useState("");
  const [busy, setBusy] = useState(false);
  const [warn, setWarn] = useState("");
  const [err, setErr] = useState("");
  const autoFired = useRef(false);

  const refresh = useCallback(async () => {
    try { setSt(await haproxyApi.localStatus()); } catch {}
  }, []);

  const deploy = useCallback(async () => {
    setBusy(true); setWarn(""); setErr("");
    try {
      const r = await haproxyApi.deployLocal(san.trim());
      setSt(r.local);
      if (r.warning) setWarn(r.warning);
      onChange();
    } catch (e: any) { setErr(e?.message || "Не удалось развернуть"); }
    finally { setBusy(false); }
  }, [san, onChange]);

  const stop = async () => {
    setBusy(true); setErr("");
    try { const r = await haproxyApi.stopLocal(); setSt(r.local); onChange(); }
    catch (e: any) { setErr(e?.message || "Не удалось остановить"); }
    finally { setBusy(false); }
  };

  // Initial status load.
  useEffect(() => { void refresh(); }, [refresh]);

  // Poll while deploying or until reachable (reflect container bring-up).
  useEffect(() => {
    if (!st?.deploying && st?.reachable) return;
    const t = setInterval(() => { void refresh(); }, 3000);
    return () => clearInterval(t);
  }, [st?.deploying, st?.reachable, refresh]);

  // Auto-deploy ONCE by default: never deployed (no token), images present, idle.
  useEffect(() => {
    if (autoFired.current || !st) return;
    if (!st.has_token && st.images_built && !st.deploying && !st.last_error && st.panel !== "no-docker") {
      autoFired.current = true;
      void deploy();
    }
  }, [st, deploy]);

  const panelUp = st?.reachable;
  const dockerMissing = st?.panel === "no-docker";

  return (
    <div className="flex flex-col gap-4 max-w-xl">
      <div className="card p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-[var(--t-hi)] flex items-center gap-1.5"><ServerCog size={15} /> Локальный NodeFlow</p>
            <p className="text-[11px] text-[var(--t-low)] mt-0.5">Разворачивается на этом хосте по Docker-сокету</p>
          </div>
          <span className={`chip ${panelUp ? "ok" : st?.deploying ? "warn" : "neutral"}`}>
            {st?.deploying ? "разворачивается…" : panelUp ? "работает" : dockerMissing ? "нет Docker" : "остановлена"}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          <Stat label="Панель" value={st?.panel ?? "—"} ok={st?.panel === "running"} />
          <Stat label="Postgres" value={st?.postgres ?? "—"} ok={st?.postgres === "running"} />
          <Stat label="Образы собраны" value={st?.images_built ? "да" : "нет"} ok={!!st?.images_built} />
          <Stat label="Доступна" value={st?.reachable ? "да" : "нет"} ok={!!st?.reachable} />
        </div>

        {st?.agent_endpoint && (
          <p className="text-[11px] text-[var(--t-low)]">
            mTLS-эндпоинт агента: <code className="text-[var(--t-mid)]">{st.agent_endpoint}</code>
          </p>
        )}

        {st?.last_error && (
          <div className="rounded-lg border border-[var(--err-line)] bg-[var(--err-dim)] p-2.5 text-xs text-[var(--err)]">
            {st.last_error}
          </div>
        )}
        {warn && (
          <div className="rounded-lg border border-[var(--warn-line)] bg-[var(--warn-dim)] p-2.5 text-xs text-[var(--warn)] flex gap-2">
            <AlertTriangle size={14} className="flex-none mt-0.5" /> {warn}
          </div>
        )}
        {err && <p className="text-xs text-[var(--err)]">{err}</p>}

        <div className="flex flex-col gap-1">
          <label className="label">Публичный адрес хоста для mTLS (необязательно)</label>
          <input className="input" value={san} onChange={e => setSan(e.target.value)}
            placeholder={st?.san_host || "авто по публичному IP"} spellCheck={false} />
        </div>

        <div className="flex items-center gap-2">
          <button className="btn btn-primary" onClick={deploy} disabled={busy || st?.deploying}>
            {busy || st?.deploying ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
            {st?.has_token ? "Переразвернуть" : "Развернуть"}
          </button>
          <button className="btn btn-soft" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} /></button>
          {panelUp && (
            <button className="btn btn-danger ml-auto" onClick={stop} disabled={busy}><Square size={13} /> Остановить</button>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-[var(--warn-line)] bg-[var(--warn-dim)] p-3 text-xs text-[var(--warn)] flex gap-2">
        <ShieldAlert size={14} className="flex-none mt-0.5" />
        <span><b>Требование:</b> этот хост должен быть публичным, порт <b>4200/tcp</b> открыт наружу —
          агенты нод подключаются к нему по mTLS. Порт браузерной панели (8080) наружу не публикуется.
          Если образы не собраны — на хосте выполните <code>docker compose --profile nodeflow-build build nodeflow-panel nodeflow-migrate</code>.</span>
      </div>
    </div>
  );
}

// ── remote (existing panel) form ────────────────────────────────
function RemotePanel({ cfg, onChange }: { cfg: HaproxyConnState | null; onChange: () => void }) {
  const [baseUrl, setBaseUrl] = useState(cfg?.base_url ?? "");
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(cfg?.enabled ?? false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [test, setTest] = useState<HaproxyTestResult | null>(null);

  useEffect(() => { setBaseUrl(cfg?.base_url ?? ""); setEnabled(cfg?.enabled ?? false); }, [cfg?.base_url, cfg?.enabled]);

  const save = async () => {
    setBusy(true); setErr(""); setTest(null);
    try {
      await haproxyApi.saveConfig({ enabled, mode: "remote", base_url: baseUrl.trim(), admin_token: token.trim() });
      setToken("");
      onChange();
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
    <div className="card p-5 flex flex-col gap-4 max-w-xl">
      <div className="flex flex-col gap-1">
        <label className="label">URL панели</label>
        <input className="input" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
          placeholder="https://haproxy.example.com" spellCheck={false} autoCapitalize="off" />
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
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Включить интеграцию
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
        <button className="btn btn-soft" onClick={runTest} disabled={busy || !cfg?.configured}>Проверить соединение</button>
      </div>
    </div>
  );
}

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-[var(--line-soft)] bg-[var(--bg2)] px-2.5 py-1.5">
      <span className="text-[var(--t-low)]">{label}</span>
      <span style={{ color: ok ? "var(--ok)" : "var(--t-mid)" }}>{value}</span>
    </div>
  );
}

function Row({ ok, label }: { ok: boolean; label: string }) {
  return (
    <p className="flex items-center gap-1.5" style={{ color: ok ? "var(--ok)" : "var(--err)" }}>
      {ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />} {label}
    </p>
  );
}
