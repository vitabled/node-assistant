import { useEffect, useMemo, useState } from "react";
import { Loader2, ScanSearch, Server, X } from "lucide-react";
import { deployJobsKey } from "../auth/store";

/**
 * «Авто» для «Управления SSL» (Wave-4 PR-3): сервис заходит на сервер по SSH
 * и собирает ВСЕ его домены (nginx/apache, certbot, xray/remnanode, масксайт),
 * оператор выбирает нужные чекбоксами — они добавляются в «Домены».
 *
 * Креды подставляются из формы сертификата или из сохранённых серверов деплоя
 * (localStorage, как везде — SSH-креды на сервере не хранятся).
 */

interface KnownServer {
  ip: string; ssh_user: string; ssh_password: string; ssh_port: string | number;
  domain?: string;
}

interface FoundDomain { domain: string; sources: string[] }

export function ScanDomainsModal({ defaults, onClose, onAdded }: {
  defaults: { ip: string; ssh_user: string; ssh_password: string; ssh_port: string };
  onClose: () => void;
  onAdded: () => void;
}) {
  const [ip, setIp] = useState(defaults.ip);
  const [port, setPort] = useState(defaults.ssh_port);
  const [user, setUser] = useState(defaults.ssh_user);
  const [password, setPassword] = useState(defaults.ssh_password);
  const [scanning, setScanning] = useState(false);
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState("");
  const [found, setFound] = useState<FoundDomain[] | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  // Известные серверы из деплоя — выбор заполняет креды одним кликом.
  const known = useMemo<KnownServer[]>(() => {
    try {
      const jobs = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
      const arr = Array.isArray(jobs) ? jobs : [];
      const seen = new Set<string>();
      return arr
        .map((j: { savedForm?: KnownServer; ip?: string }) => ({
          ip: j.savedForm?.ip || j.ip || "",
          ssh_user: j.savedForm?.ssh_user || "root",
          ssh_password: j.savedForm?.ssh_password || "",
          ssh_port: j.savedForm?.ssh_port || 22,
          domain: j.savedForm?.domain,
        }))
        .filter((s: KnownServer) => {
          if (!s.ip || seen.has(s.ip)) return false;
          seen.add(s.ip);
          return true;
        });
    } catch { return []; }
  }, []);

  const pick = (s: KnownServer) => {
    setIp(s.ip); setUser(s.ssh_user); setPassword(s.ssh_password);
    setPort(String(s.ssh_port)); setErr("");
  };

  const scan = async () => {
    setScanning(true); setErr(""); setFound(null);
    try {
      const res = await fetch("/api/certs/scan-domains", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: ip.trim(), ssh_port: parseInt(port, 10) || 22,
          ssh_user: user.trim(), ssh_password: password,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      const list: FoundDomain[] = Array.isArray(d.domains) ? d.domains : [];
      setFound(list);
      setChecked(new Set(list.map(x => x.domain)));
      if (list.length === 0) setErr("Домены не найдены — на сервере нет nginx/certbot/xray конфигов с доменами.");
    } finally { setScanning(false); }
  };

  const addSelected = async () => {
    setAdding(true); setErr("");
    try {
      for (const domain of checked) {
        await fetch("/api/domains", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ domain }),
        }).catch(() => {});
      }
      onAdded();
      onClose();
    } finally { setAdding(false); }
  };

  const toggle = (d: string) => setChecked(s => {
    const n = new Set(s);
    if (n.has(d)) n.delete(d); else n.add(d);
    return n;
  });

  // Esc закрывает, как другие модалки
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg">
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <ScanSearch size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
              Авто: домены сервера
            </h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          {known.length > 0 && (
            <div>
              <label className="label">Сервер</label>
              <div className="flex flex-wrap gap-1.5">
                {known.map(s => (
                  <button key={s.ip} type="button"
                    className={`chip ${s.ip === ip ? "accent" : "neutral"}`}
                    style={{ cursor: "pointer", border: "1px solid" }}
                    onClick={() => pick(s)}>
                    {s.domain || s.ip}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-[1fr_90px] gap-2">
            <div>
              <label className="label">IP сервера</label>
              <input className="input" value={ip} onChange={e => setIp(e.target.value)} placeholder="1.2.3.4" />
            </div>
            <div>
              <label className="label">SSH порт</label>
              <input className="input" value={port} onChange={e => setPort(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">SSH пользователь</label>
              <input className="input" value={user} onChange={e => setUser(e.target.value)} />
            </div>
            <div>
              <label className="label">SSH пароль</label>
              <input className="input" type="password" value={password}
                onChange={e => setPassword(e.target.value)} autoComplete="off" />
            </div>
          </div>

          <button type="button" className="btn btn-soft" onClick={scan}
            disabled={scanning || !ip.trim() || !user.trim() || !password}>
            {scanning ? <Loader2 size={13} className="spin" /> : <ScanSearch size={13} />}
            {scanning ? "Сканирую…" : "Сканировать домены"}
          </button>

          {found && found.length > 0 && (
            <div style={{
              border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)",
              maxHeight: 220, overflowY: "auto",
            }}>
              {found.map(f => (
                <label key={f.domain} className="flex items-center gap-2.5 px-3 py-2 cursor-pointer"
                  style={{ borderBottom: "1px solid var(--line-soft)", fontSize: 13 }}
                  onClick={e => { e.preventDefault(); toggle(f.domain); }}>
                  <span className={`ck ${checked.has(f.domain) ? "on" : ""}`}>
                    {checked.has(f.domain) ? "✓" : ""}
                  </span>
                  <span style={{ color: "var(--t-hi)", flex: 1 }}>{f.domain}</span>
                  {f.sources.map(s => <span key={s} className="tag">{s}</span>)}
                </label>
              ))}
            </div>
          )}

          {err && <p className="errmsg">{err}</p>}
          <p className="hint">Читаются nginx/apache server_name, сертификаты certbot,
            конфиги xray/remnanode и маскировочный сайт. Пароль нигде не сохраняется.</p>
        </div>

        <div className="flex justify-end gap-2 px-5 py-3.5"
          style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button type="button" className="btn" onClick={onClose}>Отмена</button>
          <button type="button" className="btn btn-primary" onClick={addSelected}
            disabled={adding || !found || checked.size === 0}>
            {adding ? <Loader2 size={13} className="spin" /> : <Server size={13} />}
            Добавить выбранные ({checked.size})
          </button>
        </div>
      </div>
    </div>
  );
}
