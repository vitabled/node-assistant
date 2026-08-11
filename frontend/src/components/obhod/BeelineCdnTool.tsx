import { useEffect, useState } from "react";
import { CheckCircle2, Cloud, Loader2 } from "lucide-react";
import { Page, PageHeader } from "../../theme/ui";

/**
 * «Beeline CDN» (Обходы БС, Wave-4 PR-9): проставить CDN-домен Beeline в
 * sni/host хостов панели Remnawave (PATCH /api/hosts через /api/obhod/beeline/apply).
 * Origin в ЛК Beeline CDN настраивается вручную — гайд-карточка ниже.
 */

interface HostOpt {
  uuid: string; remark: string; address: string; port?: number;
  sni?: string; host?: string; isDisabled?: boolean;
}

const DOMAIN_RE = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;

export function BeelineCdnTool() {
  const [hosts, setHosts] = useState<HostOpt[]>([]);
  const [loadErr, setLoadErr] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [result, setResult] = useState<{ applied: string[]; errors: { uuid: string; error: string }[] } | null>(null);

  useEffect(() => {
    fetch("/api/obhod/hosts").then(async r => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setLoadErr(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`); return; }
      setHosts(d.hosts || []);
    }).catch(() => setLoadErr("Панель недоступна"));
  }, []);

  const toggle = (uuid: string) =>
    setSelected(s => s.includes(uuid) ? s.filter(x => x !== uuid) : [...s, uuid]);

  const apply = async () => {
    setErr(""); setResult(null);
    const d = domain.trim().toLowerCase();
    if (!DOMAIN_RE.test(d)) { setErr("Некорректный CDN-домен (нужен FQDN)"); return; }
    setBusy(true);
    try {
      const res = await fetch("/api/obhod/beeline/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_uuids: selected, domain: d }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`); return; }
      setResult({ applied: data.applied || [], errors: data.errors || [] });
    } finally { setBusy(false); }
  };

  return (
    <Page max={860}>
      <PageHeader icon={<Cloud size={16} />} title="Beeline CDN"
        subtitle="Обход через CDN Beeline: CDN-домен в SNI/Host хостов панели" />

      <div className="card card-p" style={{ marginBottom: 14 }}>
        <p className="micro" style={{ marginBottom: 8 }}>Шаги в личном кабинете Beeline CDN</p>
        <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: "var(--t-mid)", display: "flex", flexDirection: "column", gap: 4 }}>
          <li>Создайте <strong>CDN-ресурс</strong>: origin — домен (или IP) вашей ноды, порт 443, HTTPS.</li>
          <li>Получите <strong>CDN-домен</strong> ресурса (или привяжите свой домен CNAME'ом).</li>
          <li>Впишите CDN-домен ниже — хосты панели начнут отвечать на него (SNI/Host).</li>
        </ol>
      </div>

      <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <p className="micro">Применение CDN-домена к хостам</p>
        {loadErr && <p className="errmsg">{loadErr}</p>}

        <div>
          <label className="label">CDN-домен (SNI и Host одновременно)</label>
          <input className="input" value={domain} onChange={e => setDomain(e.target.value)}
            placeholder="cdn123.b-cdn.net" disabled={busy} />
        </div>

        <div>
          <label className="label">Хосты панели ({selected.length} выбрано)</label>
          {hosts.length === 0 && !loadErr && <p className="hint"><Loader2 size={12} className="spin" /></p>}
          <div style={{
            border: "1px solid var(--line-soft)", borderRadius: "var(--r-md)",
            maxHeight: 260, overflowY: "auto",
          }}>
            {hosts.map(h => (
              <label key={h.uuid} className="flex items-center gap-2.5 px-3 py-2 cursor-pointer"
                style={{ borderBottom: "1px solid var(--line-soft)", fontSize: 13 }}
                onClick={e => { e.preventDefault(); toggle(h.uuid); }}>
                <span className={`ck ${selected.includes(h.uuid) ? "on" : ""}`}>
                  {selected.includes(h.uuid) ? "✓" : ""}
                </span>
                <span style={{ color: "var(--t-hi)", flex: 1 }} className="trunc">{h.remark || h.address}</span>
                {h.sni && <span className="tag" title="Текущий SNI">{h.sni}</span>}
                {h.isDisabled && <span className="chip neutral" style={{ fontSize: 10 }}>выкл</span>}
              </label>
            ))}
          </div>
        </div>

        {err && <p className="errmsg">{err}</p>}
        {result && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12.5 }}>
            <span style={{ color: "var(--ok)", display: "flex", alignItems: "center", gap: 6 }}>
              <CheckCircle2 size={14} /> Применено к {result.applied.length} хостам
            </span>
            {result.errors.map(e => (
              <span key={e.uuid} style={{ color: "var(--err)" }}>Ошибка {e.uuid.slice(0, 8)}…: {e.error}</span>
            ))}
          </div>
        )}

        <button className="btn btn-primary" style={{ alignSelf: "flex-start" }} onClick={apply}
          disabled={busy || selected.length === 0 || !domain.trim()}>
          {busy ? <Loader2 size={13} className="spin" /> : <Cloud size={13} />}
          Применить CDN-домен
        </button>
      </div>
    </Page>
  );
}
