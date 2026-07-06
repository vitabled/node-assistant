import { useState, useEffect, useCallback } from "react";
import { PieChart, RefreshCw, Loader2, Wallet, TrendingDown, AlertTriangle } from "lucide-react";
import { infraApi, type DashboardSummary } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, fmtNum } from "./ui";

const PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#ef4444", "#14b8a6", "#eab308", "#ec4899"];

export function InfraDashboard() {
  const [d, setD] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setD(await infraApi.dashboard()); }
    catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const b = d?.burnRate;
  return (
    <Page>
      <PageHeader icon={<PieChart size={16} className="text-[var(--accent-hi)]" />} title="Dashboard биллинга"
        subtitle="Сводные финансовые метрики инфраструктуры"
        actions={<button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] hover:bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>} />

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : !d ? (
        <div className="py-16 text-center text-[var(--t-faint)] text-sm">Нет данных.</div>
      ) : (
        <>
          {/* Top widgets */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
            <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-5 flex items-center gap-4">
              <Wallet size={22} className="text-[var(--accent-hi)]" />
              <div>
                <p className="text-[11px] uppercase tracking-widest text-[var(--t-low)]">Общий баланс</p>
                <p className="text-2xl font-semibold text-[var(--t-hi)] tabular-nums">{fmtNum(d.totalBalance, d.baseCurrency)}</p>
                <p className="text-[11px] text-[var(--t-faint)]">по всем провайдерам, в базовой валюте</p>
              </div>
            </div>
            <div className={`rounded-xl border p-5 flex items-center gap-4 ${b?.critical ? "border-[var(--err-line)] bg-[var(--err-dim)]" : "border-[var(--line-soft)] bg-[var(--bg2)]"}`}>
              <TrendingDown size={22} className={b?.critical ? "text-[var(--err)]" : "text-[var(--accent-hi)]"} />
              <div className="flex-1">
                <p className="text-[11px] uppercase tracking-widest text-[var(--t-low)]">Burn Rate</p>
                <p className={`text-2xl font-semibold ${b?.critical ? "text-[var(--err)]" : "text-[var(--t-hi)]"}`}>
                  {b?.daysLeft != null ? `≈ ${b.daysLeft} дн до нуля` : "нет расходов"}
                </p>
                <p className="text-[11px] text-[var(--t-faint)]">
                  {fmtNum(b?.hourly ?? 0)}/ч · {fmtNum(b?.daily ?? 0)}/д · {fmtNum(b?.monthly ?? 0)}/мес
                </p>
                {b?.critical && <p className="text-[11px] text-[var(--err)] flex items-center gap-1 mt-0.5"><AlertTriangle size={11} /> Осталось менее 7 дней!</p>}
              </div>
            </div>
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-4">
              <p className="text-[11px] font-semibold text-[var(--t-low)] uppercase tracking-widest mb-3">Распределение расходов по провайдерам</p>
              <Donut data={d.spendByProvider} />
            </div>
            <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-4">
              <p className="text-[11px] font-semibold text-[var(--t-low)] uppercase tracking-widest mb-3">Траты по месяцам</p>
              <MonthLine data={d.spendByMonth} />
            </div>
          </div>
        </>
      )}
    </Page>
  );
}

export function Donut({ data }: { data: { provider: string; total: number }[] }) {
  const total = data.reduce((s, x) => s + x.total, 0);
  if (total <= 0) return <div className="h-44 flex items-center justify-center text-[var(--t-faint)] text-xs">Нет данных о расходах.</div>;
  const C = 2 * Math.PI * 60; let off = 0;
  return (
    <div className="flex items-center gap-5">
      <svg viewBox="0 0 160 160" className="w-40 h-40 shrink-0">
        <g transform="rotate(-90 80 80)">
          {data.map((x, i) => {
            const dash = (x.total / total) * C;
            const el = <circle key={i} cx="80" cy="80" r="60" fill="none" stroke={PALETTE[i % PALETTE.length]}
              strokeWidth="20" strokeDasharray={`${dash} ${C - dash}`} strokeDashoffset={-off} />;
            off += dash; return el;
          })}
        </g>
        <text x="80" y="84" textAnchor="middle" fill="var(--t-hi)" fontSize="12" fontWeight="600">{Math.round(total).toLocaleString("ru-RU")}</text>
      </svg>
      <div className="flex flex-col gap-1.5 text-xs min-w-0">
        {data.map((x, i) => (
          <div key={i} className="flex items-center gap-2 min-w-0">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: PALETTE[i % PALETTE.length] }} />
            <span className="text-[var(--t-mid)] truncate">{x.provider}</span>
            <span className="text-[var(--t-low)] ml-auto tabular-nums">{Math.round((x.total / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function MonthLine({ data }: { data: { month: string; total: number }[] }) {
  if (data.length < 2) return <div className="h-44 flex items-center justify-center text-[var(--t-faint)] text-xs">Недостаточно данных (нужно ≥ 2 месяцев).</div>;
  const W = 400, H = 176, P = 28, max = Math.max(...data.map(d => d.total), 1);
  const x = (i: number) => P + (i / (data.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const path = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.total).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-44">
      <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="var(--line-soft)" />
      <path d={path} fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinejoin="round" />
      {data.map((d, i) => <circle key={i} cx={x(i)} cy={y(d.total)} r="2.5" fill="#3b82f6" />)}
      {data.map((d, i) => <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fill="var(--t-low)" fontSize="9">{d.month.slice(5)}</text>)}
    </svg>
  );
}
