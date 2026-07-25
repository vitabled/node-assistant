// Wave-8 §3 — «Обновления». Global self-update: shows the tracked branch's local
// vs remote commit, an auto-update toggle, and «Обновить сейчас» which launches a
// detached DooD sidecar (git pull + compose build/up). Progress is polled from
// the sidecar's status file while it runs. Host-level, not per-account.
import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Download, Loader2, AlertTriangle, CheckCircle2, GitBranch, Save } from "lucide-react";
import { toast } from "../infra/Toast";

interface Progress { step: string; running: boolean; ok: boolean | null; ts: number }
interface Status {
  docker: boolean;
  branch: string;
  local: string;
  remote: string;
  behind: boolean;
  subject: string;
  auto_update: boolean;
  error?: string;
  progress?: Progress | null;
}

const short = (h: string) => (h || "").slice(0, 8) || "—";

export function UpdatesTab() {
  const [st, setSt] = useState<Status | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState(false);
  const [branch, setBranch] = useState("");
  const [image, setImage] = useState("");
  const [auto, setAuto] = useState(false);

  const load = useCallback(async () => {
    try {
      const d: Status = await fetch("/api/updates/status").then(r => r.json());
      setSt(d);
      setBranch(prev => prev || d.branch || "");
      setAuto(!!d.auto_update);
    } catch { /* transient (backend may be restarting mid-update) */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  // While the sidecar runs, keep polling its status file.
  useEffect(() => {
    if (!st?.progress?.running) return;
    const id = window.setInterval(load, 5000);
    return () => clearInterval(id);
  }, [st?.progress?.running, load]);

  const check = async () => { setChecking(true); await load(); setChecking(false); };

  const saveCfg = async () => {
    try {
      await fetch("/api/updates/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_update: auto, branch, image }),
      });
      toast("Настройки обновления сохранены", "success");
      load();
    } catch { toast("Не удалось сохранить", "error"); }
  };

  const apply = async () => {
    if (!window.confirm("Запустить обновление? Сервис будет пересобран и перезапущен.")) return;
    setApplying(true);
    try {
      const d = await fetch("/api/updates/apply", { method: "POST" }).then(r => r.json());
      if (!d.ok) { toast(d.warning || "Не удалось запустить обновление", "error"); return; }
      toast("Обновление запущено — сервис скоро перезапустится", "success");
      setTimeout(load, 2000);
    } catch { toast("Ошибка запуска обновления", "error"); }
    finally { setApplying(false); }
  };

  const prog = st?.progress;

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="card card-p flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-[var(--t-hi)] flex items-center gap-1.5">
            <GitBranch size={14} /> Версия сервиса
          </span>
          <button className="btn btn-soft" disabled={checking} onClick={check}>
            {checking ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Проверить
          </button>
        </div>

        {!st ? (
          <p className="hint">Загрузка…</p>
        ) : !st.docker ? (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
            <AlertTriangle size={14} className="shrink-0" /> {st.error || "Docker/compose недоступны — самообновление невозможно"}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div><span className="text-[var(--t-low)]">Ветка</span><p className="text-[var(--t-hi)]">{st.branch || "—"}</p></div>
              <div><span className="text-[var(--t-low)]">Текущий коммит</span><p className="tabular-nums text-[var(--t-hi)]">{short(st.local)}</p></div>
              <div><span className="text-[var(--t-low)]">Удалённый коммит</span><p className="tabular-nums text-[var(--t-hi)]">{short(st.remote)}</p></div>
              <div><span className="text-[var(--t-low)]">Статус</span>
                <p className={st.behind ? "text-[var(--warn)]" : "text-[var(--ok)]"}>
                  {st.behind ? "Доступно обновление" : "Актуально"}
                </p></div>
            </div>
            {st.behind && st.subject && (
              <p className="hint">Последнее изменение: «{st.subject}»</p>
            )}
          </>
        )}
      </div>

      {/* progress while updating */}
      {prog && (prog.running || prog.ok === false) && (
        <div className="card card-p flex items-center gap-2 text-xs">
          {prog.running
            ? <Loader2 size={14} className="animate-spin text-[var(--accent-hi)]" />
            : <AlertTriangle size={14} className="text-[var(--err)]" />}
          <span className="text-[var(--t-mid)]">
            {prog.running ? `Обновление: этап «${prog.step}»…` : `Обновление прервано на этапе «${prog.step}»`}
          </span>
        </div>
      )}
      {prog && !prog.running && prog.ok === true && (
        <div className="card card-p flex items-center gap-2 text-xs text-[var(--ok)]">
          <CheckCircle2 size={14} /> Обновление завершено.
        </div>
      )}

      <div className="card card-p flex flex-col gap-3">
        <span className="text-sm font-semibold text-[var(--t-hi)]">Настройки обновления</span>

        <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
          Автообновление при новой версии в ветке
        </label>

        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1">
            <label className="label">Ветка</label>
            <input value={branch} onChange={e => setBranch(e.target.value)}
              placeholder="напр. main" spellCheck={false} className="input" />
          </div>
          <div className="flex flex-col gap-1">
            <label className="label">Docker-образ обновления</label>
            <input value={image} onChange={e => setImage(e.target.value)}
              placeholder="docker:cli" spellCheck={false} className="input" />
          </div>
        </div>

        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertTriangle size={14} className="shrink-0" /> Обновление пересобирает и перезапускает весь стек — панель будет недоступна 1–2 минуты. Действие затрагивает ВЕСЬ сервер (не только ваш аккаунт).
        </div>

        <div className="flex items-center gap-2">
          <button className="btn btn-primary" onClick={saveCfg}>
            <Save size={14} /> Сохранить
          </button>
          <button className="btn" disabled={applying || !st?.docker} onClick={apply} title="Обновить прямо сейчас">
            {applying ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Обновить сейчас
          </button>
        </div>
      </div>
    </div>
  );
}
