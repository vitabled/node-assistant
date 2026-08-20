import { useState, useEffect, useCallback } from "react";
import { Kanban as KanbanIcon, RefreshCw, Loader2 } from "lucide-react";
import { bedolagaApi, type Ticket } from "./api";
import { toast } from "../infra/Toast";
import { Page, PageHeader, fmtDate } from "../infra/ui";
import { NotConfigured } from "./NotConfigured";

const COLUMNS: { status: string; label: string }[] = [
  { status: "open", label: "Открыт" },
  { status: "pending", label: "Ожидает" },
  { status: "answered", label: "Отвечено" },
  { status: "closed", label: "Закрыт" },
];

export function BedolagaKanban() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [notConfigured, setNotConfigured] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await bedolagaApi.listTickets(undefined, 200, 0);
      setTickets(data.items || []);
      setNotConfigured(!!data.not_configured);
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!loading && notConfigured) {
    return (
      <Page>
        <PageHeader icon={<KanbanIcon size={16} className="text-[var(--accent-hi)]" />} title="Канбан-доска" />
        <NotConfigured />
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader icon={<KanbanIcon size={16} className="text-[var(--accent-hi)]" />} title="Канбан-доска"
        subtitle="Тикеты по статусам"
        actions={<button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] hover:bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>} />

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          {COLUMNS.map(col => {
            const items = tickets.filter(t => (t.status || "open") === col.status);
            return (
              <div key={col.status} className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-3 min-h-[200px]">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-[var(--t-low)] mb-3 flex items-center justify-between">
                  {col.label} <span className="text-[var(--t-faint)]">{items.length}</span>
                </p>
                <div className="flex flex-col gap-2">
                  {items.map(t => (
                    <div key={t.id} className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] p-2.5">
                      <p className="text-xs font-medium text-[var(--t-hi)]">#{t.id} {t.username || t.telegram_id || ""}</p>
                      <p className="text-[11px] text-[var(--t-low)] truncate mt-0.5">{t.subject || t.last_message || "—"}</p>
                      {t.updated_at && <p className="text-[10px] text-[var(--t-faint)] mt-1">{fmtDate(t.updated_at)}</p>}
                    </div>
                  ))}
                  {items.length === 0 && <p className="text-[11px] text-[var(--t-faint)] text-center py-4">Пусто</p>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Page>
  );
}
