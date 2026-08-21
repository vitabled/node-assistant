import { useCallback, useState } from "react";
import { CheckCircle2, Clipboard, Loader2, Play, ShieldCheck, XCircle } from "lucide-react";
import { TerminalOutput } from "../TerminalOutput";
import { toast } from "../infra/Toast";
import { useTaskStream, type StatusFrame, type TaskStatus } from "../../hooks/useTaskStream";

interface FormState {
  ip: string;
  ssh_port: string;
  ssh_user: string;
  ssh_password: string;
  your_site: string;
  ws_path: string;
  xray_port: string;
  xray_host: string;
}

interface VerifyResult {
  ok: boolean;
  origin: { status: number; ok: boolean };
  vip: { status: number; ok: boolean };
}

const INITIAL: FormState = {
  ip: "",
  ssh_port: "22",
  ssh_user: "root",
  ssh_password: "",
  your_site: "",
  ws_path: "/api/v3/media/ws",
  xray_port: "12080",
  xray_host: "",
};

function Field({ label, value, onChange, type = "text", placeholder, hint }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label">{label}</span>
      <input
        className="input"
        type={type}
        value={value}
        onChange={event => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

async function errorText(response: Response): Promise<string> {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null;
  if (Array.isArray(body?.detail)) {
    return body.detail.map(item => String((item as { msg?: string }).msg ?? item)).join("; ");
  }
  return String(body?.detail ?? response.statusText ?? "Ошибка запроса");
}

export function RegruVipTab() {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusFrame>({ status: "pending", current_step: 0, total_steps: 3 });
  const [logs, setLogs] = useState<string[]>([]);
  const [htaccess, setHtaccess] = useState("");
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);
  const [verifying, setVerifying] = useState(false);

  const set = (key: keyof FormState) => (value: string) => setForm(current => ({ ...current, [key]: value }));
  const running = status.status === "running" || (!!taskId && status.status === "pending");
  const valid = form.ip.trim() && form.ssh_user.trim() && form.ssh_password && form.your_site.trim()
    && form.ws_path.trim() && form.xray_host.trim() && Number(form.xray_port) > 0;

  const onLog = useCallback((line: string) => setLogs(current => [...current, line]), []);
  const onStatus = useCallback((frame: StatusFrame) => {
    setStatus(current => ({
      status: frame.status,
      current_step: frame.current_step < 0 ? current.current_step : frame.current_step,
      total_steps: frame.total_steps < 0 ? current.total_steps : frame.total_steps,
    }));
  }, []);
  const onDone = useCallback((finalStatus: TaskStatus, error: string | null) => {
    if (finalStatus === "success") toast("Reg.ru VIP origin развёрнут", "success");
    else toast(error || "Развёртывание завершилось с ошибкой", "error");
  }, []);

  useTaskStream({ taskId, onLog, onStatus, onDone });

  const payload = () => ({
    ip: form.ip.trim(),
    ssh_port: Number(form.ssh_port) || 22,
    ssh_user: form.ssh_user.trim(),
    ssh_password: form.ssh_password,
    your_site: form.your_site.trim(),
    ws_path: form.ws_path.trim(),
    xray_port: Number(form.xray_port),
    xray_host: form.xray_host.trim(),
  });

  const deploy = async () => {
    setTaskId(null);
    setLogs([]);
    setVerifyResult(null);
    setStatus({ status: "pending", current_step: 0, total_steps: 3 });
    try {
      const response = await fetch("/api/regru-vip/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      if (!response.ok) throw new Error(await errorText(response));
      const body = await response.json() as { task_id: string; htaccess: string };
      setHtaccess(body.htaccess);
      setTaskId(body.task_id);
    } catch (error) {
      setStatus(current => ({ ...current, status: "failed" }));
      toast((error as Error).message, "error");
    }
  };

  const verify = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const response = await fetch("/api/regru-vip/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload()),
      });
      if (!response.ok) throw new Error(await errorText(response));
      setVerifyResult(await response.json() as VerifyResult);
    } catch (error) {
      toast((error as Error).message, "error");
    } finally {
      setVerifying(false);
    }
  };

  const copyHtaccess = async () => {
    try {
      await navigator.clipboard.writeText(htaccess);
      toast(".htaccess скопирован", "success");
    } catch {
      toast("Не удалось скопировать .htaccess", "error");
    }
  };

  return (
    <div className="flex flex-col gap-5 max-w-3xl">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <ShieldCheck size={16} className="text-[var(--accent-hi)]" />
          <h2 className="text-sm font-semibold text-[var(--t-hi)]">VLESS + WebSocket через Reg.ru VIP</h2>
        </div>
        <p className="text-xs text-[var(--t-low)]">
          Панель добавит локальный Xray inbound и nginx origin. Файл .htaccess нужно установить на сайте Reg.ru вручную.
        </p>
      </div>

      <section className="card card-p flex flex-col gap-4">
        <h3 className="micro">SSH ноды</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="IP ноды" value={form.ip} onChange={set("ip")} placeholder="203.0.113.10" />
          <Field label="SSH-порт" value={form.ssh_port} onChange={set("ssh_port")} type="number" placeholder="22" />
          <Field label="SSH-пользователь" value={form.ssh_user} onChange={set("ssh_user")} placeholder="root" />
        </div>
        <Field label="SSH-пароль" value={form.ssh_password} onChange={set("ssh_password")} type="password" />

        <h3 className="micro mt-2">Reg.ru VIP и Xray</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Домен VIP" value={form.your_site} onChange={set("your_site")} placeholder="vip.example.com" />
          <Field label="WebSocket-путь" value={form.ws_path} onChange={set("ws_path")} placeholder="/api/v3/media/ws" />
          <Field label="Локальный порт Xray" value={form.xray_port} onChange={set("xray_port")} type="number" placeholder="12080" />
          <Field label="Host для Xray" value={form.xray_host} onChange={set("xray_host")} placeholder="origin.example.com" />
        </div>

        <button type="button" onClick={deploy} disabled={!valid || running} className="btn btn-primary self-start">
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? "Развёртывание..." : "Развернуть на ноде"}
        </button>
      </section>

      {taskId && (
        <section className="card card-p flex flex-col gap-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--t-mid)]">Шаг {status.current_step} из {status.total_steps}</span>
            <span style={{ color: status.status === "success" ? "var(--ok)" : status.status === "failed" ? "var(--err)" : "var(--accent-hi)" }}>
              {status.status === "success" ? "Готово" : status.status === "failed" ? "Ошибка" : "Выполняется"}
            </span>
          </div>
          <div className="h-64"><TerminalOutput lines={logs} /></div>
        </section>
      )}

      {htaccess && (
        <section className="card card-p flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-[var(--t-hi)]">Готовый .htaccess</h3>
              <p className="hint">Скопируйте файл в корень сайта на Reg.ru VIP.</p>
            </div>
            <button type="button" onClick={copyHtaccess} className="btn btn-secondary shrink-0">
              <Clipboard size={13} /> Скопировать
            </button>
          </div>
          <textarea className="input font-mono text-xs min-h-64 resize-y" value={htaccess} readOnly spellCheck={false} />
        </section>
      )}

      <section className="card card-p flex flex-col gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--t-hi)]">Проверка</h3>
          <p className="hint">Curl-пробы WebSocket с ноды: origin напрямую и публичный VIP должны вернуть HTTP 101.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={verify} disabled={!valid || verifying || running} className="btn btn-secondary">
            {verifying ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
            {verifying ? "Проверка..." : "Проверить"}
          </button>
          {verifyResult && [
            ["Origin", verifyResult.origin],
            ["VIP", verifyResult.vip],
          ].map(([label, probe]) => {
            const item = probe as VerifyResult["origin"];
            return (
              <span key={label as string} className="flex items-center gap-1.5 text-xs" style={{ color: item.ok ? "var(--ok)" : "var(--err)" }}>
                {item.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                {label as string}: HTTP {item.status || "—"}
              </span>
            );
          })}
        </div>
      </section>
    </div>
  );
}
