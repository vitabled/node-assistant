import { useState, useCallback, useEffect, useRef } from "react";
import {
  SlidersHorizontal, Loader2, Plus, Trash2, Save, KeyRound,
  AlertTriangle, RefreshCw, ServerCog,
} from "lucide-react";
import { toast } from "../infra/Toast";
import { panelJobsKey } from "../../auth/store";
import type { PanelJobSummary } from "./PanelDashboard";

// Ф8 — «Переменные»: edit a deployed panel's /opt/remnawave/.env over SSH.
// Pick a panel (from panel_jobs_<id>, successful, target != subpage since the
// panel .env lives on the panel box) → read its .env (secrets masked) → edit /
// add / delete rows → «Применить» (merge-write + docker compose up -d). Untouched
// secret rows are NEVER sent, so the server preserves them (merge). Creds ride
// per-request from savedForm and are never persisted server-side.

// Mirrors the server _ENV_KEY_RE (backend/app/api/panel_deploy.py).
const ENV_KEY = /^[A-Z_][A-Z0-9_]*$/;
const MASK = "••••••••"; // ••••••••

interface EnvPairResp { key: string; value: string; masked: boolean }

interface Row {
  key: string;
  value: string;   // masked-untouched: "" (placeholder shown); edited/new: real
  masked: boolean; // came from the server as a secret
  dirty: boolean;  // user changed the value (or it is a new row)
  isNew: boolean;  // added locally (editable key)
}

function loadPanelJobs(): PanelJobSummary[] {
  try {
    const jobs: PanelJobSummary[] = JSON.parse(localStorage.getItem(panelJobsKey()) ?? "[]");
    // The panel .env is on the PANEL box → only panel/both installs qualify.
    return jobs.filter(j => j.finalStatus === "success" && j.savedForm.target !== "subpage");
  } catch { return []; }
}

const jobLabel = (j: PanelJobSummary) =>
  j.savedForm.panel_domain || j.savedForm.sub_domain || j.savedForm.ip;

type ReadState = "idle" | "loading" | "ok" | "missing" | "error";

export function PanelVariables() {
  const [jobs] = useState<PanelJobSummary[]>(loadPanelJobs);
  const [selectedId, setSelectedId] = useState<string>(jobs[0]?.id ?? "");
  const [rows, setRows] = useState<Row[]>([]);
  const [removed, setRemoved] = useState<string[]>([]);
  const [state, setState] = useState<ReadState>("idle");
  const [errMsg, setErrMsg] = useState<string>("");
  const [rowErr, setRowErr] = useState<string>("");
  const [applying, setApplying] = useState(false);

  const selected = jobs.find(j => j.id === selectedId) ?? null;

  // Monotonic request id: a fast panel switch fires two reads; ignore a stale
  // response so a slow answer for a previous panel can't overwrite the current
  // one (its .env values would show under the wrong panel).
  const reqSeq = useRef(0);
  const load = useCallback(async (job: PanelJobSummary | null) => {
    if (!job) return;
    const myReq = ++reqSeq.current;
    setState("loading");
    setErrMsg(""); setRowErr(""); setRows([]); setRemoved([]);
    const p = job.savedForm;
    try {
      const res = await fetch("/api/panel/env/read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: p.ip, ssh_user: p.ssh_user, ssh_password: p.ssh_password, ssh_port: p.ssh_port,
        }),
      });
      if (myReq !== reqSeq.current) return;   // a newer read superseded this one
      if (res.status === 404) { setState("missing"); return; }
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        if (myReq !== reqSeq.current) return;
        setErrMsg(typeof j.detail === "string" ? j.detail : "Не удалось прочитать .env");
        setState("error");
        return;
      }
      const data = await res.json();
      if (myReq !== reqSeq.current) return;
      const pairs: EnvPairResp[] = Array.isArray(data?.pairs) ? data.pairs : [];
      setRows(pairs.map(pr => ({
        key: pr.key,
        value: pr.masked ? "" : pr.value,
        masked: pr.masked,
        dirty: false,
        isNew: false,
      })));
      setState("ok");
    } catch {
      if (myReq !== reqSeq.current) return;
      setErrMsg("Сеть недоступна");
      setState("error");
    }
  }, []);

  const pickPanel = (id: string) => {
    setSelectedId(id);
    const job = jobs.find(j => j.id === id) ?? null;
    load(job);
  };

  // Auto-read the pre-selected panel once on mount.
  useEffect(() => {
    if (selectedId) load(jobs.find(j => j.id === selectedId) ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── row editing ──
  const setRowValue = (i: number, value: string) =>
    setRows(rs => rs.map((r, k) => (k === i ? { ...r, value, dirty: true } : r)));
  const setRowKey = (i: number, key: string) =>
    setRows(rs => rs.map((r, k) => (k === i ? { ...r, key, dirty: true } : r)));
  const addRow = () =>
    setRows(rs => [...rs, { key: "", value: "", masked: false, dirty: true, isNew: true }]);
  const delRow = (i: number) =>
    setRows(rs => {
      const r = rs[i];
      if (r && !r.isNew && r.key.trim()) setRemoved(rm => (rm.includes(r.key) ? rm : [...rm, r.key]));
      return rs.filter((_, k) => k !== i);
    });

  // A masked row is only sent when the user typed a NON-EMPTY replacement — an
  // empty/untouched masked row is preserved server-side (never wiped).
  const pairsToSend = () =>
    rows.filter(r => {
      const key = r.key.trim();
      if (!key) return false;
      if (r.isNew) return true;
      if (r.masked) return r.dirty && r.value !== "";
      return r.dirty;
    });

  const hasChanges = rows.some(r => r.dirty) || removed.length > 0;

  const apply = useCallback(async () => {
    if (!selected) return;
    setRowErr("");
    const send = pairsToSend();
    // Duplicate check across ALL rows (not just the sent ones): a new row whose
    // key collides with an untouched existing one would otherwise slip through
    // and the server merge would overwrite it (last wins).
    const allSeen = new Set<string>();
    for (const r of rows) {
      const key = r.key.trim();
      if (!key) continue;
      if (allSeen.has(key)) { setRowErr(`Дубликат ключа: ${key}`); return; }
      allSeen.add(key);
    }
    // client-side validation (mirror server)
    for (const r of send) {
      const key = r.key.trim();
      if (!ENV_KEY.test(key)) { setRowErr(`Неверный ключ: ${key || "(пусто)"} — ожидается A-Z, _, 0-9`); return; }
      if (/[\n\r]/.test(r.value)) { setRowErr(`Значение ${key}: без переносов строк`); return; }
    }
    const p = selected.savedForm;
    setApplying(true);
    try {
      const res = await fetch("/api/panel/env/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: p.ip, ssh_user: p.ssh_user, ssh_password: p.ssh_password, ssh_port: p.ssh_port,
          pairs: send.map(r => ({ key: r.key.trim(), value: r.value })),
          deleted: removed,
        }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({ detail: res.statusText }));
        toast(typeof j.detail === "string" ? j.detail : "Ошибка применения", "error");
        return;
      }
      const data = await res.json();
      if (data.restarted) {
        toast(`Переменные применены (${data.applied} изм.) — контейнер перезапущен`, "success");
      } else {
        toast(`Переменные записаны, но контейнер не перезапустился: ${data.detail || "см. логи"}`, "error", 9000);
      }
      // Re-read to reflect the new masked state and clear dirty flags.
      await load(selected);
    } catch {
      toast("Сеть недоступна", "error");
    } finally {
      setApplying(false);
    }
  }, [selected, rows, removed, load]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6 ni-pagebody">
        <div className="flex items-center justify-between mb-6 ni-pagehead">
          <div>
            <h1 className="h1">Переменные</h1>
            <p className="sub">Редактор <code>/opt/remnawave/.env</code> панели по SSH</p>
          </div>
          {selected && (
            <div className="flex items-center gap-2 ni-pagehead-actions">
              <button onClick={() => load(selected)} disabled={state === "loading" || applying}
                className="btn btn-soft" title="Перечитать .env">
                {state === "loading" ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
                Обновить
              </button>
            </div>
          )}
        </div>

        {jobs.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {/* Panel picker */}
            <div className="card card-p mb-4 flex flex-col gap-1.5">
              <label className="micro">Панель</label>
              <select value={selectedId} onChange={e => pickPanel(e.target.value)}
                disabled={applying} className="selectbox">
                <option value="" disabled>— выберите панель —</option>
                {jobs.map(j => <option key={j.id} value={j.id}>{jobLabel(j)}</option>)}
              </select>
            </div>

            {!selectedId ? (
              <p className="text-xs px-1" style={{ color: "var(--t-faint)" }}>
                Выберите панель, чтобы прочитать её переменные.
              </p>
            ) : state === "loading" ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 size={20} className="spin" style={{ color: "var(--t-faint)" }} />
              </div>
            ) : state === "missing" ? (
              <StatusCard tone="warn"
                text="Файл /opt/remnawave/.env не найден — панель ещё не установлена на этом сервере?" />
            ) : state === "error" ? (
              <StatusCard tone="err" text={errMsg || "Не удалось прочитать .env (SSH недоступен?)"}
                onRetry={() => load(selected)} />
            ) : state === "ok" ? (
              <Editor
                rows={rows}
                rowErr={rowErr}
                hasChanges={hasChanges}
                applying={applying}
                onKey={setRowKey}
                onValue={setRowValue}
                onDel={delRow}
                onAdd={addRow}
                onApply={apply}
              />
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function Editor({ rows, rowErr, hasChanges, applying, onKey, onValue, onDel, onAdd, onApply }: {
  rows: Row[];
  rowErr: string;
  hasChanges: boolean;
  applying: boolean;
  onKey: (i: number, v: string) => void;
  onValue: (i: number, v: string) => void;
  onDel: (i: number) => void;
  onAdd: () => void;
  onApply: () => void;
}) {
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
        <SlidersHorizontal size={13} style={{ color: "var(--t-low)" }} />
        <span className="micro">Переменные окружения</span>
        <span className="text-[10px] tabular-nums" style={{ color: "var(--t-faint)", marginLeft: "auto" }}>
          {rows.length}
        </span>
      </div>

      <div className="p-3 flex flex-col gap-2" style={{ overflowX: "auto" }}>
        {rows.length === 0 && (
          <p className="text-xs px-1 py-3 text-center" style={{ color: "var(--t-faint)" }}>
            Нет переменных. Добавьте пару KEY=значение ниже.
          </p>
        )}
        {rows.map((r, i) => (
          <div key={i} className="grid gap-2 items-center"
            style={{ gridTemplateColumns: "minmax(140px, 1fr) minmax(160px, 1.4fr) auto", minWidth: 420 }}>
            <div className="flex items-center gap-1.5 min-w-0">
              {r.masked && <KeyRound size={12} style={{ color: "var(--warn)", flex: "none" }} />}
              <input value={r.key} onChange={e => onKey(i, e.target.value)}
                readOnly={!r.isNew} placeholder="KEY" autoComplete="off" spellCheck={false}
                className="input"
                style={r.isNew ? undefined : { opacity: 0.85, cursor: "default" }} />
            </div>
            <input value={r.value} onChange={e => onValue(i, e.target.value)}
              type={r.masked ? "password" : "text"}
              placeholder={r.masked ? `${MASK} (оставить как есть)` : "значение"}
              autoComplete="off" spellCheck={false} className="input font-mono text-xs" />
            <button type="button" onClick={() => onDel(i)} disabled={applying}
              className="iconbtn danger" style={{ width: 30, height: 30, flex: "none" }} title="Удалить">
              <Trash2 size={13} />
            </button>
          </div>
        ))}
      </div>

      <div className="px-3 pb-3 flex flex-col gap-3">
        <button type="button" onClick={onAdd} disabled={applying}
          className="btn btn-soft self-start">
          <Plus size={13} /> Добавить переменную
        </button>

        {rows.some(r => r.masked) && (
          <div className="flex items-start gap-1.5 px-2.5 py-2 rounded-md border text-[11px]"
            style={{ background: "var(--bg2)", borderColor: "var(--line-soft)", color: "var(--t-low)" }}>
            <KeyRound size={12} className="shrink-0 mt-0.5" style={{ color: "var(--warn)" }} />
            <span>Секретные значения скрыты. Оставьте поле пустым, чтобы сохранить текущее; введите новое — чтобы перезаписать.</span>
          </div>
        )}

        {rowErr && <p className="errmsg">{rowErr}</p>}

        <div className="flex items-center gap-2 pt-1 border-t" style={{ borderColor: "var(--line-soft)" }}>
          <button type="button" onClick={onApply} disabled={!hasChanges || applying}
            className="btn btn-primary">
            {applying ? <><Loader2 size={13} className="spin" /> Применение…</> : <><Save size={13} /> Применить</>}
          </button>
          <p className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--t-faint)" }}>
            <AlertTriangle size={12} /> Применение перезапускает контейнер панели (docker compose up).
          </p>
        </div>
      </div>
    </div>
  );
}

function StatusCard({ tone, text, onRetry }: {
  tone: "warn" | "err"; text: string; onRetry?: () => void;
}) {
  const c = tone === "warn"
    ? { bg: "var(--warn-dim)", line: "var(--warn-line)", fg: "var(--warn)" }
    : { bg: "var(--err-dim)", line: "var(--err-line)", fg: "var(--err)" };
  return (
    <div className="rounded-lg border px-4 py-3 flex items-center gap-2.5 text-sm"
      style={{ background: c.bg, borderColor: c.line, color: c.fg }}>
      <AlertTriangle size={16} className="shrink-0" />
      <span className="flex-1">{text}</span>
      {onRetry && (
        <button onClick={onRetry} className="btn btn-soft" style={{ flex: "none" }}>
          <RefreshCw size={13} /> Повторить
        </button>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <ServerCog size={40} className="mb-4" style={{ color: "var(--t-faint)" }} />
      <p className="text-sm mb-1" style={{ color: "var(--t-low)" }}>Нет установленных панелей</p>
      <p className="text-xs" style={{ color: "var(--t-faint)" }}>
        Сначала установите панель в разделе «Установка панели», затем сможете править её переменные.
      </p>
    </div>
  );
}
