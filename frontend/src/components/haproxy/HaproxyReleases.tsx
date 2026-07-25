import { useCallback, useEffect, useState } from "react";
import { Package, RefreshCw, Loader2, Trash2, Info } from "lucide-react";
import { Page, PageHeader, fmtDate } from "../infra/ui";
import { haproxyApi, asList } from "./api";
import { useHaproxyReady, NotConnected } from "./HaproxyConnect";
import { fmtBytes } from "./format";
import type { AgentRelease } from "./contracts";

export function HaproxyReleases() {
  const { ready, loading: gate } = useHaproxyReady();
  const [rels, setRels] = useState<AgentRelease[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!ready) return;
    setLoading(true); setErr("");
    try {
      const list = asList<AgentRelease>(await haproxyApi.releases(), "releases");
      setRels([...list].sort((a, b) => b.sequence - a.sequence));
    } catch (e: any) { setErr(e?.message || "Ошибка"); }
    finally { setLoading(false); }
  }, [ready]);
  useEffect(() => { void load(); }, [load]);

  const del = async (id: string) => {
    try { await haproxyApi.deleteRelease(id); setConfirm(null); await load(); }
    catch (e: any) { setErr(e?.message || "Не удалось удалить"); }
  };

  if (!ready) return <Page><NotConnected loading={gate} /></Page>;

  return (
    <Page>
      <PageHeader
        icon={<Package size={18} />}
        title="Релизы Node Agent"
        subtitle="Подписанные версии агента для установки/обновления нод"
        actions={
          <button className="btn btn-soft" onClick={() => void load()} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          </button>
        }
      />

      {err && <p className="text-xs text-[var(--err)] mb-3">{err}</p>}

      <div className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-3 mb-4 text-xs text-[var(--t-low)] flex gap-2">
        <Info size={14} className="flex-none mt-0.5" />
        <span>Загрузка и подпись новых релизов выполняется в самой панели NodeFlow («Настройки → Node Agent»)
          из бинарника install-kit. Здесь — просмотр и удаление уже опубликованных релизов.</span>
      </div>

      {rels.length === 0 ? (
        <div className="card p-8 text-center text-sm text-[var(--t-low)]">Опубликованных релизов пока нет.</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="tbl w-full">
            <thead><tr>
              <th>Версия</th><th>Платформа</th><th className="r">Seq</th><th className="r">Размер</th>
              <th>SHA-256</th><th>Создан</th><th></th>
            </tr></thead>
            <tbody>
              {rels.map(r => (
                <tr key={r.id}>
                  <td className="text-[var(--t-hi)]">{r.version}</td>
                  <td className="text-[var(--t-low)]">{r.os}/{r.arch}</td>
                  <td className="r text-[var(--t-low)]">#{r.sequence}</td>
                  <td className="r text-[var(--t-low)]">{fmtBytes(r.size_bytes)}</td>
                  <td className="text-[var(--t-faint)] font-mono text-[10px]">{r.sha256.slice(0, 16)}…</td>
                  <td className="text-[var(--t-low)]">{fmtDate(r.created_at)}</td>
                  <td className="r">
                    {confirm === r.id ? (
                      <button className="btn btn-danger !py-1" onClick={() => del(r.id)}>Точно?</button>
                    ) : (
                      <button className="btn btn-ghost !p-1.5 text-[var(--err)]" onClick={() => setConfirm(r.id)} title="Удалить"><Trash2 size={14} /></button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
