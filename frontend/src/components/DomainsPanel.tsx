import { useState, useEffect, useCallback } from "react";
import { Globe, Trash2, Plus, ShieldCheck, Loader2, Download } from "lucide-react";
import { deployJobsKey } from "../auth/store";
import type { FormData } from "./DeployForm";

interface DeployJob { domain: string; ip: string; savedForm: FormData; finalStatus?: string }
interface ManualDomain { id: string; domain: string }
interface CertInfo { daysLeft: number; notAfter: string }

// A domain row = a name + (for deployed nodes) its SSH creds so we can probe the
// cert expiry, or (for manual domains) just the name + its store id for delete.
interface Row { domain: string; ip?: string; form?: FormData; manualId?: string; cert?: CertInfo | null; probing?: boolean }

function loadDeployDomains(): Row[] {
  try {
    const jobs: DeployJob[] = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
    return (Array.isArray(jobs) ? jobs : [])
      .filter(j => j.finalStatus === "success" && j.domain && j.savedForm?.mode !== "haproxy")
      .map(j => ({ domain: j.domain, ip: j.ip, form: j.savedForm }));
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

  if (!row.form) {
    return (
      <button className="iconbtn" style={{ width: 22, height: 22, opacity: 0.4 }}
        disabled title="Нет сохранённых SSH-доступов (домен добавлен вручную)">
        <Download size={12} />
      </button>
    );
  }
  const f = row.form;
  const sshPort = parseInt(f.change_ssh_port ? f.new_ssh_port : f.current_ssh_port, 10) || 22;

  const download = async () => {
    const files = [fc ? "fullchain" : "", key ? "key" : ""].filter(Boolean);
    if (!files.length) { setErr("Выберите файлы"); return; }
    setBusy(true); setErr("");
    try {
      const res = await fetch("/api/certs/download", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: f.ip, ssh_user: f.ssh_user, ssh_password: f.ssh_password,
          ssh_port: sshPort, domain: row.domain, files,
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

export function DomainsPanel() {
  const [rows, setRows]   = useState<Row[]>([]);
  const [adding, setAdding] = useState("");
  const [err, setErr]     = useState("");

  const load = useCallback(async () => {
    const deployRows = loadDeployDomains();
    let manual: ManualDomain[] = [];
    try { manual = await fetch("/api/domains").then(r => r.json()); } catch { /* ignore */ }
    // Dedup: a manual domain that's also a deployed node keeps the deploy row (has creds).
    const seen = new Set(deployRows.map(r => r.domain.toLowerCase()));
    const manualRows: Row[] = (Array.isArray(manual) ? manual : [])
      .filter(m => !seen.has(m.domain.toLowerCase()))
      .map(m => ({ domain: m.domain, manualId: m.id }));
    setRows([...deployRows, ...manualRows]);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Probe cert expiry for deployed rows (creds from savedForm), like DeployCard.
  useEffect(() => {
    let alive = true;
    rows.forEach((row, i) => {
      // Guard on `probing` too: setting probing:true creates a new rows array,
      // which reruns this effect — without the probing check it would fire a
      // fresh SSH probe on every rerun until the first resolved (a fetch storm).
      if (!row.form || row.cert !== undefined || row.probing) return;
      const f = row.form;
      const sshPort = parseInt(f.change_ssh_port ? f.new_ssh_port : f.current_ssh_port, 10) || 22;
      setRows(rs => rs.map((r, j) => j === i ? { ...r, probing: true } : r));
      fetch("/api/stats/node", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: f.ip, ssh_port: sshPort, ssh_user: f.ssh_user, ssh_password: f.ssh_password, domain: row.domain }),
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

  const certLabel = (row: Row) => {
    if (!row.form) return { text: "добавлен вручную", tone: "var(--t-faint)" };
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
              <DownloadCtl row={row} />
              {row.manualId && (
                <button onClick={() => removeManual(row.manualId!)} title="Удалить"
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
