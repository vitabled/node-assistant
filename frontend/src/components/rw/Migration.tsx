import { useState } from "react";
import { Eye, KeyRound, ArrowLeftRight, ShieldAlert, X, Copy, Check } from "lucide-react";
import { useTaskStream } from "../../hooks/useTaskStream";
import { TerminalOutput } from "../TerminalOutput";
import { toast } from "../infra/Toast";

interface Preview { total_users: number; inbound_tags: string[]; will_not_migrate: string[] }
interface RealityReport { applied: boolean; matched: string[]; unmatched: string[] }

export function Migration() {
  // source (Marzban admin API)
  const [mzUrl, setMzUrl] = useState("");
  const [mzUser, setMzUser] = useState("");
  const [mzPass, setMzPass] = useState("");
  // target (Remnawave)
  const [rwUrl, setRwUrl] = useState("");
  const [rwToken, setRwToken] = useState("");
  // options
  const [preserveStatus, setPreserveStatus] = useState(true);
  const [preserveSubhash, setPreserveSubhash] = useState(true);
  const [squads, setSquads] = useState("");
  const [profileUuid, setProfileUuid] = useState("");
  // marzban ssh (legacy secret)
  const [mzIp, setMzIp] = useState("");
  const [mzSshUser, setMzSshUser] = useState("root");
  const [mzSshPass, setMzSshPass] = useState("");

  const [preview, setPreview] = useState<Preview | null>(null);
  const [reality, setReality] = useState<RealityReport | null>(null);
  const [legacy, setLegacy] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [runTask, setRunTask] = useState<string | null>(null);

  const marzbanReady = mzUrl.trim() && mzUser.trim() && mzPass.trim();
  const targetReady = rwUrl.trim() && rwToken.trim();

  const post = async (path: string, body: any) => {
    const r = await fetch(`/api/migrate/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new Error(data?.detail || "Ошибка");
    return data;
  };

  const marzbanCreds = () => ({ marzban_url: mzUrl.trim(), marzban_username: mzUser.trim(), marzban_password: mzPass });
  const targetCreds = () => ({ remnawave_url: rwUrl.trim(), remnawave_token: rwToken });

  const doPreview = async () => {
    setBusy("preview");
    try { setPreview(await post("preview", marzbanCreds())); }
    catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
    finally { setBusy(null); }
  };

  const doReality = async () => {
    if (!profileUuid.trim()) { toast("Укажите UUID config-profile", "error"); return; }
    setBusy("reality");
    try {
      const rep = await post("reality", { ...marzbanCreds(), ...targetCreds(), config_profile_uuid: profileUuid.trim() });
      setReality(rep);
      toast(rep.applied ? `Reality перенесён: ${rep.matched.join(", ")}` : "Совпадающих inbounds нет", rep.applied ? "success" : "info");
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
    finally { setBusy(null); }
  };

  const doMigrate = async () => {
    if (!confirm("Миграция ПИШЕТ пользователей в целевую Remnawave-панель. Продолжить?")) return;
    setBusy("run");
    try {
      const data = await post("run", {
        ...marzbanCreds(), ...targetCreds(),
        preserve_status: preserveStatus, preserve_subhash: preserveSubhash,
        internal_squad_uuids: squads.split(/[\s,]+/).filter(Boolean),
        confirm: true,
      });
      setRunTask(data.task_id);
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
    finally { setBusy(null); }
  };

  const doLegacy = async () => {
    if (!mzIp.trim()) { toast("Укажите IP Marzban-сервера", "error"); return; }
    setBusy("legacy");
    try {
      const data = await post("legacy-secret", { ip: mzIp.trim(), ssh_port: 22, ssh_user: mzSshUser.trim() || "root", ssh_password: mzSshPass });
      setLegacy(data.secret_key);
    } catch (e) { toast(e instanceof Error ? e.message : "Ошибка", "error"); }
    finally { setBusy(null); }
  };

  return (
    <div className="flex-1 overflow-y-auto ni-pagebody">
      <div className="max-w-3xl mx-auto px-6 py-6 flex flex-col gap-5">
        <div className="ni-pagehead">
          <h1 className="text-base font-semibold text-[var(--t-hi)] flex items-center gap-2"><ArrowLeftRight size={17} /> Миграция Marzban → Remnawave</h1>
          <p className="text-xs text-[var(--t-low)] mt-0.5">Пользователи через официальный remnawave/migrate + перенос Reality-ключей.</p>
        </div>

        {/* source + target */}
        <Section title="1. Источник (Marzban) и цель (Remnawave)">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Marzban URL"><input className="input" value={mzUrl} onChange={e => setMzUrl(e.target.value)} placeholder="https://marzban.example" /></Field>
            <Field label="Marzban admin login"><input className="input" value={mzUser} onChange={e => setMzUser(e.target.value)} /></Field>
            <Field label="Marzban admin пароль"><input className="input" type="password" value={mzPass} onChange={e => setMzPass(e.target.value)} /></Field>
            <div />
            <Field label="Remnawave URL"><input className="input" value={rwUrl} onChange={e => setRwUrl(e.target.value)} placeholder="https://panel.example" /></Field>
            <Field label="Remnawave API-токен"><input className="input" type="password" value={rwToken} onChange={e => setRwToken(e.target.value)} /></Field>
          </div>
        </Section>

        {/* preview */}
        <Section title="2. Предпросмотр">
          <button onClick={doPreview} disabled={!marzbanReady || !!busy}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] disabled:opacity-40"><Eye size={14} />{busy === "preview" ? "..." : "Предпросмотр"}</button>
          {preview && (
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <p className="text-[var(--t-hi)]">Пользователей: <b>{preview.total_users}</b></p>
              <p className="text-[var(--t-mid)] text-xs">Inbounds: {preview.inbound_tags.join(", ") || "—"}</p>
              <div className="px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-xs text-[var(--warn)]">
                <div className="flex items-center gap-1 font-medium mb-1"><ShieldAlert size={12} /> НЕ переносится автоматически:</div>
                <ul className="list-disc ml-4">{preview.will_not_migrate.map((x, i) => <li key={i}>{x}</li>)}</ul>
              </div>
            </div>
          )}
        </Section>

        {/* reality */}
        <Section title="3. Перенос Reality-ключей">
          <Field label="UUID config-profile (Remnawave)"><input className="input" value={profileUuid} onChange={e => setProfileUuid(e.target.value)} placeholder="uuid профиля с inbounds" /></Field>
          <button onClick={doReality} disabled={!marzbanReady || !targetReady || !!busy}
            className="flex items-center gap-2 mt-2 px-3 py-1.5 rounded-lg text-sm font-medium border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] disabled:opacity-40"><KeyRound size={14} />{busy === "reality" ? "..." : "Перенести Reality"}</button>
          {reality && (
            <p className="mt-2 text-xs text-[var(--t-mid)]">
              Совпало: {reality.matched.join(", ") || "—"}
              {reality.unmatched.length > 0 && <span className="text-[var(--warn)]"> · без пары: {reality.unmatched.join(", ")}</span>}
            </p>
          )}
        </Section>

        {/* migrate */}
        <Section title="4. Миграция пользователей">
          <div className="flex flex-wrap gap-4 mb-3">
            <Toggle label="preserve_status" checked={preserveStatus} onChange={setPreserveStatus} />
            <Toggle label="preserve_subhash" checked={preserveSubhash} onChange={setPreserveSubhash} />
          </div>
          <Field label="Internal-squad UUID (через запятую, опц.)"><input className="input" value={squads} onChange={e => setSquads(e.target.value)} /></Field>
          <button onClick={doMigrate} disabled={!marzbanReady || !targetReady || !!busy}
            className="flex items-center gap-2 mt-3 px-4 py-2 rounded-lg font-semibold text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
            <ArrowLeftRight size={14} />{busy === "run" ? "..." : "Мигрировать пользователей"}
          </button>
        </Section>

        {/* legacy */}
        <Section title="5. Legacy-ссылки (secret_key Marzban)">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="IP Marzban-сервера"><input className="input" value={mzIp} onChange={e => setMzIp(e.target.value)} /></Field>
            <Field label="SSH user"><input className="input" value={mzSshUser} onChange={e => setMzSshUser(e.target.value)} /></Field>
            <Field label="SSH пароль"><input className="input" type="password" value={mzSshPass} onChange={e => setMzSshPass(e.target.value)} /></Field>
          </div>
          <button onClick={doLegacy} disabled={!!busy} className="flex items-center gap-2 mt-2 px-3 py-1.5 rounded-lg text-sm font-medium border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)] disabled:opacity-40">
            <KeyRound size={14} />{busy === "legacy" ? "..." : "Прочитать secret_key"}
          </button>
          {legacy && (
            <div className="mt-3 flex flex-col gap-1">
              <CopyRow label="MARZBAN_LEGACY_SECRET_KEY" value={legacy} />
              <p className="text-[11px] text-[var(--t-faint)]">Добавьте в .env панели + включите MARZBAN_LEGACY_LINK_ENABLED, чтобы старые ссылки подписки продолжили работать.</p>
            </div>
          )}
        </Section>
      </div>

      {runTask && <MigrateStreamModal taskId={runTask} onClose={() => setRunTask(null)} />}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card card-p flex flex-col">
      <span className="text-sm font-semibold text-[var(--t-hi)] mb-3">{title}</span>
      {children}
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1"><span className="micro">{label}</span>{children}</label>;
}
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 rounded-full transition-colors ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${checked ? "translate-x-4" : ""}`} />
      </button>{label}
    </label>
  );
}
function CopyRow({ label, value }: { label: string; value: string }) {
  const [c, setC] = useState(false);
  return (
    <div className="flex flex-col gap-1">
      <span className="micro">{label}</span>
      <div className="flex items-center gap-2">
        <input className="input font-mono text-xs" readOnly value={value} type="password" onFocus={e => e.currentTarget.select()} />
        <button onClick={async () => { try { await navigator.clipboard.writeText(value); setC(true); setTimeout(() => setC(false), 1500); } catch {} }}
          className="p-2 rounded-md border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)]">
          {c ? <Check size={14} className="text-[var(--ok)]" /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}
function MigrateStreamModal({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState("running");
  useTaskStream({ taskId, onLog: (l: string) => setLogs(p => [...p, l]), onStatus: (f: any) => setStatus(f.status) });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay)] p-3">
      <div className="w-full max-w-2xl bg-[var(--bg1)] border border-[var(--line)] rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--line-soft)]">
          <span className="text-sm font-semibold text-[var(--t-hi)]">Миграция {status === "success" ? "✓" : status === "failed" ? "✗" : "..."}</span>
          <button onClick={onClose} className="text-[var(--t-faint)] hover:text-[var(--t-mid)]"><X size={16} /></button>
        </div>
        <div className="p-3 overflow-hidden"><TerminalOutput lines={logs} /></div>
      </div>
    </div>
  );
}
