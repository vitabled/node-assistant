import { useEffect, useState } from "react";
import { Plus, Trash2, Loader2, RefreshCw, X, GitBranch, ShieldAlert } from "lucide-react";
import { useTaskStream } from "../../hooks/useTaskStream";
import { TerminalOutput } from "../TerminalOutput";
import { toast } from "../infra/Toast";
import type { PanelJobSummary } from "./PanelDashboard";

type Role = "primary" | "standby";
interface Member { panel_key: string; priority: number; role: Role }
interface Group {
  id: string;
  name: string;
  auto_sync: boolean;
  interval_hours: number;
  members: Member[];
  last_sync_at: number | null;
  last_sync_status: "success" | "error" | null;
}

function credsOf(job: PanelJobSummary) {
  const f: any = job.savedForm;
  return { ip: f.ip, ssh_port: Number(f.ssh_port) || 22, ssh_user: f.ssh_user, ssh_password: f.ssh_password };
}

export function SyncGroupPanel({ jobs }: { jobs: PanelJobSummary[] }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  const [runTask, setRunTask] = useState<string | null>(null);
  const [syncingKey, setSyncingKey] = useState<string | null>(null);  // double-click guard

  // Only successfully-deployed panels can join a group (they have live creds).
  const panels = jobs.filter(j => j.finalStatus === "success");
  const label = (key: string) => {
    const j = jobs.find(x => x.id === key);
    return j ? ((j.savedForm as any).panel_domain || (j.savedForm as any).ip || key) : key;
  };

  const load = async () => {
    try {
      const r = await fetch("/api/sync/groups");
      if (!r.ok) throw new Error("bad");
      setGroups(await r.json());
    } catch { toast("Не удалось загрузить группы синхронизации", "error"); setGroups([]); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const del = async (id: string) => {
    try {
      const r = await fetch(`/api/sync/groups/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("bad");
    } catch { toast("Не удалось удалить группу", "error"); }
    await load();
  };

  const runSync = async (group: Group, standbyKey: string) => {
    if (syncingKey) return;  // one sync in flight from this panel at a time
    // Re-fetch the group FRESH so priorities/roles edited elsewhere don't make us
    // pick (and ship creds for) the wrong primary.
    let fresh = group;
    try {
      const r = await fetch("/api/sync/groups");
      if (r.ok) { const gs: Group[] = await r.json(); setGroups(gs); fresh = gs.find(g => g.id === group.id) || group; }
    } catch { /* fall back to the in-memory snapshot */ }

    const standbyMember = fresh.members.find(m => m.panel_key === standbyKey);
    if (!standbyMember || standbyMember.role !== "standby") { toast("Узел больше не является standby в группе", "error"); return; }
    const primary = nearestHigher(fresh.members, standbyKey);
    const standbyJob = jobs.find(j => j.id === standbyKey);
    const primaryJob = primary && jobs.find(j => j.id === primary.panel_key);
    if (!standbyJob || !primaryJob) { toast("Нет сохранённых кред для одной из панелей", "error"); return; }
    if (!confirm(`Синхронизация ПЕРЕЗАПИШЕТ базу standby «${label(standbyKey)}» бэкапом «${label(primary!.panel_key)}». Продолжить?`)) return;

    setSyncingKey(standbyKey);
    try {
      const r = await fetch(`/api/sync/groups/${group.id}/run`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          standby_key: standbyKey, confirm: true,
          primary_creds: credsOf(primaryJob), standby_creds: credsOf(standbyJob),
        }),
      });
      const data = await r.json().catch(() => null);
      if (!r.ok) throw new Error(data?.detail || "Ошибка запуска");
      if (!data?.task_id) throw new Error("Некорректный ответ сервера");
      setRunTask(data.task_id);
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
    finally { setSyncingKey(null); }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <GitBranch size={15} className="text-[var(--accent-hi)]" />
        <span className="text-sm font-semibold text-[var(--t-hi)]">Синхронизация панелей</span>
        <button onClick={() => setEditing("new")} disabled={panels.length < 2}
          className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                     bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-40">
          <Plus size={13} /> Группа
        </button>
      </div>
      <p className="text-[11px] text-[var(--t-faint)] flex items-center gap-1">
        <ShieldAlert size={12} /> Синхронизация ДЕСТРУКТИВНА: база standby перезаписывается бэкапом primary.
      </p>

      {loading ? (
        <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-4"><Loader2 size={14} className="animate-spin" /> Загрузка...</div>
      ) : panels.length < 2 ? (
        <p className="text-[13px] text-[var(--t-faint)] py-2">Нужно ≥2 успешно развёрнутых панелей для группы.</p>
      ) : groups.length === 0 ? (
        <p className="text-[13px] text-[var(--t-faint)] py-2">Групп синхронизации пока нет.</p>
      ) : (
        groups.map(g => (
          <div key={g.id} className="rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] p-3 flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-[var(--t-hi)]">{g.name}</span>
              {g.last_sync_status && (
                <span className={`chip ${g.last_sync_status === "success" ? "ok" : "err"}`} style={{ fontSize: 10 }}>
                  {g.last_sync_status === "success" ? "синхр." : "ошибка"}
                </span>
              )}
              <button onClick={() => setEditing(g)} className="ml-auto text-[11px] text-[var(--accent-hi)] hover:underline">изменить</button>
              <button onClick={() => del(g.id)} className="p-1 rounded text-[var(--t-faint)] hover:text-[var(--err)]"><Trash2 size={13} /></button>
            </div>
            <div className="flex flex-col gap-1">
              {[...g.members].sort((a, b) => b.priority - a.priority).map(m => (
                <div key={m.panel_key} className="flex items-center gap-2 text-xs">
                  <span className={`chip ${m.role === "primary" ? "accent" : ""}`} style={{ fontSize: 10 }}>{m.role}</span>
                  <span className="num text-[var(--t-low)]">#{m.priority}</span>
                  <span className="text-[var(--t-mid)] truncate">{label(m.panel_key)}</span>
                  {m.role === "standby" && (
                    <button onClick={() => runSync(g, m.panel_key)} disabled={syncingKey === m.panel_key}
                      className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] disabled:opacity-40">
                      {syncingKey === m.panel_key ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />} Синхронизировать
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      {editing && (
        <GroupEditor
          initial={editing === "new" ? null : editing}
          panels={panels}
          label={label}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
      {runTask && <SyncStreamModal taskId={runTask} onClose={() => { setRunTask(null); load(); }} />}
    </div>
  );
}

function nearestHigher(members: Member[], standbyKey: string): Member | null {
  const s = members.find(m => m.panel_key === standbyKey);
  if (!s || s.role !== "standby") return null;
  const higher = members.filter(m => m.role === "primary" && m.priority > s.priority);
  if (!higher.length) return null;
  return higher.reduce((a, b) => (b.priority < a.priority ? b : a));
}

// ── group editor ──────────────────────────────────────────────
function GroupEditor({ initial, panels, label, onClose, onSaved }: {
  initial: Group | null; panels: PanelJobSummary[]; label: (k: string) => string;
  onClose: () => void; onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "Группа");
  const [rows, setRows] = useState<Record<string, { on: boolean; priority: number; role: Role }>>(() => {
    const init: Record<string, { on: boolean; priority: number; role: Role }> = {};
    panels.forEach((p, i) => {
      const m = initial?.members.find(x => x.panel_key === p.id);
      // New group → all panels pre-selected; editing → only its existing members.
      init[p.id] = { on: initial ? !!m : true, priority: m?.priority ?? (panels.length - i) * 10, role: m?.role ?? (i === 0 ? "primary" : "standby") };
    });
    return init;
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = (k: string, patch: Partial<{ on: boolean; priority: number; role: Role }>) =>
    setRows(r => ({ ...r, [k]: { ...r[k], ...patch } }));

  const save = async () => {
    const members = panels.filter(p => rows[p.id].on)
      .map(p => ({ panel_key: p.id, priority: rows[p.id].priority, role: rows[p.id].role }));
    if (members.length < 2) { setErr("Выберите минимум 2 панели"); return; }
    const prios = members.map(m => m.priority);
    if (new Set(prios).size !== prios.length) { setErr("Приоритеты должны быть уникальны"); return; }
    // Every standby must have a strictly-higher primary, else it can never sync.
    const primaries = members.filter(m => m.role === "primary");
    const orphan = members.find(m => m.role === "standby" && !primaries.some(p => p.priority > m.priority));
    if (orphan) { setErr("У каждого standby должен быть primary с бóльшим приоритетом"); return; }
    setErr(null);
    setSaving(true);
    try {
      const body = { name, members };
      const url = initial ? `/api/sync/groups/${initial.id}` : "/api/sync/groups";
      const r = await fetch(url, { method: initial ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw new Error(data?.detail || "Ошибка сохранения");
      onSaved();
    } catch (e) { setErr(e instanceof Error ? e.message : "Ошибка"); }
    finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-3">
      <div className="w-full max-w-lg bg-[var(--bg1)] border border-[var(--line)] rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--line-soft)]">
          <h2 className="text-sm font-semibold text-[var(--t-hi)]">{initial ? "Изменить группу" : "Новая группа"}</h2>
          <button onClick={onClose} className="text-[var(--t-faint)] hover:text-[var(--t-mid)]"><X size={16} /></button>
        </div>
        <div className="overflow-y-auto p-5 flex flex-col gap-4">
          <label className="flex flex-col gap-1">
            <span className="micro">Название</span>
            <input className="input" value={name} onChange={e => setName(e.target.value)} disabled={saving} />
          </label>
          <div className="flex flex-col gap-2">
            <span className="micro">Панели (роль + приоритет; выше число = выше приоритет)</span>
            {panels.map(p => (
              <div key={p.id} className="flex items-center gap-2">
                <input type="checkbox" checked={rows[p.id].on} disabled={saving}
                  onChange={e => set(p.id, { on: e.target.checked })} className="accent-[var(--accent)]" />
                <span className="text-xs text-[var(--t-mid)] flex-1 truncate">{label(p.id)}</span>
                <select className="selectbox !w-28 text-xs" value={rows[p.id].role} disabled={saving || !rows[p.id].on}
                  onChange={e => set(p.id, { role: e.target.value as Role })}>
                  <option value="primary">primary</option>
                  <option value="standby">standby</option>
                </select>
                <input type="number" className="input !w-20" value={rows[p.id].priority} disabled={saving || !rows[p.id].on}
                  onChange={e => set(p.id, { priority: Number(e.target.value) })} />
              </div>
            ))}
          </div>
          {err && <div className="px-3 py-2 rounded-md bg-[var(--err-dim)] border border-[var(--err-line)] text-xs text-[var(--err)]">{err}</div>}
        </div>
        <div className="flex gap-2 px-5 py-4 border-t border-[var(--line-soft)]">
          <button onClick={save} disabled={saving}
            className="ml-auto flex items-center gap-2 px-5 py-2.5 rounded-lg font-semibold text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
            {saving ? <Loader2 size={14} className="animate-spin" /> : null} Сохранить
          </button>
          <button onClick={onClose} className="px-4 py-2.5 rounded-lg text-sm text-[var(--t-mid)] hover:bg-[var(--bg3)]">Отмена</button>
        </div>
      </div>
    </div>
  );
}

// ── sync stream modal ─────────────────────────────────────────
function SyncStreamModal({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<string>("running");
  useTaskStream({
    taskId,
    onLog: (l: string) => setLogs(prev => [...prev, l]),
    onStatus: (f: any) => setStatus(f.status),
  });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-3">
      <div className="w-full max-w-2xl bg-[var(--bg1)] border border-[var(--line)] rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--line-soft)]">
          <span className="text-sm font-semibold text-[var(--t-hi)]">Синхронизация {status === "success" ? "✓" : status === "failed" ? "✗" : "..."}</span>
          {/* Always closable — a dropped WS never leaves the user trapped; the
              backend task keeps running regardless of this modal. */}
          <button onClick={onClose} className="text-[var(--t-faint)] hover:text-[var(--t-mid)]"><X size={16} /></button>
        </div>
        <div className="p-3 overflow-hidden"><TerminalOutput lines={logs} /></div>
      </div>
    </div>
  );
}
