import { useCallback, useEffect, useRef, useState } from "react";
import { X, ShieldCheck, AlertCircle, CheckCircle2, Loader2, RefreshCw } from "lucide-react";
import { haproxyApi } from "./api";
import type {
  BootstrapAuthMode, BootstrapSudoMode, HostKeyResult, BootstrapJobResponse, BootstrapNodeRequest,
} from "./contracts";

const STAGE: Record<string, string> = {
  queued: "Ожидает свободного установщика", installing: "Подготовка установки",
  configuration: "Проверка конфигурации", binary: "Подготовка Node Agent",
  identity: "Создание mTLS-идентичности", authentication: "Проверка SSH", connect: "Подключение по SSH",
  prepare: "Подготовка сервера", upload: "Загрузка Node Agent", privilege: "Проверка прав root / sudo",
  install: "Установка и запуск Agent", create_node: "Регистрация ноды", generate_token: "Создание учётных данных",
  store_token: "Сохранение учётных данных", verify_credential: "Проверка mTLS-канала",
  firewall_policy: "Применение политики UFW", finalize: "Завершение", installed: "Agent установлен",
  timeout: "Превышено время установки",
};
const STATUS: Record<BootstrapJobResponse["status"], string> = {
  queued: "Ожидает", running: "Выполняется", installed: "Готово", failed: "Ошибка",
};

const ALGOS: HostKeyResult["algorithm"][] = ["ssh-ed25519", "ecdsa-sha2-nistp256", "rsa-sha2-256"];

export function HaproxyAddNode({ onClose, onInstalled }: { onClose: () => void; onInstalled: () => void }) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [port, setPort] = useState("22");
  const [agentPort, setAgentPort] = useState("4200");
  const [username, setUsername] = useState("root");
  const [authMode, setAuthMode] = useState<BootstrapAuthMode>("password");
  const [password, setPassword] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [keyPass, setKeyPass] = useState("");
  const [sudoMode, setSudoMode] = useState<BootstrapSudoMode>("auto");
  const [sudoPassword, setSudoPassword] = useState("");
  const [algo, setAlgo] = useState<HostKeyResult["algorithm"]>("ssh-ed25519");
  const [allowFw, setAllowFw] = useState(true);
  const [hostKey, setHostKey] = useState<HostKeyResult | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [job, setJob] = useState<BootstrapJobResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const announced = useRef("");

  const clearSecrets = () => { setPassword(""); setPrivateKey(""); setKeyPass(""); setSudoPassword(""); };

  const scan = async () => {
    setBusy(true); setErr(""); setHostKey(null); setAccepted(false);
    try {
      const r = await haproxyApi.hostKey({ address: address.trim(), ssh_port: Number(port), algorithm: algo } as any);
      setHostKey(r); setStep(2);
    } catch (e: any) { setErr(e?.message || "Не удалось проверить SSH"); }
    finally { setBusy(false); }
  };

  const install = async () => {
    if (!hostKey || !accepted) return;
    const req: BootstrapNodeRequest = {
      name: name.trim(), address: address.trim(), ssh_port: Number(port), username: username.trim(),
      auth_mode: authMode, sudo_mode: sudoMode, agent_port: Number(agentPort),
      host_key_sha256: hostKey.fingerprint, host_key_algorithm: hostKey.algorithm,
      allow_firewall_apply: allowFw,
      ...(authMode === "password" ? { password } : { private_key: privateKey, private_key_passphrase: keyPass }),
      ...(sudoPassword ? { sudo_password: sudoPassword } : {}),
    };
    clearSecrets();
    setBusy(true); setErr("");
    try {
      const created = await haproxyApi.bootstrap(req);
      setJob(created); setStep(3);
      if (created.status === "installed") onInstalled();
    } catch (e: any) { setErr(e?.message || "Не удалось установить Node Agent"); setStep(1); }
    finally { setBusy(false); }
  };

  const poll = useCallback(async (jobId: string) => {
    try {
      const next = await haproxyApi.bootstrapJob(jobId);
      setJob(next);
      if (next.status === "installed" && announced.current !== next.job_id) {
        announced.current = next.job_id; onInstalled();
      }
      return next;
    } catch { return null; }
  }, [onInstalled]);

  useEffect(() => {
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    let stop = false; let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      const n = await poll(job.job_id);
      if (!stop) timer = setTimeout(tick, n && (n.status === "queued" || n.status === "running") ? 1000 : 2000);
    };
    timer = setTimeout(tick, 400);
    return () => { stop = true; clearTimeout(timer); };
  }, [job?.job_id, job?.status, poll]);

  const connValid = name.trim() && address.trim() && username.trim() && Number(port) > 0 && Number(agentPort) > 0
    && (authMode === "password" ? password : privateKey)
    && (sudoMode !== "password" || authMode === "password" || sudoPassword);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
      onMouseDown={e => e.target === e.currentTarget && !busy && onClose()}>
      <div className="bg-[var(--bg1)] border border-[var(--line)] rounded-xl w-full max-w-lg p-5 max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-[var(--t-hi)]">Добавить ноду</h2>
          <button className="btn btn-ghost !p-1" onClick={() => !busy && onClose()}><X size={16} /></button>
        </div>

        {/* stepper */}
        <div className="flex items-center gap-2 mb-4 text-[11px]">
          {["Подключение", "Ключ хоста", "Установка"].map((l, i) => (
            <span key={l} className="flex items-center gap-1"
              style={{ color: step >= (i + 1) ? "var(--accent-hi)" : "var(--t-faint)" }}>
              <b className="inline-grid place-items-center w-4 h-4 rounded-full text-[9px]"
                style={{ background: step >= (i + 1) ? "var(--accent)" : "var(--bg3)", color: step >= (i + 1) ? "var(--accent-ink)" : "var(--t-low)" }}>{i + 1}</b>
              {l}{i < 2 && <span className="text-[var(--t-faint)] ml-1">·</span>}
            </span>
          ))}
        </div>

        {err && <div className="mb-3 text-xs text-[var(--err)] flex items-center gap-1.5"><AlertCircle size={13} />{err}</div>}

        {step === 1 && (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <L label="Название"><input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="edge-msk-01" /></L>
              <L label="IP-адрес"><input className="input" value={address} onChange={e => setAddress(e.target.value)} placeholder="10.10.2.31" spellCheck={false} /></L>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <L label="SSH-порт"><input className="input" value={port} onChange={e => setPort(e.target.value)} inputMode="numeric" /></L>
              <L label="Пользователь"><input className="input" value={username} onChange={e => setUsername(e.target.value)} /></L>
              <L label="Порт mTLS"><input className="input" value={agentPort} onChange={e => setAgentPort(e.target.value)} inputMode="numeric" /></L>
            </div>
            <L label="Способ входа">
              <div className="seg mini accent">
                <button className={authMode === "password" ? "on" : ""} onClick={() => { setAuthMode("password"); clearSecrets(); }}>Пароль</button>
                <button className={authMode === "private_key" ? "on" : ""} onClick={() => { setAuthMode("private_key"); clearSecrets(); }}>Приватный ключ</button>
              </div>
            </L>
            {authMode === "password" ? (
              <L label="Пароль SSH"><input className="input" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" /></L>
            ) : (
              <>
                <L label="Приватный SSH-ключ"><textarea className="input" rows={4} value={privateKey} onChange={e => setPrivateKey(e.target.value)}
                  placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" spellCheck={false} /></L>
                <L label="Пароль ключа (если есть)"><input className="input" type="password" value={keyPass} onChange={e => setKeyPass(e.target.value)} autoComplete="new-password" /></L>
              </>
            )}
            <div className="grid grid-cols-2 gap-3">
              <L label="Права на сервере">
                <select className="selectbox" value={sudoMode} onChange={e => { setSudoMode(e.target.value as BootstrapSudoMode); setSudoPassword(""); }}>
                  <option value="auto">Определить автоматически</option>
                  <option value="root">Вход под root</option>
                  <option value="passwordless">sudo без пароля</option>
                  <option value="password">sudo с паролем</option>
                </select>
              </L>
              <L label="Алгоритм ключа хоста">
                <select className="selectbox" value={algo} onChange={e => setAlgo(e.target.value as HostKeyResult["algorithm"])}>
                  {ALGOS.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </L>
            </div>
            {sudoMode === "password" && (
              <L label="Пароль sudo"><input className="input" type="password" value={sudoPassword} onChange={e => setSudoPassword(e.target.value)} autoComplete="new-password" /></L>
            )}
            <label className="flex items-center gap-2 text-xs text-[var(--t-mid)] cursor-pointer">
              <input type="checkbox" checked={allowFw} onChange={e => setAllowFw(e.target.checked)} />
              Разрешить Agent открывать listener-порты в UFW (служебный порт наружу не открывается)
            </label>
            <div className="flex justify-between mt-1">
              <button className="btn btn-soft" onClick={onClose}>Отмена</button>
              <button className="btn btn-primary" onClick={scan} disabled={busy || !connValid}>
                {busy && <Loader2 size={14} className="animate-spin" />} Получить ключ хоста
              </button>
            </div>
          </div>
        )}

        {step === 2 && hostKey && (
          <div className="flex flex-col gap-3">
            <div className="rounded-lg border border-[var(--accent-line)] bg-[var(--accent-dim)] p-3">
              <p className="text-xs font-medium text-[var(--accent-hi)] flex items-center gap-1.5 mb-1.5"><ShieldCheck size={13} /> Сверьте отпечаток SSH-ключа хоста</p>
              <code className="text-[11px] text-[var(--t-hi)] break-all">{hostKey.fingerprint}</code>
              <p className="text-[10px] text-[var(--t-low)] mt-1">{hostKey.algorithm}{hostKey.os ? ` · ${hostKey.os}/${hostKey.arch}` : ""}</p>
            </div>
            <div className="text-xs text-[var(--t-low)] grid grid-cols-2 gap-2">
              <div><span className="text-[var(--t-faint)]">Нода:</span> {name} <span className="text-[var(--t-faint)]">({address}:{port})</span></div>
              <div><span className="text-[var(--t-faint)]">Вход:</span> {authMode === "password" ? "пароль" : "ключ"} · {username} · {sudoMode}</div>
            </div>
            <label className="flex items-center gap-2 text-xs text-[var(--t-mid)] cursor-pointer">
              <input type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)} />
              Я сверил отпечаток и доверяю этому ключу
            </label>
            <div className="flex justify-between mt-1">
              <button className="btn btn-soft" onClick={() => { setStep(1); setAccepted(false); }}>Назад</button>
              <button className="btn btn-primary" onClick={install} disabled={busy || !accepted}>
                {busy && <Loader2 size={14} className="animate-spin" />} Установить Node Agent
              </button>
            </div>
          </div>
        )}

        {step === 3 && job && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              {job.status === "installed" ? <CheckCircle2 size={26} className="text-[var(--ok)]" />
                : job.status === "failed" ? <AlertCircle size={26} className="text-[var(--err)]" />
                : <Loader2 size={24} className="animate-spin text-[var(--accent-hi)]" />}
              <div>
                <p className="text-sm font-medium text-[var(--t-hi)]">
                  {job.status === "installed" ? "Нода установлена" : job.status === "failed" ? "Установка не завершена" : "Устанавливаем Node Agent"}
                </p>
                <p className="text-xs text-[var(--t-low)]">{STAGE[job.stage] ?? "Безопасная установка"} · {STATUS[job.status]}</p>
              </div>
            </div>
            {(job.status === "queued" || job.status === "running") && (
              <p className="text-[11px] text-[var(--t-low)]">Окно можно закрыть — установка продолжится на сервере.</p>
            )}
            {job.status === "installed" && (
              <>
                {job.node_id && <code className="text-[11px] text-[var(--t-low)] break-all">{job.node_id}</code>}
                <button className="btn btn-primary self-end" onClick={onClose}>Готово</button>
              </>
            )}
            {job.status === "failed" && (
              <>
                {job.failure_summary && (
                  <div className="rounded-lg border border-[var(--err-line)] bg-[var(--err-dim)] p-3 text-xs text-[var(--err)]">
                    {job.failure_summary}
                    {job.failure_code && <code className="block mt-1 text-[10px] opacity-80">{job.failure_code}{job.exit_code ? ` · exit ${job.exit_code}` : ""}</code>}
                  </div>
                )}
                <p className="text-[11px] text-[var(--t-low)]">Параметры доступа удалены из памяти панели. Автоповтор не запускается.</p>
                <div className="flex justify-between">
                  <button className="btn btn-soft" onClick={() => void poll(job.job_id)}><RefreshCw size={13} /> Проверить снова</button>
                  <button className="btn btn-primary" onClick={() => { setJob(null); setHostKey(null); setAccepted(false); setErr(""); setStep(1); }}>Изменить данные</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function L({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-col gap-1"><label className="label">{label}</label>{children}</div>;
}
