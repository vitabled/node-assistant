import { useState, useEffect, useCallback } from "react";
import { MessageCircle, RefreshCw, Loader2, Send, User } from "lucide-react";
import { bedolagaApi, type Ticket } from "./api";
import { toast } from "../infra/Toast";
import { Page, PageHeader, fmtDate } from "../infra/ui";
import { NotConfigured } from "./NotConfigured";

const STATUS_LABEL: Record<string, string> = {
  open: "Открыт", pending: "Ожидает", answered: "Отвечено", closed: "Закрыт",
};
const STATUS_COLOR: Record<string, string> = {
  open: "text-[var(--accent-hi)]", pending: "text-[#f59e0b]",
  answered: "text-[var(--t-mid)]", closed: "text-[var(--t-faint)]",
};

export function BedolagaChats() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [notConfigured, setNotConfigured] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [selected, setSelected] = useState<Ticket | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await bedolagaApi.listTickets(statusFilter || undefined, 100, 0);
      setTickets(data.items || []);
      setNotConfigured(!!data.not_configured);
      if (data.error) toast(data.error, "error");
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, [statusFilter]);
  useEffect(() => { load(); }, [load]);

  const openTicket = async (t: Ticket) => {
    setSelected(t);
    try {
      const full = await bedolagaApi.getTicket(t.id);
      setSelected(full);
    } catch (e) { toast((e as Error).message, "error"); }
  };

  const sendReply = async () => {
    if (!selected || !reply.trim()) return;
    setSending(true);
    try {
      const r = await bedolagaApi.replyTicket(selected.id, reply.trim());
      if (r.ok) { toast("Ответ отправлен", "success"); setReply(""); }
      else toast(r.error || "Ошибка отправки", "error");
    } catch (e) { toast((e as Error).message, "error"); }
    setSending(false);
  };

  if (!loading && notConfigured) {
    return (
      <Page>
        <PageHeader icon={<MessageCircle size={16} className="text-[var(--accent-hi)]" />} title="Чаты клиентов" />
        <NotConfigured />
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader icon={<MessageCircle size={16} className="text-[var(--accent-hi)]" />} title="Чаты клиентов"
        subtitle="Тикеты поддержки бедолаги"
        actions={<>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="selectbox text-xs">
            <option value="">Все статусы</option>
            <option value="open">Открытые</option>
            <option value="pending">Ожидают</option>
            <option value="answered">Отвечено</option>
            <option value="closed">Закрытые</option>
          </select>
          <button onClick={load} className="p-2 rounded-md bg-[var(--bg3)] hover:bg-[var(--bg3)] text-[var(--t-mid)]"><RefreshCw size={13} /></button>
        </>} />

      {loading ? (
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      ) : tickets.length === 0 ? (
        <div className="py-16 text-center text-[var(--t-faint)] text-sm">Открытых тикетов нет.</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
          <div className="flex flex-col gap-2 max-h-[70vh] overflow-y-auto">
            {tickets.map(t => (
              <button key={t.id} onClick={() => openTicket(t)}
                className={`text-left rounded-lg border p-3 transition ${selected?.id === t.id ? "border-[var(--accent)] bg-[var(--bg3)]" : "border-[var(--line-soft)] bg-[var(--bg2)] hover:bg-[var(--bg3)]"}`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-semibold text-[var(--t-hi)] flex items-center gap-1">
                    <User size={11} /> {t.username || t.telegram_id || `#${t.id}`}
                  </span>
                  <span className={`text-[10px] uppercase tracking-wide ${STATUS_COLOR[t.status || ""] || "text-[var(--t-faint)]"}`}>
                    {STATUS_LABEL[t.status || ""] || t.status || "—"}
                  </span>
                </div>
                <p className="text-xs text-[var(--t-low)] truncate">{t.subject || t.last_message || "—"}</p>
                {t.updated_at && <p className="text-[10px] text-[var(--t-faint)] mt-1">{fmtDate(t.updated_at)}</p>}
              </button>
            ))}
          </div>

          <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-4 min-h-[300px] flex flex-col">
            {!selected ? (
              <p className="text-sm text-[var(--t-faint)] m-auto">Выберите тикет слева</p>
            ) : (
              <>
                <div className="flex-1 overflow-y-auto mb-3">
                  <p className="text-sm font-semibold text-[var(--t-hi)] mb-2">
                    Тикет #{selected.id} — {selected.subject || "Без темы"}
                  </p>
                  {Array.isArray((selected as any).messages) && (selected as any).messages.length > 0 ? (
                    <div className="flex flex-col gap-2">
                      {((selected as any).messages as any[]).map((m, i) => (
                        <div key={i} className={`text-xs rounded-lg p-2 max-w-[80%] ${m.from_operator || m.is_operator ? "self-end bg-[var(--accent-dim)] text-[var(--t-hi)]" : "self-start bg-[var(--bg3)] text-[var(--t-mid)]"}`}>
                          {m.text || m.message || JSON.stringify(m)}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-[var(--t-faint)]">{selected.last_message || "Нет сообщений."}</p>
                  )}
                </div>
                <div className="flex gap-2 border-t border-[var(--line-soft)] pt-3">
                  <input value={reply} onChange={e => setReply(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && sendReply()}
                    placeholder="Ответить клиенту…" className="input flex-1 text-xs" />
                  <button onClick={sendReply} disabled={sending || !reply.trim()}
                    className="px-3 py-2 rounded-md bg-[var(--accent)] text-[var(--accent-ink)] text-xs font-medium disabled:opacity-50 flex items-center gap-1">
                    {sending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Page>
  );
}
