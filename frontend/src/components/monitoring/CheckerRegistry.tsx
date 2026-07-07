import { useState, useEffect, useCallback } from "react";
import {
  Server, Plus, Trash2, Loader2, CheckCircle2, XCircle, Wifi, TerminalSquare, ChevronDown,
} from "lucide-react";

// Ф2 — registry of xray-checker instances (local built-in + remote). Connect a
// remote checker by URL or deploy one over SSH; enable/disable, test, delete.
// SSH credentials are sent per-request and never persisted.

interface Instance {
  id: string;
  name: string;
  kind: "local" | "remote";
  base_url: string;
  enabled: boolean;
}

const SSH_INIT = { ip: "", ssh_user: "root", ssh_password: "", ssh_port: 22, name: "", host_port: 2112 };

export function CheckerRegistry() {
  const [items,   setItems]   = useState<Instance[]>([]);
  const [loading, setLoading] = useState(true);

  const [url,     setUrl]     = useState("");
  const [urlName, setUrlName] = useState("");
  const [urlErr,  setUrlErr]  = useState<string | null>(null);
  const [adding,  setAdding]  = useState(false);

  const [showSsh,   setShowSsh]   = useState(false);
  const [ssh,       setSsh]       = useState(SSH_INIT);
  const [sshErr,    setSshErr]    = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);

  const [tests,   setTests]   = useState<Record<string, { ok: boolean; text: string }>>({});
  const [testing, setTesting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/checker/instances").then(r => r.json());
      setItems(Array.isArray(r.instances) ? r.instances : []);
    } catch { /* keep last */ }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const addUrl = async () => {
    if (!url.trim()) return;
    setAdding(true); setUrlErr(null);
    try {
      const res = await fetch("/api/checker/instances", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: urlName.trim(), base_url: url.trim() }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setUrlErr(res.status === 422 ? "URL должен начинаться с http(s)://"
          : res.status === 409 ? "Инстанс с таким URL уже добавлен"
          : res.status === 400 ? "Хост должен быть публичным (маршрутизируемым)"
          : String(d.detail ?? res.statusText));
      } else { setUrl(""); setUrlName(""); await load(); }
    } catch (e) { setUrlErr(String(e)); }
    setAdding(false);
  };

  const deploy = async () => {
    if (!ssh.ip.trim() || !ssh.ssh_password) { setSshErr("Укажите IP и пароль SSH"); return; }
    setDeploying(true); setSshErr(null);
    try {
      const res = await fetch("/api/checker/instances/deploy", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ssh),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) setSshErr(String(d.detail ?? res.statusText));
      else { setSsh(SSH_INIT); setShowSsh(false); await load(); }
    } catch (e) { setSshErr(String(e)); }
    setDeploying(false);
  };

  const toggle = async (it: Instance) => {
    await fetch(`/api/checker/instances/${it.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !it.enabled }),
    }).catch(() => {});
    await load();
  };

  const remove = async (id: string) => {
    await fetch(`/api/checker/instances/${id}`, { method: "DELETE" }).catch(() => {});
    await load();
  };

  const test = async (id: string) => {
    setTesting(id);
    try {
      const d = await fetch(`/api/checker/instances/${id}/test`, { method: "POST" }).then(r => r.json());
      setTests(t => ({ ...t, [id]: d.ok
        ? { ok: true,  text: "доступен" }
        : { ok: false, text: String(d.error || d.state || "недоступен") } }));
    } catch { setTests(t => ({ ...t, [id]: { ok: false, text: "ошибка" } })); }
    setTesting(null);
  };

  return (
    <div className="card card-p">
      <span className="micro flex items-center gap-2 mb-4"><Server size={13} /> Инстансы мониторинга</span>

      {loading ? (
        <p className="hint">Загрузка…</p>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map(it => {
            const t = tests[it.id];
            return (
              <div key={it.id}
                className="flex items-center gap-3 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] px-3 py-2">
                <button type="button" role="switch" aria-checked={it.enabled}
                  disabled={it.kind === "local"}
                  onClick={() => { if (it.kind !== "local") toggle(it); }}
                  className={`switch ${it.enabled ? "on" : ""}`}
                  title={it.kind === "local" ? "Управляется в «Локальный чекер»"
                    : it.enabled ? "Выключить" : "Включить"} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-[var(--t-hi)] truncate">{it.name}</span>
                    <span className="chip">{it.kind === "local" ? "локальный" : "удалённый"}</span>
                  </div>
                  {it.base_url && <span className="text-[11px] text-[var(--t-faint)] truncate">{it.base_url}</span>}
                </div>
                {t && (
                  <span className="text-[11px] flex items-center gap-1"
                    style={{ color: t.ok ? "var(--ok)" : "var(--err)" }}>
                    {t.ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />} {t.text}
                  </span>
                )}
                <button onClick={() => test(it.id)} disabled={testing === it.id}
                  className="iconbtn" title="Проверить соединение">
                  {testing === it.id ? <Loader2 size={13} className="animate-spin" /> : <Wifi size={13} />}
                </button>
                {it.kind === "remote" && (
                  <button onClick={() => remove(it.id)} className="iconbtn" title="Удалить">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            );
          })}

          {/* Connect by URL */}
          <div className="mt-2 flex flex-col gap-2 rounded-lg border border-dashed border-[var(--line-soft)] p-3">
            <span className="micro">Подключить удалённый чекер по URL</span>
            <div className="flex flex-col sm:flex-row gap-2">
              <input className="input flex-1" value={urlName}
                onChange={e => setUrlName(e.target.value)} placeholder="Название (необязательно)" />
              <input className="input flex-[2]" value={url}
                onChange={e => { setUrl(e.target.value); setUrlErr(null); }}
                placeholder="http://checker.example.com:2112" />
              <button onClick={addUrl} disabled={adding || !url.trim()} className="btn btn-primary">
                {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Добавить
              </button>
            </div>
            {urlErr && <span className="errmsg">{urlErr}</span>}
          </div>

          {/* Deploy by SSH */}
          <div className="rounded-lg border border-dashed border-[var(--line-soft)] p-3">
            <button type="button" onClick={() => setShowSsh(s => !s)}
              className="w-full flex items-center justify-between gap-2 bg-transparent border-none cursor-pointer p-0">
              <span className="micro flex items-center gap-2"><TerminalSquare size={13} /> Развернуть чекер по SSH</span>
              <ChevronDown size={14} className="text-[var(--t-faint)] transition-transform"
                style={{ transform: showSsh ? "rotate(180deg)" : "none" }} />
            </button>
            {showSsh && (
              <div className="mt-3 flex flex-col gap-2">
                <p className="hint">Установит kutovoys/xray-checker на сервер. Использует подписку из «Локальный чекер».
                  SSH-данные не сохраняются.</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <input className="input" value={ssh.ip}
                    onChange={e => setSsh(s => ({ ...s, ip: e.target.value }))} placeholder="IP сервера" />
                  <input className="input" value={ssh.name}
                    onChange={e => setSsh(s => ({ ...s, name: e.target.value }))} placeholder="Название (необязательно)" />
                  <input className="input" value={ssh.ssh_user}
                    onChange={e => setSsh(s => ({ ...s, ssh_user: e.target.value }))} placeholder="SSH-пользователь" />
                  <input className="input" type="password" value={ssh.ssh_password}
                    onChange={e => setSsh(s => ({ ...s, ssh_password: e.target.value }))} placeholder="SSH-пароль" />
                  <input className="input" type="number" value={ssh.ssh_port}
                    onChange={e => setSsh(s => ({ ...s, ssh_port: parseInt(e.target.value) || 22 }))} placeholder="SSH-порт" />
                  <input className="input" type="number" value={ssh.host_port}
                    onChange={e => setSsh(s => ({ ...s, host_port: parseInt(e.target.value) || 2112 }))} placeholder="Порт чекера" />
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={deploy} disabled={deploying} className="btn btn-primary">
                    {deploying ? <><Loader2 size={13} className="animate-spin" /> Развёртывание…</>
                      : <><TerminalSquare size={13} /> Развернуть</>}
                  </button>
                  {sshErr && <span className="errmsg">{sshErr}</span>}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
