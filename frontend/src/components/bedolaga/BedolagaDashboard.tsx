import { useState, useEffect, useCallback } from "react";
import { PieChart, RefreshCw, Loader2, Ticket as TicketIcon, Users, Inbox, HeartPulse } from "lucide-react";
import { bedolagaApi, type DashboardData } from "./api";
import { toast } from "../infra/Toast";
import { Page, PageHeader } from "../infra/ui";
import { NotConfigured } from "./NotConfigured";

export function BedolagaDashboard() {
  const [d, setD] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setD(await bedolagaApi.dashboard()); }
    catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <Page>
      <PageHeader icon={<PieChart size={16} className="text-[var(--accent-hi)]" />} title="BEDOLAGA — Дашборд"
        subtitle="Сводка по саппорт-боту: тикеты, пользователи, статус API"
        actions={<button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] hover:bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>} />

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : !d || d.not_configured ? (
        <NotConfigured />
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <Card icon={<Inbox size={20} />} label="Открытых тикетов" value={d.open_tickets ?? "—"} />
            <Card icon={<TicketIcon size={20} />} label="Всего тикетов" value={d.total_tickets ?? "—"} />
            <Card icon={<Users size={20} />} label="Пользователей бота" value={d.total_users ?? "—"} />
          </div>

          <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-5 flex items-center gap-4">
            <HeartPulse size={22} className={d.bot_health?.status === "ok" ? "text-[var(--accent-hi)]" : "text-[var(--err)]"} />
            <div>
              <p className="text-[11px] uppercase tracking-widest text-[var(--t-low)]">Статус бота</p>
              <p className="text-lg font-semibold text-[var(--t-hi)]">
                {d.bot_health?.status === "ok" ? "Работает" : "Недоступен"}
                {d.bot_health?.bot_version && <span className="text-xs text-[var(--t-faint)] ml-2">v{d.bot_health.bot_version}</span>}
              </p>
            </div>
          </div>

          {d.errors?.length > 0 && (
            <div className="mt-4 rounded-lg border border-[var(--err-line)] bg-[var(--err-dim)] p-3 text-xs text-[var(--err)]">
              {d.errors.map((e, i) => <p key={i}>{e}</p>)}
            </div>
          )}
        </>
      )}
    </Page>
  );
}

function Card({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-5 flex items-center gap-4">
      <span className="text-[var(--accent-hi)]">{icon}</span>
      <div>
        <p className="text-[11px] uppercase tracking-widest text-[var(--t-low)]">{label}</p>
        <p className="text-2xl font-semibold text-[var(--t-hi)] tabular-nums">{value}</p>
      </div>
    </div>
  );
}
