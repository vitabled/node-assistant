import { useState, useEffect, useCallback } from "react";
import { Globe, Trash2, Plus, ShieldCheck, Loader2, Download, Image, Info } from "lucide-react";
import { deployJobsKey, certJobsKey } from "../auth/store";
import { toast } from "./infra/Toast";
import { useTaskStream } from "../hooks/useTaskStream";
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
  const [fc, setFc]     = useState(true);
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
    const files = [fc ? "fullchain" : "", key ? "key" : ""].filter(Boolean);
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
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--t-mid)" }}>
            <input type="checkbox" checked={fc} onChange={e => setFc(e.target.checked)} /> fullchain.pem
          </label>
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--t-mid)" }}>
            <input type="checkbox" checked={key} onChange={e => setKey(e.target.checked)} /> приватный ключ
          </label>
          {key && (
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

  return (
    <div className="rounded-lg border" style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
      <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
        <Globe size={13} style={{ color: "var(--t-low)" }} />
        <span className="micro">Домены</span>
      </div>
      <div className="p-3 flex flex-col gap-1.5">
        {rows.length === 0 && (
          <p className="text-xs" style={{ color: "var(--t-faint)" }}>Нет доменов — добавьте ниже или задеплойте ноду.</p>
        )}
        {rows.map((row, i) => {
          const cl = certLabel(row);
          return (
            <div key={row.manualId ?? `d-${i}`} className="flex items-center gap-2 py-0.5">
              <ShieldCheck size={12} style={{ color: cl.tone, flex: "none" }} />
              <span className="text-sm truncate flex-1" style={{ color: "var(--t-mid)" }}>{row.domain}</span>
              {row.ip && <span className="text-[10px] tabular-nums" style={{ color: "var(--t-faint)" }}>{row.ip}</span>}
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
    </div>
  );
}
