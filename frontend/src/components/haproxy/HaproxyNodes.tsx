import { useCallback, useEffect, useState } from "react";
import { Boxes, Plus, RefreshCw, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { Page, PageHeader, fmtDate } from "../infra/ui";
import { haproxyApi, asList } from "./api";
import { useHaproxyReady, NotConnected } from "./gate";
import { HaproxyAddNode } from "./HaproxyAddNode";
import { HaproxyNodeDetail } from "./HaproxyNodeDetail";
import { nodeTone, TONE_COLOR, TONE_LABEL } from "./format";
import type { NodeRecord } from "./contracts";

export function HaproxyNodes() {
  const { ready, loading: gate } = useHaproxyReady();
  const [nodes, setNodes] = useState<NodeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    if (!ready) return;
    setLoading(true); setErr("");
    try { setNodes(asList<NodeRecord>(await haproxyApi.nodes(), "nodes")); }
    catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready]);
  useEffect(() => { void load(); }, [load]);

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  return (
    <Page>
      <PageHeader
        icon={<Boxes size={18} />}
        title="Ноды HAProxy"
        subtitle="Серверы под управлением NodeFlow-агента"
        actions={
          <div className="flex items-center gap-2">
            <button className="btn btn-soft" onClick={() => void load()} disabled={loading}>
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            </button>
            <button className="btn btn-primary" onClick={() => setAdding(true)}><Plus size={14} /> Добавить ноду</button>
          </div>
        }
      />

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      {nodes.length === 0 ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">
          Нод пока нет. Нажмите «Добавить ноду», чтобы установить Node Agent по SSH.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {nodes.map(n => {
            const tone = nodeTone(n.status, undefined);
            const isOpen = open === n.id;
            return (
              <div key={n.id} className="card overflow-hidden">
                <button className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--row-hover)]"
                  onClick={() => setOpen(isOpen ? null : n.id)}>
                  {isOpen ? <ChevronDown size={15} className="text-[var(--t-low)]" /> : <ChevronRight size={15} className="text-[var(--t-low)]" />}
                  <span className="w-2 h-2 rounded-full flex-none" style={{ background: TONE_COLOR[tone] }} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-[var(--t-hi)] truncate">{n.name || n.id.slice(0, 8)}</p>
                    <p className="text-[11px] text-[var(--t-low)] truncate">{n.address}</p>
                  </div>
                  <span className="text-[11px]" style={{ color: TONE_COLOR[tone] }}>{TONE_LABEL[tone]}</span>
                  {n.last_seen_at && <span className="text-[10px] text-[var(--t-faint)] hidden md:block">{fmtDate(n.last_seen_at)}</span>}
                </button>
                {isOpen && (
                  <div className="px-4 pb-4 pt-1 border-t border-[var(--line-soft)]">
                    <HaproxyNodeDetail nodeId={n.id} onChanged={load}
                      onDeleted={() => { setOpen(null); void load(); }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {adding && (
        <HaproxyAddNode onClose={() => setAdding(false)}
          onInstalled={() => { void load(); }} />
      )}
    </Page>
  );
}
