import { useCallback, useState } from "react";
import { CloudDownload, FileCode, Loader2, Save, ServerCog } from "lucide-react";
import { Seg } from "../../theme/ui";
import { toast } from "../infra/Toast";
import { deployJobsKey } from "../../auth/store";

/**
 * «Развёрнутая страница» (Wave-4 PR-10): рабочий редактор РЕАЛЬНО развёрнутой
 * подписочной страницы. По SSH находим bind-mount контейнера
 * remnawave-subscription-page (директория frontend/ или одиночный index.html),
 * читаем файлы, правим, атомарно пишем обратно (бэкап в .nai-backup/) и
 * перезапускаем контейнер. Страница, встроенная в образ (builtin), не правится —
 * предлагаем задеплоить вариант из каталога.
 */

interface PanelJob {
  taskId: string;
  savedForm?: {
    panel_domain?: string; sub_domain?: string; target?: string;
    ip?: string; ssh_user?: string; ssh_password?: string; ssh_port?: string | number;
  };
  ip?: string;
}

interface DeployedInfo { mode: "dir" | "file" | "builtin" | "missing"; mount: string; files: { path: string; size: number }[] }

interface Creds { ip: string; ssh_port: string; ssh_user: string; ssh_password: string }

const credsBody = (c: Creds) => ({
  ip: c.ip.trim(), ssh_port: parseInt(c.ssh_port, 10) || 22,
  ssh_user: c.ssh_user.trim() || "root", ssh_password: c.ssh_password,
});

export function DeployedEditor() {
  const [creds, setCreds] = useState<Creds>({ ip: "", ssh_port: "22", ssh_user: "root", ssh_password: "" });
  const [info, setInfo] = useState<DeployedInfo | null>(null);
  const [inspecting, setInspecting] = useState(false);
  const [selPath, setSelPath] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [reading, setReading] = useState(false);
  const [writing, setWriting] = useState(false);
  const [restart, setRestart] = useState(true);
  const [preview, setPreview] = useState(false);
  const [err, setErr] = useState("");

  // Серверы панели из карточек деплоя — заполняют креды одним кликом.
  const [panelJobs] = useState<PanelJob[]>(() => {
    try {
      const arr = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
      return Array.isArray(arr) ? arr.filter(j => j.savedForm?.panel_domain || j.savedForm?.sub_domain) : [];
    } catch { return []; }
  });

  const pickJob = (j: PanelJob) => {
    const f = j.savedForm!;
    setCreds({
      ip: f.ip || j.ip || "",
      ssh_port: String(f.ssh_port || 22),
      ssh_user: f.ssh_user || "root",
      ssh_password: f.ssh_password || "",
    });
    setInfo(null); setSelPath(""); setErr("");
  };

  const inspect = useCallback(async () => {
    setInspecting(true); setErr(""); setInfo(null); setSelPath("");
    try {
      const res = await fetch("/api/subpages/deployed/inspect", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credsBody(creds)),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setInfo(d);
      const first = (d.files || [])[0];
      if (first) readFile(first.path, credsBody(creds));
    } finally { setInspecting(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [creds]);

  const readFile = async (path: string, cb = credsBody(creds)) => {
    setReading(true); setErr("");
    try {
      const res = await fetch("/api/subpages/deployed/read", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...cb, path }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setSelPath(path);
      setContent(d.content || "");
      setDirty(false);
    } finally { setReading(false); }
  };

  const save = async () => {
    setWriting(true); setErr("");
    try {
      const res = await fetch("/api/subpages/deployed/write", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...credsBody(creds), path: selPath, content, restart }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`); return; }
      setDirty(false);
      toast(restart ? "Записано, контейнер перезапущен" : "Записано", "success");
    } finally { setWriting(false); }
  };

  const set = (k: keyof Creds) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setCreds(c => ({ ...c, [k]: e.target.value }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* ── подключение ── */}
      <div className="card card-p" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <p className="micro">Сервер панели</p>
        {panelJobs.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {panelJobs.map(j => (
              <button key={j.taskId} type="button"
                className={`chip ${(j.savedForm?.ip || j.ip) === creds.ip ? "accent" : "neutral"}`}
                style={{ cursor: "pointer" }} onClick={() => pickJob(j)}>
                {j.savedForm?.panel_domain || j.savedForm?.sub_domain || j.savedForm?.ip}
              </button>
            ))}
          </div>
        )}
        <div className="grid grid-cols-[1fr_90px] gap-2">
          <div>
            <label className="label">IP сервера</label>
            <input className="input" value={creds.ip} onChange={set("ip")} placeholder="1.2.3.4" />
          </div>
          <div>
            <label className="label">SSH порт</label>
            <input className="input" value={creds.ssh_port} onChange={set("ssh_port")} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">SSH пользователь</label>
            <input className="input" value={creds.ssh_user} onChange={set("ssh_user")} />
          </div>
          <div>
            <label className="label">SSH пароль</label>
            <input className="input" type="password" value={creds.ssh_password}
              onChange={set("ssh_password")} autoComplete="off" />
          </div>
        </div>
        <button className="btn btn-soft" style={{ alignSelf: "flex-start" }} onClick={inspect}
          disabled={inspecting || !creds.ip.trim() || !creds.ssh_password}>
          {inspecting ? <Loader2 size={13} className="spin" /> : <CloudDownload size={13} />}
          Подключить и найти страницу
        </button>
        {err && <p className="errmsg">{err}</p>}
      </div>

      {info && info.mode === "builtin" && (
        <div className="card card-p" style={{ fontSize: 13, color: "var(--t-mid)" }}>
          На сервере страница <strong>встроена в образ</strong> контейнера — править её файлы нельзя.
          Загрузите свою страницу в «Каталог» и задеплойте её вариантом (Панель → переустановка
          подписочной страницы) — тогда здесь появится редактируемая директория.
        </div>
      )}
      {info && info.mode === "missing" && (
        <div className="card card-p" style={{ fontSize: 13, color: "var(--warn)" }}>
          Контейнер remnawave-subscription-page на сервере не найден.
        </div>
      )}

      {info && (info.mode === "dir" || info.mode === "file") && (
        <div style={{ display: "grid", gap: 14, gridTemplateColumns: "minmax(200px,260px) 1fr", alignItems: "start" }}>
          {/* ── файлы ── */}
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
              <FileCode size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">Файлы страницы</span>
            </div>
            <div className="p-2 flex flex-col gap-0.5" style={{ maxHeight: 420, overflowY: "auto" }}>
              {info.files.map(f => {
                const on = f.path === selPath;
                return (
                  <button key={f.path} onClick={() => readFile(f.path)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-md text-left"
                    style={{ background: on ? "var(--accent-dim)" : "transparent" }}>
                    <FileCode size={12} style={{ color: on ? "var(--accent)" : "var(--t-low)", flex: "none" }} />
                    <span className="text-xs trunc" style={{ color: "var(--t-hi)", flex: 1 }}>{f.path}</span>
                    <span className="text-[10px] tabular-nums" style={{ color: "var(--t-faint)" }}>{f.size}Б</span>
                  </button>
                );
              })}
            </div>
            <div className="px-3 py-2 text-[10px]" style={{ borderTop: "1px solid var(--line-soft)", color: "var(--t-faint)" }}>
              {info.mount}
            </div>
          </div>

          {/* ── редактор ── */}
          <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <div className="flex items-center gap-2 px-3 py-2.5" style={{ borderBottom: "1px solid var(--line-soft)" }}>
              <ServerCog size={13} style={{ color: "var(--t-low)" }} />
              <span className="micro">{selPath || "редактор"}</span>
              {dirty && <span className="chip warn" style={{ fontSize: 10 }}>изменён</span>}
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
                <Seg mini options={[{ v: "code", l: "Код" }, { v: "preview", l: "Просмотр" }]}
                  value={preview ? "preview" : "code"}
                  onChange={(v: string) => setPreview(v === "preview")} />
                <button className="btn btn-primary" style={{ padding: "5px 12px", fontSize: 12 }}
                  onClick={save} disabled={writing || !dirty || !selPath}>
                  {writing ? <Loader2 size={12} className="spin" /> : <Save size={12} />} Записать
                </button>
              </div>
            </div>

            {reading ? (
              <div className="flex items-center justify-center" style={{ height: 380 }}>
                <Loader2 size={18} className="spin" style={{ color: "var(--t-faint)" }} />
              </div>
            ) : preview ? (
              <iframe title="Предпросмотр развёрнутой страницы" sandbox="" srcDoc={content}
                style={{ width: "100%", height: 480, border: "none", background: "#fff" }} />
            ) : (
              <textarea className="font-mono text-xs" value={content} spellCheck={false}
                onChange={e => { setContent(e.target.value); setDirty(true); }}
                style={{
                  width: "100%", minHeight: 480, border: "none", outline: "none", resize: "vertical",
                  background: "var(--term-bg)", color: "#c9d1d9", padding: "12px 14px", lineHeight: 1.6,
                }} />
            )}

            <div className="flex items-center gap-2 px-3 py-2" style={{ borderTop: "1px solid var(--line-soft)" }}>
              <label className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--t-low)" }}>
                <input type="checkbox" checked={restart} onChange={e => setRestart(e.target.checked)} />
                перезапустить контейнер после записи
              </label>
              <span className="text-[10px]" style={{ color: "var(--t-faint)", marginLeft: "auto" }}>
                бэкап прежней версии — .nai-backup/ на сервере
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
