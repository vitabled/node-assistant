import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, KeyRound, Loader2 } from "lucide-react";

/**
 * «Вход через CLIProxyAPI (OAuth)» (Wave-4 PR-5). Backend-флоу уже есть:
 * POST /api/cliproxy/oauth/start → {url, state} → пользователь проходит
 * авторизацию у провайдера → вставляет redirect URL или code →
 * POST /api/cliproxy/oauth/callback → поллинг GET /oauth/status?state=.
 * Ниже — подключённые аккаунты шлюза (GET /api/cliproxy/accounts).
 */

const OAUTH_PROVIDERS: { id: string; label: string }[] = [
  { id: "claude",      label: "Claude (Anthropic)" },
  { id: "codex",       label: "Codex (OpenAI)" },
  { id: "xai",         label: "xAI (Grok)" },
  { id: "kimi",        label: "Kimi" },
  { id: "antigravity", label: "Antigravity (Google)" },
];

type Phase = "idle" | "link" | "wait" | "done" | "error";

interface Account { name?: string; disabled?: boolean; [k: string]: unknown }

export function CliproxyOAuth() {
  const [provider, setProvider] = useState("claude");
  const [phase, setPhase] = useState<Phase>("idle");
  const [url, setUrl] = useState("");
  const [state, setState] = useState("");
  const [redirect, setRedirect] = useState("");
  const [msg, setMsg] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPoll = () => { if (poll.current) { clearInterval(poll.current); poll.current = null; } };
  useEffect(() => stopPoll, []);

  const loadAccounts = useCallback(() => {
    fetch("/api/cliproxy/accounts")
      .then(r => (r.ok ? r.json() : []))
      .then(d => setAccounts(Array.isArray(d) ? d : (d.accounts ?? [])))
      .catch(() => {});
  }, []);
  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  const start = async () => {
    setMsg(""); setPhase("idle");
    const r = await fetch("/api/cliproxy/oauth/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      setPhase("error");
      setMsg(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
      return;
    }
    setUrl(d.url || "");
    setState(d.state || "");
    setPhase("link");
  };

  const finish = async () => {
    const r = await fetch("/api/cliproxy/oauth/callback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_url: redirect.trim(), state }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) {
      setPhase("error");
      setMsg(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
      return;
    }
    setPhase("wait");
    stopPoll();
    let tries = 0;
    poll.current = setInterval(async () => {
      tries += 1;
      try {
        const r2 = await fetch(`/api/cliproxy/oauth/status?state=${encodeURIComponent(state)}`);
        const d2 = await r2.json().catch(() => ({}));
        if (d2.status === "ok") {
          stopPoll(); setPhase("done"); setMsg(""); loadAccounts();
        } else if (d2.status === "error" || d2.error) {
          stopPoll(); setPhase("error"); setMsg(d2.error || "Ошибка авторизации");
        } else if (tries >= 30) {
          stopPoll(); setPhase("error"); setMsg("Таймаут ожидания подтверждения входа");
        }
      } catch { /* повторим */ }
    }, 2000);
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-3"
      // Фон обязателен: блок лежит на .card (bg2) — без заливки контейнер
      // прозрачный, и рамка «висит в воздухе» (было на скрине).
      style={{ borderColor: "var(--line-soft)", background: "var(--bg1)" }}
      data-testid="cliproxy-oauth">
      <div className="flex items-center gap-2">
        <KeyRound size={14} className="text-[var(--accent-hi)]" />
        <span className="text-xs font-semibold text-[var(--t-hi)]">Вход через CLIProxyAPI (OAuth)</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2 items-end">
        <label className="flex flex-col gap-1">
          <span className="micro">Провайдер</span>
          <select className="selectbox" value={provider} onChange={e => { setProvider(e.target.value); setPhase("idle"); }}>
            {OAUTH_PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>
        <button type="button" className="btn btn-soft" onClick={start}
          disabled={phase === "wait"}>
          {phase === "wait" ? <Loader2 size={13} className="spin" /> : <ExternalLink size={13} />}
          Получить ссылку входа
        </button>
      </div>

      {url && (
        <div className="flex flex-col gap-2">
          <a href={url} target="_blank" rel="noopener noreferrer"
            className="text-[var(--accent-hi)] text-xs inline-flex items-center gap-1 break-all">
            <ExternalLink size={11} className="shrink-0" /> Открыть авторизацию у провайдера
          </a>
          <label className="flex flex-col gap-1">
            <span className="micro">Redirect URL или code из адресной строки</span>
            <div className="flex gap-2">
              <input className="input font-mono text-xs flex-1" value={redirect}
                onChange={e => setRedirect(e.target.value)}
                placeholder="http://localhost:1455/auth/callback?code=…" autoComplete="off" />
              <button type="button" className="btn btn-primary" onClick={finish}
                disabled={!redirect.trim() || phase === "wait"}>
                {phase === "wait" ? <Loader2 size={13} className="spin" /> : null}
                Завершить
              </button>
            </div>
          </label>
        </div>
      )}

      {phase === "wait" && <p className="hint">Ждём подтверждение входа…</p>}
      {phase === "done" && <p className="text-xs text-[var(--ok)]">Вход выполнен — аккаунт подключён к шлюзу.</p>}
      {phase === "error" && <p className="errmsg">{msg}</p>}

      {accounts.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {accounts.map((a, i) => (
            <span key={String(a.name ?? i)} className={`chip ${a.disabled ? "neutral" : "ok"}`}>
              {String(a.name ?? `account-${i + 1}`)}
            </span>
          ))}
        </div>
      )}
      <p className="hint">Требуется запущенный шлюз CLIProxyAPI (Шлюз → CLIProxyAPI, контейнер стартует из этого же раздела).</p>
    </div>
  );
}
