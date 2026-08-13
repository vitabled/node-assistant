import { useState, useEffect, useCallback } from "react";
import { Globe, Trash2, Plus, ShieldCheck, Loader2, Download, Image, Info, ArrowLeftRight, X } from "lucide-react";
import { deployJobsKey, certJobsKey } from "../auth/store";
import { toast } from "./infra/Toast";
import { useTaskStream } from "../hooks/useTaskStream";
import { TerminalOutput } from "./TerminalOutput";
import type { FormData } from "./DeployForm";
import type { CertsFormData } from "./CertsForm";

interface DeployJob { domain: string; ip: string; savedForm: FormData; finalStatus?: string }
interface ManualDomain { id: string; domain: string }
interface CertInfo { daysLeft: number; notAfter: string }

// Normalised SSH creds for probing cert expiry / downloading the cert. Both
// deployed nodes (from savedForm) and cert-only deploys (from CertsFormData)
// reduce to this, so the probe/download code has one shape to work with.
interface Creds { ip: string; ssh_user: string; ssh_password: string; ssh_port: number }

// A domain row = a name + (for deployed/cert rows) its SSH creds so we can probe
// the cert expiry & download it, or (for manual domains) just the name + store id.
interface Row { domain: string; ip?: string; creds?: Creds; manualId?: string; certJob?: boolean; cert?: CertInfo | null; probing?: boolean }

function loadDeployDomains(): Row[] {
  try {
    const jobs: DeployJob[] = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
    return (Array.isArray(jobs) ? jobs : [])
      .filter(j => j.finalStatus === "success" && j.domain && j.savedForm?.mode !== "haproxy")
      .map(j => {
        const f = j.savedForm;
        const port = parseInt(f.change_ssh_port ? f.new_ssh_port : f.current_ssh_port, 10) || 22;
        return { domain: j.domain, ip: j.ip,
          creds: { ip: f.ip, ssh_user: f.ssh_user, ssh_password: f.ssh_password, ssh_port: port } };
      });
  } catch { return []; }
}

// Domains a cert was deployed to via «Управление SSL». No deploy_jobs entry is
// made for a cert-only deploy, so without this the domain never appeared here.
function loadCertDomains(): Row[] {
  try {
    const jobs: CertsFormData[] = JSON.parse(localStorage.getItem(certJobsKey()) || "[]");
    return (Array.isArray(jobs) ? jobs : [])
      .filter(j => j.domain)
      .map(j => ({ domain: j.domain, ip: j.ip, certJob: true,
        creds: { ip: j.ip, ssh_user: j.ssh_user, ssh_password: j.ssh_password,
                 ssh_port: parseInt(j.ssh_port, 10) || 22 } }));
  } catch { return []; }
}

// Per-row cert download. Deployed rows carry SSH creds (from savedForm) so we can
// read the installed cert files; manual domains have none → the control is
// disabled with a hint. Creds are sent per-request and never persisted.
function DownloadCtl({ row }: { row: Row }) {
  const [open, setOpen] = useState(false);
  const [bundle, setBundle] = useState(false);
  const [fc, setFc]     = useState(true);
  const [crt, setCrt]   = useState(false);
  const [key, setKey]   = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr]   = useState("");

  if (!row.creds) {
    return (
      <button className="iconbtn" style={{ width: 22, height: 22, opacity: 0.4 }}
        disabled title="Нет сохранённых SSH-доступов (домен добавлен вручную)">
        <Download size={12} />
      </button>
    );
  }
  const c = row.creds;

  const download = async () => {
    const files = bundle ? ["bundle"] : [fc ? "fullchain" : "", key ? "key" : "", crt ? "cert" : ""].filter(Boolean);
    if (!files.length) { setErr("Выберите файлы"); return; }
    setBusy(true); setErr("");
    try {
      const res = await fetch("/api/certs/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: c.ip, ssh_user: c.ssh_user, ssh_password: c.ssh_password,
          ssh_port: c.ssh_port, domain: row.domain, files,
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        setErr(typeof j.detail === "string" ? j.detail : "Ошибка скачивания");
        return;
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="(.+?)"/);
      const name = m ? m[1] : `${row.domain}-cert`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = name; a.click();
      URL.revokeObjectURL(url);
      setOpen(false);
    } catch { setErr("Сеть недоступна"); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ position: "relative", flex: "none" }}>
      <button className="iconbtn" style={{ width: 22, height: 22 }}
        title="Скачать сертификат" onClick={() => setOpen(o => !o)}>
        <Download size={12} />
      </button>
      {open && (
        <div className="rounded-lg border" style={{
          position: "absolute", right: 0, top: 26, zIndex: 20, width: 220, padding: 10,
          background: "var(--bg1)", borderColor: "var(--line-soft)", boxShadow: "var(--shadow-pop)",
          display: "flex", flexDirection: "column", gap: 8,
        }}>
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--t-hi)" }}>
            <input type="checkbox" checked={bundle}
              onChange={e => { setBundle(e.target.checked); if (e.target.checked) { setFc(false); setKey(false); setCrt(false); } }} />
            Вся папка домена (zip)
          </label>
          <label className="flex items-center gap-2 text-xs" style={{ color: bundle ? "var(--t-faint)" : "var(--t-mid)" }}>
            <input type="checkbox" checked={fc} disabled={bundle} onChange={e => setFc(e.target.checked)} /> fullchain.pem
          </label>
          <label className="flex items-center gap-2 text-xs" style={{ color: bundle ? "var(--t-faint)" : "var(--t-mid)" }}>
            <input type="checkbox" checked={crt} disabled={bundle} onChange={e => setCrt(e.target.checked)} /> cert (.crt)
          </label>
          <label className="flex items-center gap-2 text-xs" style={{ color: bundle ? "var(--t-faint)" : "var(--t-mid)" }}>
            <input type="checkbox" checked={key} disabled={bundle} onChange={e => setKey(e.target.checked)} /> приватный ключ
          </label>
          {key && !bundle && (
            <p className="text-[10px]" style={{ color: "var(--warn)" }}>
              Ключ передаётся — используйте только по HTTPS.
            </p>
          )}
          <button className="btn btn-primary" style={{ height: 28 }} disabled={busy} onClick={download}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : "Скачать"}
          </button>
          {err && <p className="errmsg" style={{ margin: 0 }}>{err}</p>}
        </div>
      )}
    </div>
  );
}

// ── ACME-статус и SelfSteal (Wave-5 PR-1, механики remnawave-reverse) ─────
function AcmeInfo({ creds }: { creds: Creds }) {
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<{ ca: string; cron: boolean } | null>(null);
  const probe = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/certs/acme-status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...creds }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Ошибка");
      const cron = !!d.renewal_cron;
      const ca = (d.certs?.[0]?.ca || "").replace(/^https?:\/\//, "").split("/")[2] || "—";
      setInfo({ ca, cron });
    } catch (e) { toast((e as Error).message, "error"); }
    setBusy(false);
  };
  return (
    <button type="button" onClick={probe} disabled={busy}
      title={info ? `CA: ${info.ca} · авто-продление: ${info.cron ? "включено" : "не найдено"}` : "ACME-статус (CA, авто-продление)"}
      className="iconbtn accent" style={{ width: 22, height: 22, flex: "none" }}>
      {busy ? <Loader2 size={12} className="spin" /> : <Info size={12} />}
      {info && (
        <span className="text-[10px] tabular-nums" style={{ color: info.cron ? "var(--ok)" : "var(--warn)" }}>
          {info.cron ? "auto" : "no-cron"}
        </span>
      )}
    </button>
  );
}

function SelfStealBtn({ creds, domain }: { creds: Creds; domain: string }) {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "running" | "ok" | "fail">("idle");
  useTaskStream({
    taskId,
    onLog: useCallback(() => {}, []),
    onStatus: useCallback((f: { status: string }) => {
      if (f.status === "success") setState("ok");
      if (f.status === "failed") setState("fail");
    }, []),
  });
  const run = async () => {
    setState("running");
    try {
      const res = await fetch("/api/certs/selfsteal", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...creds }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Ошибка");
      setTaskId(d.task_id);
    } catch (e) {
      setState("fail");
      toast((e as Error).message, "error");
    }
  };
  return (
    <button type="button" onClick={run} disabled={state === "running"}
      title={state === "ok" ? "Маскировка сменена" : state === "fail" ? "Ошибка смены маскировки" : `Сменить маскировочный сайт (SelfSteal) на ${domain}`}
      className="iconbtn" style={{ width: 22, height: 22, flex: "none",
        color: state === "ok" ? "var(--ok)" : state === "fail" ? "var(--err)" : undefined }}>
      {state === "running" ? <Loader2 size={12} className="spin" /> : <Image size={12} />}
    </button>
  );
}

// ── перенос сертификатов между серверами (Wave-5 PR-4) ─────────
function TransferModal({ rows, onClose, onDone }: {
  rows: Row[]; onClose: () => void; onDone: () => void;
}) {
  const [ip, setIp] = useState("");
  const [port, setPort] = useState("22");
  const [user, setUser] = useState("root");
  const [password, setPassword] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [err, setErr] = useState("");

  useTaskStream({
    taskId,
    onLog: useCallback((l: string) => setLogs(ls => [...ls, l]), []),
    onStatus: useCallback((f: { status: string }) => setStatus(f.status), []),
  });

  const running = status === "running" || (status === "" && taskId !== null) || status === "pending";

  const start = async () => {
    setErr("");
    const src = rows[0]?.creds;
    if (!src) return;
    // Источник: креды первой выбранной строки (домены с одного сервера переносятся
    // вместе; разные серверы-источники — отдельными переносами).
    const res = await fetch("/api/certs/transfer", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: src,
        target: { ip: ip.trim(), ssh_port: parseInt(port, 10) || 22, ssh_user: user.trim(), ssh_password: password },
        domains: rows.map(r => r.domain),
      }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
    setLogs([]); setStatus("");
    setTaskId(d.task_id);
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget && !running) onClose(); }}>
      <div className="modal max-w-lg">
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
          style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <ArrowLeftRight size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
              Перенос сертификатов ({rows.length})
            </h2>
          </div>
          <button onClick={onClose} className="iconbtn" disabled={running}><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          <p className="hint" style={{ margin: 0 }}>
            Домены: <strong>{rows.map(r => r.domain).join(", ")}</strong> — с сервера {rows[0]?.ip}.
            Креды не сохраняются; файлы раскладываются по тем же путям + letsencrypt-симлинки + reload nginx.
          </p>
          <div className="grid grid-cols-[1fr_90px] gap-2">
            <div>
              <label className="label">IP целевого сервера</label>
              <input className="input" value={ip} onChange={e => setIp(e.target.value)} placeholder="5.6.7.8" disabled={running} />
            </div>
            <div>
              <label className="label">SSH порт</label>
              <input className="input" value={port} onChange={e => setPort(e.target.value)} disabled={running} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">SSH пользователь</label>
              <input className="input" value={user} onChange={e => setUser(e.target.value)} disabled={running} />
            </div>
            <div>
              <label className="label">SSH пароль</label>
              <input className="input" type="password" value={password} onChange={e => setPassword(e.target.value)}
                autoComplete="off" disabled={running} />
            </div>
          </div>
          {err && <p className="errmsg">{err}</p>}
          {status === "success" && <p style={{ fontSize: 13, color: "var(--ok)" }}>Перенос завершён.</p>}
          {status === "failed" && <p style={{ fontSize: 13, color: "var(--err)" }}>Перенос не удался — см. лог.</p>}
          {taskId && (
            <div style={{ height: 180 }}>
              <TerminalOutput lines={logs} />
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3.5" style={{ borderTop: "1px solid var(--line-soft)" }}>
          {status === "success"
            ? <button type="button" className="btn btn-primary" onClick={onDone}>Готово</button>
            : <>
                <button type="button" className="btn" onClick={onClose} disabled={running}>Отмена</button>
                <button type="button" className="btn btn-primary" onClick={start}
                  disabled={running || !ip.trim() || !password}>
                  {running ? <Loader2 size={13} className="spin" /> : <ArrowLeftRight size={13} />}
                  Перенести
                </button>
              </>}
        </div>
      </div>
    </div>
  );
}

export function DomainsPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [rows, setRows]   = useState<Row[]>([]);
  const [adding, setAdding] = useState("");
  const [err, setErr]     = useState("");

  const load = useCallback(async () => {
    // Priority: deployed node > cert deploy > manual — a domain present in a
    // richer source keeps that row (deploy/cert rows carry SSH creds).
    const deployRows = loadDeployDomains();
    const seen = new Set(deployRows.map(r => r.domain.toLowerCase()));
    const certRows = loadCertDomains().filter(r => !seen.has(r.domain.toLowerCase()));
    certRows.forEach(r => seen.add(r.domain.toLowerCase()));

    let manual: ManualDomain[] = [];
    try { manual = await fetch("/api/domains").then(r => r.json()); } catch { /* ignore */ }
    const manualRows: Row[] = (Array.isArray(manual) ? manual : [])
      .filter(m => !seen.has(m.domain.toLowerCase()))
      .map(m => ({ domain: m.domain, manualId: m.id }));
    setRows([...deployRows, ...certRows, ...manualRows]);
  }, []);

  // Reload on mount AND whenever a cert deploy just succeeded (refreshKey bump).
  useEffect(() => { load(); }, [load, refreshKey]);

  // Probe cert expiry for rows that carry creds (deployed nodes + cert deploys),
  // like DeployCard.
  useEffect(() => {
    let alive = true;
    rows.forEach((row, i) => {
      // Guard on `probing` too: setting probing:true creates a new rows array,
      // which reruns this effect — without the probing check it would fire a
      // fresh SSH probe on every rerun until the first resolved (a fetch storm).
      if (!row.creds || row.cert !== undefined || row.probing) return;
      const c = row.creds;
      setRows(rs => rs.map((r, j) => j === i ? { ...r, probing: true } : r));
      fetch("/api/stats/node", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: c.ip, ssh_port: c.ssh_port, ssh_user: c.ssh_user, ssh_password: c.ssh_password, domain: row.domain }),
      }).then(r => r.json()).then(d => {
        if (!alive) return;
        setRows(rs => rs.map((r, j) => j === i ? { ...r, cert: d.certInfo ?? null, probing: false } : r));
      }).catch(() => {
        if (alive) setRows(rs => rs.map((r, j) => j === i ? { ...r, cert: null, probing: false } : r));
      });
    });
    return () => { alive = false; };
  }, [rows]);

  const addDomain = async () => {
    const v = adding.trim();
    if (!v) return;
    setErr("");
    const res = await fetch("/api/domains", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: v }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      setErr(res.status === 409 ? "Домен уже добавлен" : (typeof e.detail === "string" ? e.detail : "Некорректный домен"));
      return;
    }
    setAdding("");
    await load();
  };

  const removeManual = async (id: string) => {
    await fetch(`/api/domains/${id}`, { method: "DELETE" }).catch(() => {});
    await load();
  };

  // Drop a cert-deploy row from tracking (it lives only in localStorage, not
  // /api/domains). The cert on the server is untouched — this only stops tracking.
  const removeCert = (domain: string) => {
    try {
      const key = certJobsKey();
      const jobs = JSON.parse(localStorage.getItem(key) || "[]");
      const next = (Array.isArray(jobs) ? jobs : [])
        .filter((j: CertsFormData) => j.domain?.toLowerCase() !== domain.toLowerCase());
      localStorage.setItem(key, JSON.stringify(next));
    } catch { /* ignore */ }
    load();
  };

  const certLabel = (row: Row) => {
    if (!row.creds) return { text: "добавлен вручную", tone: "var(--t-faint)" };
    if (row.probing) return { text: "проверка…", tone: "var(--t-faint)" };
    const d = row.cert?.daysLeft;
    if (d === undefined || row.cert === null) return { text: "неизвестно", tone: "var(--t-faint)" };
    if (d < 0)  return { text: `истёк ${-d} дн. назад`, tone: "var(--err)" };
    if (d < 14) return { text: `${d} дн.`, tone: "var(--warn)" };
    return { text: `${d} дн.`, tone: "var(--ok)" };
  };

  // Группировка по IP ноды (Wave-5 PR-4): заголовок группы = IP, домены —
  // карточки с фоном внутри. Без IP (ручные записи) — группа «Без сервера».
  const groups: [string, Row[]][] = [];
  for (const r of rows) {
    const key = r.ip || "";
    const g = groups.find(x => x[0] === key);
    if (g) g[1].push(r);
    else groups.push([key, [r]]);
  }

  // Выбор доменов для переноса (только строки с кредами — без них прочитать
  // сертификат неоткуда).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [transferOpen, setTransferOpen] = useState(false);
  const toggleSel = (d: string) => setSelected(s => {
    const n = new Set(s);
    if (n.has(d)) n.delete(d); else n.add(d);
    return n;
  });

  return (
    <div className="rounded-lg border" style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
      <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
        <Globe size={13} style={{ color: "var(--t-low)" }} />
        <span className="micro">Домены</span>
        {selected.size > 0 && (
          <button className="btn btn-soft" style={{ marginLeft: "auto", padding: "3px 10px", fontSize: 11 }}
            onClick={() => setTransferOpen(true)}>
            <ArrowLeftRight size={11} /> Перенести выбранные ({selected.size})
          </button>
        )}
      </div>
      <div className="p-3 flex flex-col gap-3">
        {rows.length === 0 && (
          <p className="text-xs" style={{ color: "var(--t-faint)" }}>Нет доменов — добавьте ниже или задеплойте ноду.</p>
        )}
        {groups.map(([ip, grows]) => (
          <div key={ip || "manual"}>
            <p className="micro" style={{ marginBottom: 6, color: "var(--t-faint)" }}>
              {ip ? `Нода ${ip}` : "Без сервера"}
            </p>
            <div className="flex flex-col gap-1.5">
              {grows.map((row, i) => {
                const cl = certLabel(row);
                return (
                  <div key={row.manualId ?? `d-${i}`}
                    className="flex items-center gap-2 px-2.5 py-2 rounded-lg"
                    style={{ background: "var(--raised)", border: "1px solid var(--line-soft)" }}>
                    {row.creds && (
                      <span className={`ck ${selected.has(row.domain) ? "on" : ""}`} style={{ marginTop: 0 }}
                        title="Выбрать для переноса"
                        onClick={() => toggleSel(row.domain)}>
                        {selected.has(row.domain) ? "✓" : ""}
                      </span>
                    )}
                    <ShieldCheck size={12} style={{ color: cl.tone, flex: "none" }} />
                    <span className="text-sm truncate flex-1" style={{ color: "var(--t-mid)" }}>{row.domain}</span>
                    <span className="text-xs tabular-nums" style={{ color: cl.tone }}>{cl.text}</span>
                    {row.creds && <AcmeInfo creds={row.creds} />}
                    {row.creds && <SelfStealBtn creds={row.creds} domain={row.domain} />}
                    <DownloadCtl row={row} />
                    {row.manualId && (
                      <button onClick={() => removeManual(row.manualId!)} title="Удалить"
                        className="iconbtn danger" style={{ width: 22, height: 22 }}>
                        <Trash2 size={12} />
                      </button>
                    )}
                    {row.certJob && (
                      <button onClick={() => removeCert(row.domain)} title="Убрать из отслеживания (сертификат на сервере не трогается)"
                        className="iconbtn danger" style={{ width: 22, height: 22 }}>
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <div className="flex gap-2 mt-1">
          <input
            value={adding}
            onChange={e => { setAdding(e.target.value); setErr(""); }}
            onKeyDown={e => e.key === "Enter" && addDomain()}
            placeholder="example.com"
            className="input"
            spellCheck={false}
          />
          <button onClick={addDomain} disabled={!adding.trim()}
            className="btn btn-primary" style={{ flex: "none" }}>
            <Plus size={14} />
          </button>
        </div>
        {err && <p className="errmsg">{err}</p>}
      </div>

      {transferOpen && (
        <TransferModal
          rows={rows.filter(r => selected.has(r.domain) && r.creds)}
          onClose={() => setTransferOpen(false)}
          onDone={() => { setTransferOpen(false); setSelected(new Set()); load(); }}
        />
      )}
    </div>
  );
}
