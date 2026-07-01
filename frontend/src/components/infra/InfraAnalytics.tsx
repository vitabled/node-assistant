import { useState, useEffect, useCallback } from "react";
import { Loader2, RefreshCw, PieChart, TrendingDown, AlertTriangle } from "lucide-react";
import { infraApi, type Analytics } from "./api";
import { toast } from "./Toast";

const PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#a855f7", "#ef4444", "#14b8a6", "#eab308", "#ec4899"];

export function InfraAnalytics() {
  const [a, setA] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setA(await infraApi.analytics()); }
    catch (e) { toast(String((e as Error).message), "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const g = a?.burnRate.global;
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h1 className="text-base font-semibold text-white flex items-center gap-2">
              <PieChart size={16} className="text-blue-400" /> Аналитика расходов
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">Распределение трат, динамика и прогноз баланса</p>
          </div>
          <button onClick={load} className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"><RefreshCw size={13} /></button>
        </div>

        {loading ? (
          <div className="py-16 text-center text-gray-600"><Loader2 size={18} className="animate-spin inline" /></div>
        ) : !a ? (
          <div className="py-16 text-center text-gray-600 text-sm">Нет данных.</div>
        ) : (
          <>
            {/* Global burn-rate widget */}
            <div className={`rounded-xl border p-5 mb-6 flex items-center gap-4 ${
              g?.critical ? "border-red-800/50 bg-red-950/40" : "border-gray-800 bg-gray-900/40"}`}>
              <TrendingDown size={22} className={g?.critical ? "text-red-400" : "text-blue-400"} />
              <div className="flex-1">
                <p className="text-sm text-gray-400">Прогноз баланса (Burn Rate)</p>
                <p className={`text-2xl font-semibold ${g?.critical ? "text-red-300" : "text-white"}`}>
                  {g?.daysLeft != null ? `≈ ${g.daysLeft} дней до нуля` : "нет данных о стоимости"}
                </p>
                {g?.critical && (
                  <p className="text-xs text-red-400 flex items-center gap-1 mt-1">
                    <AlertTriangle size={12} /> Критично: осталось менее 7 дней — пополните баланс.
                  </p>
                )}
              </div>
              <div className="text-right text-sm">
                <p className="text-gray-500 text-[11px] uppercase tracking-widest">Общий баланс</p>
                <p className="text-gray-100 tabular-nums">{g?.totalBalance.toLocaleString("ru-RU")}</p>
                <p className="text-gray-500 text-[11px] uppercase tracking-widest mt-1">Траты/мес</p>
                <p className="text-gray-100 tabular-nums">{g?.totalMonthlyCost.toLocaleString("ru-RU")}</p>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
              {/* Donut: spend by provider */}
              <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4">
                <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest mb-3">Распределение трат по провайдерам</p>
                <Donut data={a.spendByProvider} />
              </div>
              {/* Line: spend by month */}
              <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4">
                <p className="text-[11px] font-semibold text-gray-500 uppercase tracking-widest mb-3">Траты по месяцам</p>
                <MonthlyLine data={a.spendByMonth} />
              </div>
            </div>

            {/* Per-provider burn table */}
            <div className="rounded-xl border border-gray-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-900/60 text-gray-500 text-[11px] uppercase tracking-widest">
                  <tr>
                    <th className="text-left font-medium px-4 py-2.5">Провайдер</th>
                    <th className="text-right font-medium px-4 py-2.5">Баланс</th>
                    <th className="text-right font-medium px-4 py-2.5">Траты/мес</th>
                    <th className="text-right font-medium px-4 py-2.5">Дней до нуля</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800/60">
                  {a.burnRate.perProvider.length === 0 ? (
                    <tr><td colSpan={4} className="px-4 py-6 text-center text-gray-600 text-xs">Нет провайдеров.</td></tr>
                  ) : a.burnRate.perProvider.map((p, i) => (
                    <tr key={i} className="hover:bg-gray-900/40">
                      <td className="px-4 py-2.5 text-gray-200">{p.provider}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-gray-300">{p.balance.toLocaleString("ru-RU")} {p.currency}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-gray-400">{p.monthlyCost.toLocaleString("ru-RU")}</td>
                      <td className={`px-4 py-2.5 text-right tabular-nums ${p.critical ? "text-red-400" : p.daysLeft == null ? "text-gray-600" : "text-gray-200"}`}>
                        {p.daysLeft != null ? `${p.daysLeft} дн` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Dependency-free donut chart ───────────────────────────────
function Donut({ data }: { data: { provider: string; total: number }[] }) {
  const total = data.reduce((s, d) => s + d.total, 0);
  if (total <= 0) return <div className="h-44 flex items-center justify-center text-gray-700 text-xs">Нет трат за период.</div>;
  const R = 60, C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="flex items-center gap-5">
      <svg viewBox="0 0 160 160" className="w-40 h-40 shrink-0">
        <g transform="rotate(-90 80 80)">
          {data.map((d, i) => {
            const frac = d.total / total;
            const dash = frac * C;
            const el = (
              <circle key={i} cx="80" cy="80" r={R} fill="none" stroke={PALETTE[i % PALETTE.length]}
                strokeWidth="20" strokeDasharray={`${dash} ${C - dash}`} strokeDashoffset={-offset} />
            );
            offset += dash;
            return el;
          })}
        </g>
        <text x="80" y="84" textAnchor="middle" fill="#e5e7eb" fontSize="13" fontWeight="600">
          {total.toLocaleString("ru-RU")}
        </text>
      </svg>
      <div className="flex flex-col gap-1.5 text-xs min-w-0">
        {data.map((d, i) => (
          <div key={i} className="flex items-center gap-2 min-w-0">
            <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: PALETTE[i % PALETTE.length] }} />
            <span className="text-gray-300 truncate">{d.provider}</span>
            <span className="text-gray-500 ml-auto tabular-nums">{Math.round((d.total / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Dependency-free monthly line chart ────────────────────────
function MonthlyLine({ data }: { data: { month: string; total: number }[] }) {
  if (data.length < 2) return <div className="h-44 flex items-center justify-center text-gray-700 text-xs">Недостаточно данных (нужно ≥ 2 месяцев).</div>;
  const W = 400, H = 176, PAD = 28;
  const max = Math.max(...data.map(d => d.total), 1);
  const x = (i: number) => PAD + (i / (data.length - 1)) * (W - 2 * PAD);
  const y = (v: number) => H - PAD - (v / max) * (H - 2 * PAD);
  const path = data.map((d, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(d.total).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-44">
      <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="#1f2937" />
      <path d={path} fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinejoin="round" />
      {data.map((d, i) => <circle key={i} cx={x(i)} cy={y(d.total)} r="2.5" fill="#3b82f6" />)}
      {data.map((d, i) => (
        <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fill="#6b7280" fontSize="9">{d.month.slice(5)}</text>
      ))}
    </svg>
  );
}
