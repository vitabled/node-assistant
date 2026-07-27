// «Cloudflare → Подписки» — что оплачивается и когда следующий период.
import { useCallback, useEffect, useState } from "react";
import { ReceiptText, RefreshCw } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { useCfReady, CfNotConnected } from "./gate";
import { listSubscriptions, fmtMoney, fmtDay, messageOf, type CfSubscription } from "./api";

const FREQ: Record<string, string> = {
  weekly: "неделя", monthly: "месяц", quarterly: "квартал", yearly: "год",
};

// Only «оплачено» and the terminal failures get colour; everything else stays
// neutral so a state Cloudflare adds later still renders sensibly.
const STATE_COLOR: Record<string, string> = {
  Paid: "var(--ok, var(--accent))",
  Provisioned: "var(--ok, var(--accent))",
  Trial: "var(--t-low)",
  AwaitingPayment: "var(--warn, var(--t-hi))",
  Failed: "var(--err, var(--t-hi))",
  Cancelled: "var(--t-low)",
  Expired: "var(--t-low)",
};

export function CfSubscriptions() {
  const { ready, loading: gate } = useCfReady();
  const [rows, setRows] = useState<CfSubscription[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback((refresh = false) => {
    setBusy(true);
    listSubscriptions(refresh)
      .then(r => { setRows(Array.isArray(r) ? r : []); setErr(""); })
      .catch(e => setErr(messageOf(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (ready) load(); }, [ready, load]);

  if (gate) return <Page><p className="micro">Загрузка…</p></Page>;
  if (!ready) return <CfNotConnected title="Cloudflare: подписки" />;

  return (
    <Page>
      <PageHeader
        icon={<ReceiptText size={18} />}
        title="Cloudflare: подписки"
        subtitle="Активные подписки аккаунта и их периоды"
        actions={
          <button className="btn" disabled={busy} onClick={() => load(true)}>
            <RefreshCw size={14} /> Обновить
          </button>
        }
      />
      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}
      {!err && rows.length === 0 && !busy && (
        <p className="micro" style={{ color: "var(--t-low)" }}>Подписок нет.</p>
      )}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>Тариф</th><th>Цена</th><th>Период</th><th>Состояние</th><th>Текущий период до</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s, i) => (
                <tr key={s.id ?? i}>
                  <td>{s.rate_plan?.id || s.id || "—"}</td>
                  <td>{fmtMoney(s.price ?? null, s.currency || "USD")}</td>
                  <td>{FREQ[s.frequency || ""] || s.frequency || "—"}</td>
                  <td style={{ color: STATE_COLOR[s.state || ""] || "var(--t-hi)" }}>
                    {s.state || "—"}
                  </td>
                  <td>{fmtDay(s.current_period_end)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
