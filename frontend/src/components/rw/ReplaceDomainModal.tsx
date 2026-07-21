import { useState, useCallback } from "react";
import { X, Loader2, CheckCircle2, XCircle, ArrowLeftRight, AlertTriangle } from "lucide-react";
import { TerminalOutput } from "../TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";

// Wave-4 Plan E (E7) — domain-replacement wizard (node + panel). Self-contained:
// collects the new domain(s) + cert provider, double-confirms (it rewrites prod
// config + restarts the stack), POSTs to /api/replace-domain/{node|panel} and
// streams the task into an inline terminal.

interface Creds { ip: string; ssh_user: string; ssh_password: string; ssh_port: number }

type Props =
  | { mode: "node"; creds: Creds; currentDomain: string; onClose: () => void }
  | {
      mode: "panel"; creds: Creds; currentPanelDomain: string; currentSubDomain: string;
      reverseProxy: "caddy" | "nginx"; onClose: () => void;
    };

const DOMAIN = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;
const PROVIDERS = [
  { v: "cloudflare", l: "Cloudflare (DNS-01)" },
  { v: "letsencrypt", l: "Let's Encrypt (HTTP-01)" },
  { v: "zerossl", l: "ZeroSSL (HTTP-01)" },
];

export function ReplaceDomainModal(props: Props) {
  const { mode, creds, onClose } = props;
  const [newDomain, setNewDomain] = useState("");
  const [newSub, setNewSub] = useState("");
  const [provider, setProvider] = useState("cloudflare");
  const [cfToken, setCfToken] = useState("");
  const [email, setEmail] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // ── task stream ──
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<TaskStatus>("pending");
  const [submitting, setSubmitting] = useState(false);
  const addLog = useCallback((l: string) => setLogs(p => [...p, l]), []);
  const onStatus = useCallback((f: StatusFrame) => setStatus(f.status), []);
  useTaskStream({ taskId, onLog: addLog, onStatus });

  const running = submitting || (status === "running" && !!taskId);
  const done = status === "success" || status === "failed";
  const started = submitting || !!taskId || done;

  const needsCf = provider === "cloudflare";
  const needsEmail = provider !== "cloudflare";

  const validate = (): string | null => {
    if (mode === "node") {
      if (!DOMAIN.test(newDomain.trim())) return "Укажите корректный новый домен";
    } else {
      if (!newDomain.trim() && !newSub.trim()) return "Укажите новый домен панели и/или подписки";
      if (newDomain.trim() && !DOMAIN.test(newDomain.trim())) return "Некорректный домен панели";
      if (newSub.trim() && !DOMAIN.test(newSub.trim())) return "Некорректный домен подписки";
    }
    if (needsCf && !cfToken.trim()) return "Нужен Cloudflare API-токен";
    if (needsEmail && !email.trim()) return "Нужен email для ACME";
    return null;
  };

  const submit = async () => {
    const v = validate();
    if (v) { setErr(v); return; }
    if (!confirm) { setErr("Подтвердите: домен правит прод-конфиг и перезапускает стек"); return; }
    setErr(null); setSubmitting(true); setLogs([]); setStatus("running"); setTaskId(null);
    try {
      let url: string;
      let body: Record<string, unknown>;
      const base = {
        ip: creds.ip, ssh_user: creds.ssh_user, ssh_password: creds.ssh_password, ssh_port: creds.ssh_port,
        cert_provider: provider, email: email.trim(), cf_api_key: cfToken.trim() || null,
      };
      if (props.mode === "node") {
        url = "/api/replace-domain/node";
        body = { ...base, old_domain: props.currentDomain || "", new_domain: newDomain.trim() };
      } else {
        url = "/api/replace-domain/panel";
        body = {
          ...base, reverse_proxy: props.reverseProxy,
          old_panel_domain: props.currentPanelDomain || "", new_panel_domain: newDomain.trim(),
          old_sub_domain: props.currentSubDomain || "", new_sub_domain: newSub.trim(),
        };
      }
      const res = await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        setLogs([`\x1b[31m[HTTP ${res.status}] ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}\x1b[0m`]);
        setStatus("failed");
        return;
      }
      const { task_id } = await res.json();
      setTaskId(task_id);
    } catch (e) {
      setLogs([`\x1b[31m${(e as Error).message}\x1b[0m`]); setStatus("failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4" style={{ background: "var(--overlay)" }}
      onMouseDown={e => { if (e.target === e.currentTarget && !running) onClose(); }}>
      <div className="w-full max-w-lg rounded-xl overflow-hidden flex flex-col max-h-[88vh]"
        style={{ background: "var(--bg1)", border: "1px solid var(--line)" }}>
        <div className="flex items-center gap-2 px-5 py-3.5 shrink-0" style={{ borderBottom: "1px solid var(--line-soft)" }}>
          <ArrowLeftRight size={15} style={{ color: "var(--accent-hi)" }} />
          <h2 className="text-sm font-semibold flex-1" style={{ color: "var(--t-hi)" }}>
            Сменить домен {mode === "node" ? "ноды" : "панели"}
          </h2>
          <button onClick={onClose} disabled={running} className="iconbtn disabled:opacity-40"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          {!started ? (
            <>
              {props.mode === "node" ? (
                <>
                  <Info label="Текущий домен" value={props.currentDomain || "— (определится на сервере)"} />
                  <Field label="Новый домен ноды" value={newDomain} onChange={setNewDomain} placeholder="node2.example.com" />
                </>
              ) : (
                <>
                  <Field label={`Новый домен панели${props.currentPanelDomain ? ` (сейчас ${props.currentPanelDomain})` : ""}`}
                    value={newDomain} onChange={setNewDomain} placeholder="panel2.example.com" />
                  <Field label={`Новый домен подписки${props.currentSubDomain ? ` (сейчас ${props.currentSubDomain})` : ""}`}
                    value={newSub} onChange={setNewSub} placeholder="sub2.example.com (опц.)" />
                  <p className="text-[11px] text-[var(--t-faint)]">Reverse-proxy: {props.reverseProxy} {props.reverseProxy === "caddy" && "(серт авто)"}</p>
                </>
              )}

              <div className="flex flex-col gap-1">
                <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>Провайдер сертификата</label>
                <select value={provider} onChange={e => setProvider(e.target.value)} className="selectbox">
                  {PROVIDERS.map(p => <option key={p.v} value={p.v}>{p.l}</option>)}
                </select>
              </div>
              {needsCf && <Field label="Cloudflare API-токен" value={cfToken} onChange={setCfToken} secret placeholder="DNS:Edit" />}
              {needsEmail && <Field label="Email (ACME)" value={email} onChange={setEmail} placeholder="you@example.com" />}

              <label className="flex items-start gap-2 mt-1 cursor-pointer text-[11px]" style={{ color: "var(--warn)" }}>
                <input type="checkbox" checked={confirm} onChange={() => setConfirm(c => !c)} className="mt-0.5 accent-[var(--accent)]" />
                <span className="flex items-center gap-1"><AlertTriangle size={12} className="shrink-0" />
                  Понимаю: это перевыпустит сертификат, перепишет конфиг и перезапустит стек.</span>
              </label>

              {err && <p className="errmsg">{err}</p>}

              <div className="flex justify-end gap-2 mt-1">
                <button onClick={onClose} className="px-3 py-1.5 rounded-md text-sm text-[var(--t-mid)] hover:text-[var(--t-hi)]">Отмена</button>
                <button onClick={submit} disabled={!confirm}
                  className="flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
                  <ArrowLeftRight size={13} /> Сменить домен
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 text-sm" style={{
                color: status === "success" ? "var(--ok)" : status === "failed" ? "var(--err)" : "var(--accent-hi)",
              }}>
                {status === "running" ? <Loader2 size={15} className="animate-spin" />
                  : status === "success" ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                {status === "running" ? "Выполняется…" : status === "success" ? "Домен сменён" : "Ошибка"}
              </div>
              <div style={{ minHeight: 220 }}><TerminalOutput lines={logs} /></div>
              {done && (
                <div className="flex justify-end">
                  <button onClick={onClose} className="px-4 py-1.5 rounded-md text-sm bg-[var(--bg3)] text-[var(--t-mid)] hover:text-[var(--t-hi)]">Закрыть</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, secret }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; secret?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <input type={secret ? "password" : "text"} value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} autoComplete="off" spellCheck={false} className="input" />
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <div className="text-sm px-2.5 py-1.5 rounded-md bg-[var(--bg2)] border border-[var(--line-soft)] text-[var(--t-mid)]">{value}</div>
    </div>
  );
}
