// «Cloudflare → Использование» — paygo-расход по датам.
//
// The paygo-usage payload was never probed against a live account, so it is
// normalised defensively: anything that looks like {date, amount} becomes a row,
// and an unrecognised shape is shown as raw JSON instead of an empty screen.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Gauge, RefreshCw } from "lucide-react";
import { Page, PageHeader } from "../infra/ui";
import { useCfReady, CfNotConnected } from "./gate";
import { getUsage, fmtMoney, fmtDay, messageOf } from "./api";

export interface UsageRow { date: string; amount: number | null; label: string; currency: string }

const num = (v: unknown): number | null => {
  const n = typeof v === "string" ? Number(v) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) ? n : null;
};

export function normalizeUsage(raw: unknown): { rows: UsageRow[]; unknown: boolean } {
  const list: unknown[] = Array.isArray(raw)
    ? raw
    : ((raw as Record<string, unknown> | null)?.usage
      || (raw as Record<string, unknown> | null)?.results
      || (raw as Record<string, unknown> | null)?.items
      || []) as unknown[];
  if (!Array.isArray(list) || list.length === 0) return { rows: [], unknown: !!raw && !Array.isArray(raw) };
  const rows: UsageRow[] = [];
  for (const item of list) {
    if (typeof item !== "object" || item === null) continue;
    const o = item as Record<string, unknown>;
    const date = String(o.date ?? o.day ?? o.occurred_at ?? o.period_start ?? "");
    const amount = num(o.amount ?? o.cost ?? o.total ?? o.computed_amount);
    rows.push({
      date,
      amount,
      label: String(o.product ?? o.metric ?? o.description ?? o.rate_plan ?? ""),
      currency: String(o.currency ?? "USD"),
    });
  }
  return { rows, unknown: rows.length === 0 };
}

const daysAgo = (n: number) => {
  const d = new Date(Date.now() - n * 86400_000);
  return d.toISOString().slice(0, 10);
};

function Bars({ rows }: { rows: UsageRow[] }) {
  const points = rows.filter(r => r.amount !== null);
  if (points.length < 2) return null;
  const max = Math.max(...points.map(r => r.amount as number), 0.0001);
  const w = 640, h = 120, gap = 2;
  const bw = Math.max(1, (w - gap * (points.length - 1)) / points.length);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 120, marginBottom: 10 }}
      role="img" aria-label="Расход по датам">
      {points.map((r, i) => {
        const bh = Math.max(1, ((r.amount as number) / max) * (h - 8));
        return (
          <rect key={i} x={i * (bw + gap)} y={h - bh} width={bw} height={bh}
            fill="var(--accent)" opacity={0.85} />
        );
      })}
    </svg>
  );
}

export function CfUsage() {
  const { ready, loading: gate } = useCfReady();
  const [raw, setRaw] = useState<unknown>(null);
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback((period: number, refresh = false) => {
    setBusy(true);
    getUsage(daysAgo(period), daysAgo(0), refresh)
      .then(d => { setRaw(d); setErr(""); })
      .catch(e => setErr(messageOf(e)))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { if (ready) load(days); }, [ready, days, load]);

  const { rows, unknown } = useMemo(() => normalizeUsage(raw), [raw]);

  if (gate) return <Page><p className="micro">Загрузка…</p></Page>;
  if (!ready) return <CfNotConnected title="Cloudflare: использование" />;

  return (
    <Page>
      <PageHeader
        icon={<Gauge size={18} />}
        title="Cloudflare: использование"
        subtitle="Оплачиваемый расход (pay-as-you-go)"
        actions={<>
          <div className="seg">
            {[7, 30].map(d => (
              <button key={d} className={days === d ? "active" : ""} onClick={() => setDays(d)}>
                {d} дн.
              </button>
            ))}
          </div>
          <button className="btn" disabled={busy} onClick={() => load(days, true)}>
            <RefreshCw size={14} /> Обновить
          </button>
        </>}
      />
      {err && <p className="micro" style={{ color: "var(--err, var(--accent))" }}>{err}</p>}
      {!err && rows.length === 0 && !busy && (
        <p className="micro" style={{ color: "var(--t-low)" }}>
          {unknown
            ? "Cloudflare вернул данные в неизвестном формате — ниже сырой ответ."
            : "За выбранный период расхода нет."}
        </p>
      )}
      {rows.length > 0 && <>
        <Bars rows={rows} />
        <div className="overflow-x-auto">
          <table className="tbl">
            <thead><tr><th>Дата</th><th>Позиция</th><th>Сумма</th></tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{fmtDay(r.date) === "—" ? r.date || "—" : fmtDay(r.date)}</td>
                  <td>{r.label || "—"}</td>
                  <td>{fmtMoney(r.amount, r.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>}
      {unknown && raw !== null && (
        <pre className="micro" style={{
          marginTop: 10, padding: 8, overflowX: "auto",
          background: "var(--panel)", border: "1px solid var(--line-soft)", borderRadius: 8,
        }}>{JSON.stringify(raw, null, 2).slice(0, 4000)}</pre>
      )}
    </Page>
  );
}
