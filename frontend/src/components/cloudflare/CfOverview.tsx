// «Cloudflare → Обзор» — баланс, способ оплаты, подписки, ближайшее списание.
import { useCallback, useEffect, useState } from "react";
import { Cloud, RefreshCw, AlertTriangle } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { useCfReady, CfNotConnected } from "./gate";
import { getSummary, fmtMoney, fmtDay, messageOf, type CfSummary } from "./api";

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg p-3" style={{ background: "var(--panel)", border: "1px solid var(--line-soft)" }}>
      <p className="micro" style={{ color: "var(--t-low)" }}>{label}</p>
      <p style={{ fontSize: 20, fontWeight: 600, color: "var(--t-hi)", lineHeight: 1.3 }}>{value}</p>
      {hint && <p className="micro" style={{ color: "var(--t-low)" }}>{hint}</p>}
    </div>
  );
}

export function CfOverview() {
  const { ready, loading: gate } = useCfReady();
  const [data, setData] = useState<CfSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback((refresh = false) => {
    setBusy(true);
    getSummary(refresh)
      .then(d => { setData(d); setErr(""); })
      .catch(e => setErr(messageOf(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (ready) load(); }, [ready, load]);

  if (gate) return <Page><p className="micro">Загрузка…</p></Page>;
  if (!ready) return <CfNotConnected title="Cloudflare: обзор" />;

  const p = data?.profile;
  return (
    <Page>
      <PageHeader
        icon={<Cloud size={18} />}
        title="Cloudflare: обзор"
        subtitle="Данные подключённого аккаунта Cloudflare"
        actions={
          <button className="btn" disabled={busy} onClick={() => load(true)}>
            <RefreshCw size={14} /> Обновить
          </button>
        }
      />

      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}

      {(data?.degraded?.length ?? 0) > 0 && (
        <div className="rounded-lg p-3 mb-3 text-xs"
          style={{ background: "var(--warn-dim, var(--bg-soft))", border: "1px solid var(--line)", color: "var(--t-hi)" }}>
          <AlertTriangle size={14} style={{ display: "inline", marginRight: 6, verticalAlign: "-2px" }} />
          Токену не хватает прав на: {data?.degraded.join(", ")}. Эти данные не показаны.
        </div>
      )}

      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
        <Tile label="Баланс" value={fmtMoney(p?.balance ?? null, p?.currency || "")} />
        <Tile
          label="Способ оплаты"
          value={p?.payment_method_present ? "привязан" : "нет"}
          hint={p?.payment_method_present ? undefined : "покупка домена будет отклонена"}
        />
        <Tile label="Подписки в месяц"
          value={fmtMoney(data?.subscriptions_total_monthly ?? null, p?.currency || "USD")}
          hint="приведено к месячному периоду" />
        <Tile label="Ближайшее списание" value={fmtDay(data?.next_charge_at)} />
      </div>

      {!p?.payment_method_present && (
        <p className="micro" style={{ color: "var(--t-low)", marginTop: 12 }}>
          Способ оплаты добавляется только в панели Cloudflare — node-assistant платёжные
          данные не собирает и не хранит.
        </p>
      )}
    </Page>
  );
}
