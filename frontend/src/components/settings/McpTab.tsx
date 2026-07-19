import { useEffect, useState } from "react";
import { Loader2, Copy, Check, ServerCog, AlertCircle } from "lucide-react";
import { toast } from "../infra/Toast";

interface McpConfig {
  enabled: boolean;
  readonly: boolean;
  http_port: number;
  image: string;
  endpoint: string;
  auth_token: string | null;
  remnawave_ready: boolean;
  warning?: string;
}

interface McpStatus {
  enabled: boolean;
  readonly: boolean;
  container: "running" | "stopped" | "absent" | "no-docker";
  reachable: boolean;
  http_port: number;
}

const STATE_LABEL: Record<string, { text: string; cls: string }> = {
  running:   { text: "запущен",            cls: "ok" },
  stopped:   { text: "остановлен",         cls: "warn" },
  absent:    { text: "не создан",          cls: "warn" },
  foreign:   { text: "занят другим аккаунтом", cls: "err" },
  "no-docker": { text: "Docker недоступен", cls: "err" },
};

function CopyField({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { toast("Не удалось скопировать", "error"); }
  };
  return (
    <div className="flex flex-col gap-1">
      <span className="micro">{label}</span>
      <div className="flex items-center gap-2">
        <input className="input font-mono text-xs" readOnly value={value}
          type={secret ? "password" : "text"} onFocus={e => e.currentTarget.select()} />
        <button type="button" onClick={copy} title="Копировать"
          className="p-2 rounded-md border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)]">
          {copied ? <Check size={14} className="text-[var(--ok)]" /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}

// FastAPI errors: `detail` is a string OR a validation-error array. Never blindly
// String() an array (→ "[object Object]").
function fmtError(data: any): string {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? "ошибка").join("; ") || "Ошибка сохранения";
  return "Ошибка сохранения";
}

export function McpTab() {
  const [cfg, setCfg] = useState<McpConfig | null>(null);
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Controlled port field so it resyncs with the server value after save.
  const [portInput, setPortInput] = useState("");
  useEffect(() => { if (cfg) setPortInput(String(cfg.http_port)); }, [cfg]);

  const load = async () => {
    try {
      const [cr, sr] = await Promise.all([
        fetch("/api/mcp/config"),
        fetch("/api/mcp/status"),
      ]);
      if (!cr.ok || !sr.ok) throw new Error("bad response");
      setCfg(await cr.json());
      setStatus(await sr.json());
    } catch { toast("Не удалось загрузить конфигурацию MCP", "error"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const refreshStatus = async () => {
    try {
      const r = await fetch("/api/mcp/status");
      if (r.ok) setStatus(await r.json());
    } catch { /* transient */ }
  };

  const save = async (patch: Partial<McpConfig>) => {
    if (!cfg) return;
    setSaving(true);
    try {
      const body = {
        enabled: patch.enabled ?? cfg.enabled,
        readonly: patch.readonly ?? cfg.readonly,
        http_port: patch.http_port ?? cfg.http_port,
      };
      const res = await fetch("/api/mcp/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(fmtError(data));
      setCfg(data);
      if (data.warning) toast(data.warning, "info");
      else toast("Сохранено", "success");
      await refreshStatus();
      // The container needs a moment to bind its port after `docker run -d`;
      // re-poll once so the status chip flips without a manual reload.
      if (body.enabled) setTimeout(refreshStatus, 2000);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка сохранения", "error");
    } finally { setSaving(false); }
  };

  if (loading) {
    return <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-10">
      <Loader2 size={16} className="animate-spin" /> Загрузка...
    </div>;
  }
  if (!cfg) return null;

  const st = status ? STATE_LABEL[status.container] : null;

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="card card-p flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <ServerCog size={16} className="text-[var(--accent-hi)]" />
          <span className="text-sm font-semibold text-[var(--t-hi)]">MCP-сервер</span>
          {st && <span className={`chip ${st.cls}`} style={{ marginLeft: "auto", fontSize: 10 }}>{st.text}</span>}
        </div>
        <p className="hint">
          MCP-сервер отдаёт инструменты управления Remnawave и наблюдения за панелью
          внешним AI-клиентам (и встроенному агенту). Доступ по HTTP с Bearer-токеном.
        </p>

        {!cfg.remnawave_ready && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
            <AlertCircle size={14} className="shrink-0" />
            Remnawave не настроен — задайте panel_url и токен во вкладке «Remnawave», иначе контейнер не стартует.
          </div>
        )}

        <label className="flex items-center gap-3 cursor-pointer select-none">
          <button type="button" role="switch" aria-checked={cfg.enabled} disabled={saving}
            onClick={() => save({ enabled: !cfg.enabled })}
            className={`relative w-9 h-5 rounded-full transition-colors ${cfg.enabled ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${cfg.enabled ? "translate-x-4" : ""}`} />
          </button>
          <span className="text-sm text-[var(--t-mid)]">Включить MCP-сервер</span>
        </label>

        <label className="flex items-center gap-3 cursor-pointer select-none">
          <button type="button" role="switch" aria-checked={cfg.readonly} disabled={saving}
            onClick={() => save({ readonly: !cfg.readonly })}
            className={`relative w-9 h-5 rounded-full transition-colors ${cfg.readonly ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${cfg.readonly ? "translate-x-4" : ""}`} />
          </button>
          <span className="text-sm text-[var(--t-mid)]">Только чтение (безопасные инструменты)</span>
        </label>

        <div className="flex items-end gap-2">
          <label className="flex flex-col gap-1">
            <span className="micro">HTTP-порт</span>
            <input type="number" min={1} max={65535} className="input w-32" value={portInput}
              disabled={saving}
              onChange={e => setPortInput(e.target.value)}
              onBlur={() => {
                const p = Number(portInput);
                if (!Number.isInteger(p) || p < 1 || p > 65535) {
                  toast("Порт должен быть числом 1–65535", "error");
                  setPortInput(String(cfg.http_port)); // revert bad input
                  return;
                }
                if (p !== cfg.http_port) save({ http_port: p });
              }} />
          </label>
          {saving && <Loader2 size={14} className="animate-spin text-[var(--t-faint)] mb-2" />}
        </div>
      </div>

      {cfg.enabled && cfg.auth_token && (
        <div className="card card-p flex flex-col gap-3">
          <span className="text-xs font-semibold text-[var(--t-hi)]">Подключение внешнего клиента</span>
          <CopyField label="Endpoint (замените <server-ip> на адрес сервера)" value={cfg.endpoint} />
          <CopyField label="Bearer-токен" value={cfg.auth_token} secret />
          <p className="hint">
            В AI-клиенте с поддержкой MCP over HTTP укажите этот endpoint и заголовок
            <span className="font-mono"> Authorization: Bearer &lt;токен&gt;</span>.
            Токен хранится в зашифрованном виде; при необходимости отключите и снова
            включите сервер для перезапуска с текущими настройками.
          </p>
        </div>
      )}
    </div>
  );
}
