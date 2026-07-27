// «Cloudflare → Платежи».
//
// ⚠️ Cloudflare has NO public payment-history endpoint (the old user-level
// /user/billing/history is gone and no account-level equivalent is documented).
// Rather than render an empty ledger that reads as a bug, this page shows what the
// API does give — recent billable usage and upcoming subscription charges — and
// says plainly where the real invoices live.
import { useCallback, useEffect, useMemo, useState } from "react";
import { CreditCard, RefreshCw, ExternalLink } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { useCfReady, CfNotConnected } from "./gate";
import { normalizeUsage } from "./CfUsage";
import {
  getUsage, listSubscriptions, fmtMoney, fmtDay, messageOf, type CfSubscription,
} from "./api";

const since = (days: number) => new Date(Date.now() - days * 86400_000).toISOString().slice(0, 10);

export function CfPayments() {
  const { ready, loading: gate } = useCfReady();
  const [raw, setRaw] = useState<unknown>(null);
  const [subs, setSubs] = useState<CfSubscription[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback((refresh = false) => {
    setBusy(true);
    Promise.all([
      getUsage(since(30), since(0), refresh).catch(() => null),
      listSubscriptions(refresh).catch(() => [] as CfSubscription[]),
    ])
      .then(([u, s]) => { setRaw(u); setSubs(Array.isArray(s) ? s : []); setErr(""); })
      .catch(e => setErr(messageOf(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (ready) load(); }, [ready, load]);

  const spend = useMemo(() => {
    const { rows } = normalizeUsage(raw);
    const total = rows.reduce((n, r) => n + (r.amount ?? 0), 0);
    return { total, currency: rows[0]?.currency || "USD", count: rows.length };
  }, [raw]);

  const upcoming = useMemo(
    () => subs
      .filter(s => s.current_period_end)
      .sort((a, b) => String(a.current_period_end).localeCompare(String(b.current_period_end))),
    [subs],
  );

  if (gate) return <Page><p className="micro">Загрузка…</p></Page>;
  if (!ready) return <CfNotConnected title="Cloudflare: платежи" />;

  return (
    <Page>
      <PageHeader
        icon={<CreditCard size={18} />}
        title="Cloudflare: платежи"
        subtitle="Расход за 30 дней и предстоящие списания"
        actions={
          <button className="btn" disabled={busy} onClick={() => load(true)}>
            <RefreshCw size={14} /> Обновить
          </button>
        }
      />

      <div className="rounded-lg p-3 mb-4 text-xs"
        style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t-hi)" }}>
        Cloudflare не отдаёт историю платежей через публичный API — здесь показан
        оплачиваемый расход и предстоящие списания по подпискам.{" "}
        <a href="https://dash.cloudflare.com/?to=/:account/billing"
          target="_blank" rel="noreferrer"
          style={{ color: "var(--accent)", textDecoration: "underline" }}>
          Счета в панели Cloudflare <ExternalLink size={11} style={{ display: "inline", verticalAlign: "-1px" }} />
        </a>
      </div>

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}

      <div className="rounded-lg p-3 mb-4"
        style={{ background: "var(--panel)", border: "1px solid var(--line-soft)" }}>
        <p className="micro" style={{ color: "var(--t-low)" }}>Расход за 30 дней</p>
        <p style={{ fontSize: 20, fontWeight: 600, color: "var(--t-hi)" }}>
          {spend.count ? fmtMoney(spend.total, spend.currency) : "—"}
        </p>
        {!spend.count && (
          <p className="micro" style={{ color: "var(--t-low)" }}>
            Нет данных о расходе (или у токена нет прав на биллинг).
          </p>
        )}
      </div>

      <p className="micro" style={{ marginBottom: 6 }}>Предстоящие списания</p>
      {upcoming.length === 0 ? (
        <p className="micro" style={{ color: "var(--t-low)" }}>Нет активных подписок.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead><tr><th>Тариф</th><th>Сумма</th><th>Дата</th><th>Состояние</th></tr></thead>
            <tbody>
              {upcoming.map((s, i) => (
                <tr key={s.id ?? i}>
                  <td>{s.rate_plan?.id || s.id || "—"}</td>
                  <td>{fmtMoney(s.price ?? null, s.currency || "USD")}</td>
                  <td>{fmtDay(s.current_period_end)}</td>
                  <td>{s.state || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
