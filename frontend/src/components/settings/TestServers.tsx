import { useState, useEffect, useCallback, useRef } from "react";
import { Gauge, Plus, Trash2, Loader2, TerminalSquare, ChevronDown } from "lucide-react";
import { deployJobsKey } from "../../auth/store";
import { toast } from "../infra/Toast";
import { useTaskStream } from "../../hooks/useTaskStream";

// Ф1 (wave1) — registry of iperf3 test servers («Сервера для тестирования»).
// Register an existing server by IP or provision one over SSH (iperf3 +
// speedtest + xray test tools). SSH credentials are sent per-request and never
// persisted; the deploy allowlist (UFW on the iperf port) = the account's
// successful deploy-node IPs + the backend IP (added server-side).

interface TestServer {
  id: string;
  name: string;
  ip: string;
  iperf_port: number;
  created_at: number;
}

const ADD_INIT = { name: "", ip: "", iperf_port: 5201 };
const SSH_INIT = { name: "", ip: "", ssh_user: "root", ssh_password: "", ssh_port: 22, iperf_port: 5201 };

// IPs of this account's successfully deployed nodes (from the DeployDashboard's
// localStorage) — they get UFW access to the iperf3 port at deploy time.
function collectNodeIps(): string[] {
  try {
    const jobs = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
    const ips = (Array.isArray(jobs) ? jobs : [])
      .filter((j: { finalStatus?: string; ip?: string }) => j.finalStatus === "success" && j.ip)
      .map((j: { ip: string }) => String(j.ip));
    return Array.from(new Set(ips));
  } catch { return []; }
}

const fmtDate = (ts: number) => {
  const d = new Date(ts * 1000);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
};

export function TestServers() {
  const [items,   setItems]   = useState<TestServer[]>([]);
  const [loading, setLoading] = useState(true);

  const [add,    setAdd]    = useState(ADD_INIT);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const [showSsh,   setShowSsh]   = useState(false);
  const [ssh,       setSsh]       = useState(SSH_INIT);
  const [sshErr,    setSshErr]    = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [taskId,    setTaskId]    = useState<string | null>(null);
  const [lastLog,   setLastLog]   = useState("");

  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/testservers").then(r => r.json());
      setItems(Array.isArray(r.servers) ? r.servers : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  // Live-track the SSH-deploy task: surface success/failure as a toast instead
  // of a blind "запущено" (a failed SSH/install would otherwise be invisible).
  // The stream callbacks are captured once per taskId → read the last log line
  // from a ref, not state (state would be a stale closure inside onDone).
  const lastLogRef = useRef("");
  useTaskStream({
    taskId,
    onLog: line => { lastLogRef.current = line; setLastLog(line); },
    onStatus: () => {},
    onDone: (status, error) => {
      setTaskId(null);
      setLastLog("");
      if (status === "success") {
        toast("Тест-сервер установлен и добавлен в реестр", "success");
      } else {
        toast(`Установка не удалась: ${error || lastLogRef.current || "см. логи бэкенда"}`, "error", 8000);
      }
      lastLogRef.current = "";
      load();
    },
  });

  const addByIp = async () => {
    if (!add.ip.trim()) return;
    setAdding(true); setAddErr(null);
    try {
      const res = await fetch("/api/testservers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: add.name.trim(), ip: add.ip.trim(), iperf_port: add.iperf_port }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAddErr(res.status === 422 ? "Некорректный IP-адрес или порт"
          : String(d.detail ?? res.statusText));
      } else { setAdd(ADD_INIT); await load(); }
    } catch (e) { setAddErr(String(e)); }
    setAdding(false);
  };

  const deploy = async () => {
    if (!ssh.ip.trim() || !ssh.ssh_password) { setSshErr("Укажите IP и пароль SSH"); return; }
    setDeploying(true); setSshErr(null);
    try {
      const res = await fetch("/api/testservers/deploy", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...ssh, ip: ssh.ip.trim(), allow_ips: collectNodeIps() }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSshErr(res.status === 422 ? "Некорректный IP-адрес или порт" : String(d.detail ?? res.statusText));
      } else {
        setTaskId(String(d.task_id));
        setSsh(SSH_INIT);
      }
    } catch (e) { setSshErr(String(e)); }
    setDeploying(false);
  };

  const remove = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); return; }
    setConfirmId(null);
    const res = await fetch(`/api/testservers/${id}`, { method: "DELETE" }).catch(() => null);
    if (res && !res.ok) toast("Не удалось удалить тест-сервер", "error");
    await load();
  };

  return (
    <div className="card card-p">
      <span className="micro flex items-center gap-2 mb-4"><Gauge size={13} /> Сервера для тестирования</span>

      {loading ? (
        <p className="hint">Загрузка…</p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.length === 0 && (
            <p className="hint">Пока нет тест-серверов. Добавьте существующий по IP или разверните новый по SSH.</p>
          )}
          {items.map(it => (
            <div key={it.id}
              className="flex items-center gap-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] px-3 py-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-[var(--t-hi)] truncate">{it.name}</span>
                  <span className="chip">iperf3</span>
                </div>
                <span className="text-[11px] text-[var(--t-faint)] truncate">
                  {it.ip}:{it.iperf_port} · добавлен {fmtDate(it.created_at)}
                </span>
              </div>
              <button onClick={() => remove(it.id)} className="iconbtn"
                style={confirmId === it.id ? { color: "var(--err)" } : undefined}
                title={confirmId === it.id ? "Нажмите ещё раз для удаления" : "Удалить"}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}

          {/* Register by IP */}
          <div className="mt-2 flex flex-col gap-2 rounded-lg border border-dashed border-[var(--line-soft)] p-3">
            <span className="micro">Добавить существующий сервер по IP</span>
            <div className="flex flex-col sm:flex-row gap-2">
              <input className="input flex-1" value={add.name}
                onChange={e => setAdd(s => ({ ...s, name: e.target.value }))} placeholder="Название (необязательно)" />
              <input className="input flex-[2]" value={add.ip}
                onChange={e => { setAdd(s => ({ ...s, ip: e.target.value })); setAddErr(null); }}
                placeholder="IP сервера" />
              <input className="input w-full sm:w-24" type="number" value={add.iperf_port}
                onChange={e => setAdd(s => ({ ...s, iperf_port: parseInt(e.target.value) || 5201 }))}
                placeholder="Порт" title="Порт iperf3" />
              <button onClick={addByIp} disabled={adding || !add.ip.trim()} className="btn btn-primary">
                {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Добавить
              </button>
            </div>
            {addErr && <span className="errmsg">{addErr}</span>}
          </div>

          {/* Deploy by SSH */}
          <div className="rounded-lg border border-dashed border-[var(--line-soft)] p-3">
            <button type="button" onClick={() => setShowSsh(s => !s)}
              className="w-full flex items-center justify-between gap-2 bg-transparent border-none cursor-pointer p-0">
              <span className="micro flex items-center gap-2"><TerminalSquare size={13} /> Развернуть по SSH</span>
              <ChevronDown size={14} className="text-[var(--t-faint)] transition-transform"
                style={{ transform: showSsh ? "rotate(180deg)" : "none" }} />
            </button>
            {showSsh && (
              <div className="mt-3 flex flex-col gap-2">
                <p className="hint">Установит iperf3-сервер и тест-инструменты (speedtest, xray). Доступ к порту iperf3
                  будет открыт только для IP ваших нод и бэкенда. SSH-данные не сохраняются.</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <input className="input" value={ssh.ip}
                    onChange={e => { setSsh(s => ({ ...s, ip: e.target.value })); setSshErr(null); }} placeholder="IP сервера" />
                  <input className="input" value={ssh.name}
                    onChange={e => setSsh(s => ({ ...s, name: e.target.value }))} placeholder="Название (необязательно)" />
                  <input className="input" value={ssh.ssh_user}
                    onChange={e => setSsh(s => ({ ...s, ssh_user: e.target.value }))} placeholder="SSH-пользователь" />
                  <input className="input" type="password" value={ssh.ssh_password}
                    onChange={e => setSsh(s => ({ ...s, ssh_password: e.target.value }))} placeholder="SSH-пароль" />
                  <input className="input" type="number" value={ssh.ssh_port}
                    onChange={e => setSsh(s => ({ ...s, ssh_port: parseInt(e.target.value) || 22 }))} placeholder="SSH-порт" />
                  <input className="input" type="number" value={ssh.iperf_port}
                    onChange={e => setSsh(s => ({ ...s, iperf_port: parseInt(e.target.value) || 5201 }))} placeholder="Порт iperf3" />
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <button onClick={deploy} disabled={deploying || !!taskId} className="btn btn-primary">
                    {deploying || taskId ? <><Loader2 size={13} className="animate-spin" /> Установка…</>
                      : <><TerminalSquare size={13} /> Развернуть</>}
                  </button>
                  {sshErr && <span className="errmsg">{sshErr}</span>}
                </div>
                {taskId && (
                  <p className="hint truncate" title={lastLog}>
                    Идёт установка… {lastLog && <span className="text-[var(--t-faint)]">{lastLog}</span>}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
