import { useState, useEffect, useCallback } from "react";
import { ShieldCheck, Network, Loader2, Server, Rocket, KeyRound, Copy } from "lucide-react";
import { TerminalOutput } from "../TerminalOutput";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";
import { toast } from "../infra/Toast";

// Wave-4 Plans D+F (E6/E8) — Certwarden (centralised ACME) + Netbird (self-hosted
// mesh) management. Deploy server/control-plane, install client / join agent.
// SSH creds are per-request (never persisted); the Netbird PAT is stored
// Fernet-encrypted server-side and never returned.

function useStream() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<TaskStatus>("pending");
  const addLog = useCallback((l: string) => setLogs(p => [...p, l]), []);
  const onStatus = useCallback((f: StatusFrame) => setStatus(f.status), []);
  useTaskStream({ taskId, onLog: addLog, onStatus });
  const start = useCallback(async (url: string, body: unknown) => {
    setLogs([]); setStatus("running"); setTaskId(null);
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        setLogs([`\x1b[31m[HTTP ${res.status}] ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}\x1b[0m`]);
        setStatus("failed"); return;
      }
      const { task_id } = await res.json(); setTaskId(task_id);
    } catch (e) { setLogs([`\x1b[31m${(e as Error).message}\x1b[0m`]); setStatus("failed"); }
  }, []);
  const running = status === "running" && !!taskId;
  return { logs, status, start, running };
}

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

function SshFields({ v, set }: { v: Record<string, string>; set: (k: string, val: string) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2.5">
      <Fld label="IP" value={v.ip} onChange={x => set("ip", x)} placeholder="1.2.3.4" />
      <Fld label="SSH логин" value={v.ssh_user} onChange={x => set("ssh_user", x)} placeholder="root" />
      <Fld label="SSH пароль" value={v.ssh_password} onChange={x => set("ssh_password", x)} type="password" />
      <Fld label="SSH порт" value={v.ssh_port} onChange={x => set("ssh_port", x)} placeholder="22" />
    </div>
  );
}

function Term({ logs, status }: { logs: string[]; status: TaskStatus }) {
  if (!logs.length && status === "pending") return null;
  return <div style={{ minHeight: 160 }} className="mt-1"><TerminalOutput lines={logs} /></div>;
}

const card = "rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] p-4 flex flex-col gap-3";
const btn = "flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50";
const useSshState = () => useState({ ip: "", ssh_user: "root", ssh_password: "", ssh_port: "22" });
const sshBody = (v: Record<string, string>) =>
  ({ ip: v.ip.trim(), ssh_user: v.ssh_user.trim() || "root", ssh_password: v.ssh_password, ssh_port: parseInt(v.ssh_port, 10) || 22 });

// ── Certwarden ────────────────────────────────────────────────
function CertwardenPanel() {
  const [server, setServer] = useState<{ base_url?: string; placement?: string } | null>(null);
  const load = useCallback(() => {
    fetch("/api/certwarden/server").then(r => r.json()).then(setServer).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const [srvSsh, setSrvSsh] = useSshState();
  const [placement, setPlacement] = useState("dedicated");
  const [serverUrl, setServerUrl] = useState("");
  const [domain, setDomain] = useState("");
  const srvStream = useStream();

  const deployServer = () => {
    if (!srvSsh.ip.trim() || !srvSsh.ssh_password) { toast("Укажите IP и SSH пароль", "error"); return; }
    if (!/^https?:\/\//.test(serverUrl)) { toast("server_url должен быть http(s) URL", "error"); return; }
    srvStream.start("/api/certwarden/server/deploy", { ...sshBody(srvSsh), placement, server_url: serverUrl.trim(), domain: domain.trim() });
  };

  const [cl, setCl] = useSshState();
  const [clUrl, setClUrl] = useState("");
  const [clDomain, setClDomain] = useState("");
  const [certName, setCertName] = useState("");
  const [keyName, setKeyName] = useState("");
  const [certKey, setCertKey] = useState("");
  const [keyKey, setKeyKey] = useState("");
  const clStream = useStream();

  const installClient = () => {
    if (!cl.ip.trim() || !cl.ssh_password) { toast("Укажите IP и SSH пароль ноды", "error"); return; }
    clStream.start("/api/certwarden/client/install", {
      ...sshBody(cl), server_url: clUrl.trim(), domain: clDomain.trim(),
      cert_name: certName.trim(), key_name: keyName.trim(), cert_apikey: certKey.trim(), key_apikey: keyKey.trim(),
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <ShieldCheck size={15} className="text-[var(--accent)]" />
        <h3 className="text-sm font-semibold text-[var(--t-hi)]">Certwarden — централизованный ACME</h3>
      </div>
      <p className="text-xs text-[var(--t-low)]">
        {server?.base_url
          ? <>Сервер: <span className="text-[var(--t-mid)]">{server.base_url}</span> ({server.placement})</>
          : "Сервер не развёрнут."}
      </p>

      <div className={card}>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest"><Server size={12} /> Развернуть сервер</div>
        <SshFields v={srvSsh} set={(k, val) => setSrvSsh(s => ({ ...s, [k]: val }))} />
        <div className="grid grid-cols-2 gap-2.5">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>Placement</label>
            <select value={placement} onChange={e => setPlacement(e.target.value)} className="selectbox">
              <option value="dedicated">Выделенный сервер</option>
              <option value="panel">На боксе панели</option>
            </select>
          </div>
          <Fld label="URL сервера (UI)" value={serverUrl} onChange={setServerUrl} placeholder="https://cw.example.com" />
        </div>
        <Fld label="Домен сервера (опц.)" value={domain} onChange={setDomain} placeholder="cw.example.com" />
        <button onClick={deployServer} disabled={srvStream.running} className={btn + " self-start"}>
          {srvStream.running ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />} Развернуть
        </button>
        <Term logs={srvStream.logs} status={srvStream.status} />
      </div>

      <div className={card}>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest"><KeyRound size={12} /> Установить клиент на ноду</div>
        <SshFields v={cl} set={(k, val) => setCl(s => ({ ...s, [k]: val }))} />
        <div className="grid grid-cols-2 gap-2.5">
          <Fld label="URL сервера" value={clUrl} onChange={setClUrl} placeholder="https://cw.example.com" />
          <Fld label="Домен ноды" value={clDomain} onChange={setClDomain} placeholder="node1.example.com" />
          <Fld label="Cert name" value={certName} onChange={setCertName} placeholder="cert-node1" />
          <Fld label="Key name" value={keyName} onChange={setKeyName} placeholder="key-node1" />
          <Fld label="Cert API key" value={certKey} onChange={setCertKey} type="password" />
          <Fld label="Key API key" value={keyKey} onChange={setKeyKey} type="password" />
        </div>
        <button onClick={installClient} disabled={clStream.running} className={btn + " self-start"}>
          {clStream.running ? <Loader2 size={13} className="animate-spin" /> : <KeyRound size={13} />} Установить клиент
        </button>
        <Term logs={clStream.logs} status={clStream.status} />
      </div>
    </div>
  );
}

// ── Netbird ───────────────────────────────────────────────────
function NetbirdPanel() {
  const [cp, setCp] = useState<{ domain?: string; management_url?: string; has_pat?: boolean } | null>(null);
  const load = useCallback(() => {
    fetch("/api/netbird/control-plane").then(r => r.json()).then(setCp).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const [cpSsh, setCpSsh] = useSshState();
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const cpStream = useStream();
  const deployCp = () => {
    if (!cpSsh.ip.trim() || !cpSsh.ssh_password) { toast("Укажите IP и SSH пароль", "error"); return; }
    if (!domain.trim()) { toast("Нужен публичный домен", "error"); return; }
    cpStream.start("/api/netbird/control-plane/deploy", { ...sshBody(cpSsh), domain: domain.trim(), email: email.trim() });
  };

  const [pat, setPat] = useState("");
  const savePat = async () => {
    try {
      const res = await fetch("/api/netbird/pat", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pat }) });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Ошибка"); }
      toast("PAT сохранён (зашифрован)", "success"); setPat(""); load();
    } catch (e) { toast((e as Error).message, "error"); }
  };

  const [setupKey, setSetupKey] = useState("");
  const createKey = async () => {
    try {
      const res = await fetch("/api/netbird/setup-key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка");
      setSetupKey(d.key); toast("Setup-key создан", "success");
    } catch (e) { toast((e as Error).message, "error"); }
  };

  const [jn, setJn] = useSshState();
  const [joinKey, setJoinKey] = useState("");
  const joinStream = useStream();
  const joinNode = () => {
    if (!jn.ip.trim() || !jn.ssh_password) { toast("Укажите IP и SSH пароль ноды", "error"); return; }
    if (!joinKey.trim()) { toast("Нужен setup-key", "error"); return; }
    joinStream.start("/api/netbird/agent/join", { ...sshBody(jn), setup_key: joinKey.trim() });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Network size={15} className="text-[var(--accent)]" />
        <h3 className="text-sm font-semibold text-[var(--t-hi)]">Netbird — self-hosted mesh</h3>
      </div>
      <p className="text-xs text-[var(--t-low)]">
        {cp?.domain
          ? <>Control plane: <span className="text-[var(--t-mid)]">{cp.management_url}</span> · PAT: {cp.has_pat ? "сохранён" : "нет"}</>
          : "Control plane не развёрнут."}
      </p>

      <div className={card}>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest"><Server size={12} /> Развернуть control plane</div>
        <SshFields v={cpSsh} set={(k, val) => setCpSsh(s => ({ ...s, [k]: val }))} />
        <div className="grid grid-cols-2 gap-2.5">
          <Fld label="Домен (публичный FQDN)" value={domain} onChange={setDomain} placeholder="nb.example.com" />
          <Fld label="Email (Let's Encrypt)" value={email} onChange={setEmail} placeholder="you@example.com" />
        </div>
        <button onClick={deployCp} disabled={cpStream.running} className={btn + " self-start"}>
          {cpStream.running ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />} Развернуть
        </button>
        <Term logs={cpStream.logs} status={cpStream.status} />
      </div>

      <div className={card}>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest"><KeyRound size={12} /> Service-user PAT + setup-key</div>
        <p className="text-[11px] text-[var(--t-faint)]">Создайте PAT в Dashboard Netbird (service user), сохраните его здесь — хранится зашифрованным.</p>
        <div className="flex items-end gap-2">
          <div className="flex-1"><Fld label="PAT" value={pat} onChange={setPat} type="password" placeholder="nbp_..." /></div>
          <button onClick={savePat} disabled={pat.length < 8} className={btn}>Сохранить</button>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={createKey} disabled={!cp?.has_pat} className={btn}>Создать setup-key</button>
          {setupKey && (
            <div className="flex items-center gap-1.5 text-[11px] text-[var(--t-mid)] bg-[var(--bg2)] border border-[var(--line-soft)] rounded px-2 py-1 font-mono truncate max-w-[16rem]">
              <span className="truncate">{setupKey}</span>
              <button onClick={() => { navigator.clipboard?.writeText(setupKey); toast("Скопировано", "info"); }} className="shrink-0 text-[var(--t-low)] hover:text-[var(--accent-hi)]"><Copy size={12} /></button>
            </div>
          )}
        </div>
      </div>

      <div className={card}>
        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-[var(--t-low)] uppercase tracking-widest"><Network size={12} /> Подключить ноду в mesh</div>
        <SshFields v={jn} set={(k, val) => setJn(s => ({ ...s, [k]: val }))} />
        <Fld label="Setup-key" value={joinKey} onChange={setJoinKey} type="password" placeholder="из «Создать setup-key»" />
        <button onClick={joinNode} disabled={joinStream.running} className={btn + " self-start"}>
          {joinStream.running ? <Loader2 size={13} className="animate-spin" /> : <Network size={13} />} Подключить (SSH сохранится)
        </button>
        <Term logs={joinStream.logs} status={joinStream.status} />
      </div>
    </div>
  );
}

export function InfraTab() {
  return (
    <div className="flex flex-col gap-8 max-w-2xl">
      <CertwardenPanel />
      <div className="h-px bg-[var(--line-soft)]" />
      <NetbirdPanel />
    </div>
  );
}
