import { useState, useEffect, useRef } from "react";
import {
  Rocket, Loader2, Eye, EyeOff, ChevronDown, Plus, Trash2, Upload, AlertCircle,
} from "lucide-react";

// Ф6 — Remnawave panel / subscription-page deploy form. Mirrors DeployForm's
// structure (sections, Collapsible, validate-then-open) but for PanelDeployRequest.
// `validatePanelForm` is exported for unit tests and mirrors the server validators
// in backend/app/models/panel_deploy.py.

export type PanelTarget = "panel" | "subpage" | "both";
export type ReverseProxy = "caddy" | "nginx";
export type CertProvider = "cloudflare" | "letsencrypt" | "zerossl";

export interface EnvPair { key: string; value: string }

// Form-shaped data (string ports / list-of-pairs env / toggles). Coerced to the
// wire shape by `toPayload`.
export interface PanelFormData {
  target:           PanelTarget;
  ip:               string;
  ssh_user:         string;
  ssh_password:     string;
  ssh_port:         string;
  panel_domain:     string;
  sub_domain:       string;
  email:            string;
  reverse_proxy:    ReverseProxy;
  cert_provider:    CertProvider;
  cf_api_key:       string;
  enable_webhooks:  boolean;
  webhook_url:      string;
  extra_env:        EnvPair[];
  use_sub_server:   boolean;      // toggle: separate box for the subscription page
  sub_ip:           string;
  sub_ssh_user:     string;
  sub_ssh_password: string;
  sub_ssh_port:     string;
  subpage_html:     string;       // raw Orion index.html (from catalog OR pasted/uploaded)
  subpage_api_token: string;      // Волна 6: обязателен для target subpage/both
  subpage_source_id:string;       // selected catalog page id ("" = pasted/none)
  install_test_tools: boolean;
}

// Wire shape (PanelDeployRequest). This is what lands in panel_jobs_<id>.savedForm.
export interface SubServerPayload {
  ip: string; ssh_user: string; ssh_password: string; ssh_port: number;
}
export interface PanelDeployPayload {
  target:           PanelTarget;
  ip:               string;
  ssh_user:         string;
  ssh_password:     string;
  ssh_port:         number;
  panel_domain:     string;
  sub_domain:       string;
  email:            string;
  reverse_proxy:    ReverseProxy;
  cert_provider:    CertProvider;
  cf_api_key:       string;
  enable_webhooks:  boolean;
  webhook_url:      string;
  extra_env:        Record<string, string>;
  sub_server:       SubServerPayload | null;
  subpage_html:     string;
  subpage_api_token: string;
  install_test_tools: boolean;
}

export const PANEL_FORM_DEFAULT: PanelFormData = {
  target:           "panel",
  ip:               "",
  ssh_user:         "root",
  ssh_password:     "",
  ssh_port:         "22",
  panel_domain:     "",
  sub_domain:       "",
  email:            "",
  reverse_proxy:    "caddy",
  cert_provider:    "letsencrypt",
  cf_api_key:       "",
  enable_webhooks:  false,
  webhook_url:      "",
  extra_env:        [],
  use_sub_server:   false,
  sub_ip:           "",
  sub_ssh_user:     "root",
  sub_ssh_password: "",
  sub_ssh_port:     "22",
  subpage_html:     "",
  subpage_api_token: "",
  subpage_source_id:"",
  install_test_tools: true,
};

// ── Validators (mirror backend/app/models/panel_deploy.py) ────
const IPv4    = /^(\d{1,3}\.){3}\d{1,3}$/;
const DOMAIN  = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;
// Byte-for-byte the server's _EMAIL_RE (panel_deploy.py) so the client never
// passes an address the server will 422 (e.g. digit-TLD, `_` in the domain).
const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;
const ENV_KEY = /^[A-Z_][A-Z0-9_]*$/;
// Secrets/DSN generated server-side — an override must not weaken them.
const PROTECTED_ENV = new Set([
  "POSTGRES_PASSWORD", "DATABASE_URL", "JWT_AUTH_SECRET",
  "JWT_API_TOKENS_SECRET", "METRICS_PASS", "WEBHOOK_SECRET_HEADER",
]);
const SUBPAGE_MAX = 512 * 1024;   // 512 KiB, matches the server cap

function validIp(v: string): boolean {
  return IPv4.test(v) && v.split(".").every(o => parseInt(o, 10) <= 255);
}

export type PanelErrKey =
  | "ip" | "ssh_password" | "ssh_port"
  | "panel_domain" | "sub_domain" | "email" | "cf_api_key" | "webhook_url"
  | "extra_env" | "subpage_html" | "subpage_api_token"
  | "sub_ip" | "sub_ssh_password" | "sub_ssh_port" | "sub_server";

export function validatePanelForm(f: PanelFormData): Partial<Record<PanelErrKey, string>> {
  const e: Partial<Record<PanelErrKey, string>> = {};

  // ── Server (always) ──
  if (!validIp(f.ip)) e.ip = "Неверный IPv4";
  if (!f.ssh_password) e.ssh_password = "Обязательное поле";
  const port = parseInt(f.ssh_port, 10);
  if (isNaN(port) || port < 1 || port > 65535) e.ssh_port = "1–65535";

  const wantPanel = f.target === "panel" || f.target === "both";
  const wantSub   = f.target === "subpage" || f.target === "both";

  // ── Domains (target-gated — format is only checked for the relevant target,
  //    else a stale value from a since-switched target would silently block
  //    submit on a field that no longer renders) ──
  if (wantPanel) {
    if (!f.panel_domain.trim()) e.panel_domain = "Обязательное поле";
    else if (!DOMAIN.test(f.panel_domain.trim())) e.panel_domain = "Неверный домен";
  }
  if (wantSub) {
    if (!f.sub_domain.trim()) e.sub_domain = "Обязательное поле";
    else if (!DOMAIN.test(f.sub_domain.trim())) e.sub_domain = "Неверный домен";
    // Без токена контейнер страницы подписок падает на старте (проверено на
    // образе 7.2.6) — зеркалим серверную проверку, чтобы не ловить 422.
    if (!f.subpage_api_token.trim()) e.subpage_api_token = "Обязательное поле";
  }
  // Шаблон без `<%- panelData %>` — молчаливо пустая страница.
  if (f.subpage_html.trim() && !f.subpage_html.includes("panelData"))
    e.subpage_html = "Шаблон должен содержать `<%- panelData %>`";

  // email: valid if present; required for nginx + letsencrypt/zerossl.
  if (f.email.trim() && !EMAIL_RE.test(f.email.trim())) e.email = "Неверный email";

  // ── Reverse-proxy / SSL (nginx only — caddy manages TLS itself) ──
  if (f.reverse_proxy === "nginx") {
    if (f.cert_provider === "cloudflare" && !f.cf_api_key.trim())
      e.cf_api_key = "Обязательное поле";
    if (f.cert_provider === "cloudflare" && f.cf_api_key && /["\n\r`$]/.test(f.cf_api_key))
      e.cf_api_key = "Недопустимые символы в токене";
    if ((f.cert_provider === "letsencrypt" || f.cert_provider === "zerossl") && !f.email.trim())
      e.email = "Email обязателен для Let's Encrypt/ZeroSSL";
  }

  // ── Webhooks ──
  if (f.enable_webhooks) {
    const u = f.webhook_url.trim();
    if (!u) e.webhook_url = "Обязательное поле";
    // Mirror the server: reject newline/CR/space (not every unicode space) so
    // the client doesn't reject a URL the server would accept.
    else if (!/^https?:\/\//.test(u) || /[\n\r ]/.test(u)) e.webhook_url = "Укажите http(s) URL";
  }

  // ── Extra .env pairs ──
  for (const p of f.extra_env) {
    const key = p.key.trim();
    if (!key) continue;   // blank rows are dropped on submit
    if (!ENV_KEY.test(key)) { e.extra_env = `Неверный ключ: ${key} (ожидается A-Z_)`; break; }
    if (PROTECTED_ENV.has(key)) { e.extra_env = `${key} генерируется автоматически`; break; }
    if (/[\n\r]/.test(p.value)) { e.extra_env = `Значение ${key}: без переносов строк`; break; }
  }

  // ── Subscription-page HTML (optional; only size-capped) ──
  if (wantSub && f.subpage_html) {
    // byte length (UTF-8) — cheap approximation via Blob is unavailable in tests,
    // so use the encoded length.
    const bytes = new TextEncoder().encode(f.subpage_html).length;
    if (bytes > SUBPAGE_MAX) e.subpage_html = "HTML превышает лимит 512 КиБ";
  }

  // ── Separate subscription-page server (target=both only) ──
  if (f.use_sub_server && f.target === "both") {
    if (!validIp(f.sub_ip)) e.sub_ip = "Неверный IPv4";
    if (!f.sub_ssh_password) e.sub_ssh_password = "Обязательное поле";
    const sp = parseInt(f.sub_ssh_port, 10);
    if (isNaN(sp) || sp < 1 || sp > 65535) e.sub_ssh_port = "1–65535";
  }
  if (f.use_sub_server && f.target !== "both")
    e.sub_server = "Отдельный сервер доступен только в режиме «Оба»";

  return e;
}

// Coerce the form into the wire shape. Irrelevant fields are zeroed so a stale
// value (e.g. sub_domain left over after switching to target=panel) can't reach
// the server and trip its per-field validators.
export function toPayload(f: PanelFormData): PanelDeployPayload {
  const wantPanel = f.target === "panel" || f.target === "both";
  const wantSub   = f.target === "subpage" || f.target === "both";
  const env: Record<string, string> = {};
  for (const p of f.extra_env) {
    const k = p.key.trim();
    if (k) env[k] = p.value;
  }
  return {
    target:          f.target,
    ip:              f.ip.trim(),
    ssh_user:        f.ssh_user.trim() || "root",
    ssh_password:    f.ssh_password,
    ssh_port:        parseInt(f.ssh_port, 10) || 22,
    panel_domain:    wantPanel ? f.panel_domain.trim() : "",
    sub_domain:      wantSub ? f.sub_domain.trim() : "",
    subpage_api_token: wantSub ? f.subpage_api_token.trim() : "",
    email:           f.email.trim(),
    reverse_proxy:   f.reverse_proxy,
    cert_provider:   f.cert_provider,
    cf_api_key:      f.reverse_proxy === "nginx" && f.cert_provider === "cloudflare" ? f.cf_api_key : "",
    enable_webhooks: f.enable_webhooks,
    webhook_url:     f.enable_webhooks ? f.webhook_url.trim() : "",
    extra_env:       env,
    sub_server:      (f.target === "both" && f.use_sub_server)
      ? { ip: f.sub_ip.trim(), ssh_user: f.sub_ssh_user.trim() || "root", ssh_password: f.sub_ssh_password, ssh_port: parseInt(f.sub_ssh_port, 10) || 22 }
      : null,
    subpage_html:    wantSub ? f.subpage_html : "",
    install_test_tools: f.install_test_tools,
  };
}

// Which collapsible section hides each errorable field (auto-open on failed submit).
type SectionKey = "webhooks" | "env";
const FIELD_SECTION: Partial<Record<PanelErrKey, SectionKey>> = {
  webhook_url: "webhooks",
  extra_env:   "env",
};

// ── Small reusable inputs (local, mirror DeployForm's Field/Toggle) ──

function Field({ label, value, onChange, error, type = "text", placeholder, disabled, secret, hint }: {
  label: string; value: string; onChange: (v: string) => void; error?: string;
  type?: string; placeholder?: string; disabled?: boolean; secret?: boolean; hint?: string;
}) {
  const [show, setShow] = useState(false);
  const inputType = secret ? (show ? "text" : "password") : type;
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{label}</label>
      <div className={secret ? "relative" : undefined}>
        <input
          type={inputType} value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} disabled={disabled} autoComplete="off" spellCheck={false}
          className={`input transition-colors ${secret ? "pr-9" : ""} ${error ? "err" : ""}`}
        />
        {secret && (
          <button type="button" tabIndex={-1} onClick={() => setShow(v => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2.5 text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
            {show ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        )}
      </div>
      {hint  && !error && <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>{hint}</p>}
      {error && <p className="errmsg">{error}</p>}
    </div>
  );
}

function Toggle({ label, checked, onChange, disabled }: {
  label: string; checked: boolean; onChange: () => void; disabled?: boolean;
}) {
  return (
    <label className={`flex items-center gap-3 cursor-pointer select-none group mt-1 ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
      <button type="button" role="switch" aria-checked={checked} onClick={onChange}
        className={`relative w-9 h-5 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)]
                    ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0"}`} />
      </button>
      <span className="text-sm text-[var(--t-low)] group-hover:text-[var(--t-hi)] transition-colors">{label}</span>
    </label>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-widest mt-1" style={{ color: "var(--t-faint)" }}>{children}</p>
  );
}

function Collapsible({ title, open, onToggle, children }: {
  title: string; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border" style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
      <button type="button" onClick={onToggle}
        className="w-full flex items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-[var(--bg3)] transition-colors rounded-lg">
        <span className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--t-low)" }}>{title}</span>
        <ChevronDown size={14} className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`} style={{ color: "var(--t-faint)" }} />
      </button>
      {open && (
        <div className="px-3 pb-3 flex flex-col gap-3 border-t pt-3" style={{ borderColor: "var(--line-soft)" }}>{children}</div>
      )}
    </div>
  );
}

interface SubPageMeta { id: string; name: string }

// ── Main form ─────────────────────────────────────────────────

interface Props {
  onSubmit:  (payload: PanelDeployPayload) => Promise<void>;
  onCancel?: () => void;
  initial?:  Partial<PanelFormData>;
}

export function PanelDeployForm({ onSubmit, onCancel, initial }: Props) {
  const [form,       setForm]       = useState<PanelFormData>({ ...PANEL_FORM_DEFAULT, ...initial });
  const [errors,     setErrors]     = useState<Partial<Record<PanelErrKey, string>>>({});
  const [touched,    setTouched]    = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [apiError,   setApiError]   = useState<string | null>(null);
  const [sec, setSec]               = useState({ webhooks: false, env: false });
  const [pages,      setPages]      = useState<SubPageMeta[]>([]);
  const [loadingRaw, setLoadingRaw] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const toggleSec = (k: keyof typeof sec) => setSec(s => ({ ...s, [k]: !s[k] }));

  // Catalog of saved subscription pages (Ф5). Best-effort — empty catalog just
  // means the operator pastes/uploads HTML directly.
  useEffect(() => {
    fetch("/api/subpages")
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (Array.isArray(d?.pages)) setPages(d.pages.map((p: SubPageMeta) => ({ id: p.id, name: p.name }))); })
      .catch(() => {});
  }, []);

  const set = <K extends keyof PanelFormData>(name: K, value: PanelFormData[K]) =>
    setForm(f => {
      const next = { ...f, [name]: value };
      if (touched) setErrors(validatePanelForm(next));
      return next;
    });

  const wantPanel = form.target === "panel" || form.target === "both";
  const wantSub   = form.target === "subpage" || form.target === "both";

  // Pull the raw HTML for a catalog selection (or clear when "— вставить вручную —").
  const pickCatalog = async (id: string) => {
    set("subpage_source_id", id);
    if (!id) return;
    setLoadingRaw(true);
    setApiError(null);
    try {
      const res = await fetch(`/api/subpages/${id}/raw`);
      if (!res.ok) {
        // 404 (page deleted between listing and click) etc. — keep the current
        // html and surface the error instead of silently blanking it.
        setApiError("Не удалось загрузить выбранную страницу из каталога");
        return;
      }
      const html = await res.text();
      // Guard against a stale response: only apply if this id is still selected
      // (the user may have picked another entry while the fetch was in flight).
      setForm(f => (f.subpage_source_id === id ? { ...f, subpage_html: html } : f));
    } catch { /* leave html as-is */ }
    finally { setLoadingRaw(false); }
  };

  const uploadFile = (file: File) => {
    if (file.size > SUBPAGE_MAX) {
      setApiError("HTML-файл превышает 512 КиБ");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setForm(f => ({ ...f, subpage_html: String(reader.result ?? ""), subpage_source_id: "" }));
    reader.readAsText(file);
  };

  const addEnvPair = () => set("extra_env", [...form.extra_env, { key: "", value: "" }]);
  const setEnvPair = (i: number, patch: Partial<EnvPair>) =>
    set("extra_env", form.extra_env.map((p, k) => (k === i ? { ...p, ...patch } : p)));
  const delEnvPair = (i: number) => set("extra_env", form.extra_env.filter((_, k) => k !== i));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const errs = validatePanelForm(form);
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      // Auto-open any collapsed section that hides an errored field.
      setSec(s => {
        const n = { ...s };
        for (const k of Object.keys(errs) as PanelErrKey[]) {
          const target = FIELD_SECTION[k];
          if (target) n[target] = true;
        }
        return n;
      });
      return;
    }
    setErrors({});
    setApiError(null);
    setSubmitting(true);
    try {
      await onSubmit(toPayload(form));
    } catch (err) {
      setApiError(err instanceof Error ? err.message : "Ошибка сервера");
    } finally {
      setSubmitting(false);
    }
  };

  const f = submitting;

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3">

      {/* ── Что устанавливаем ── */}
      <div className="seg accent">
        {([
          { id: "panel"   as PanelTarget, label: "Панель" },
          { id: "subpage" as PanelTarget, label: "Страница подписок" },
          { id: "both"    as PanelTarget, label: "Оба" },
        ]).map(t => (
          <button key={t.id} type="button" disabled={f}
            onClick={() => setForm(prev => {
              const updated = { ...prev, target: t.id };
              // Leaving "both" drops the separate-server toggle (server rejects it).
              if (t.id !== "both") updated.use_sub_server = false;
              if (touched) setErrors(validatePanelForm(updated));
              return updated;
            })}
            className={`flex-1 text-sm font-medium focus:outline-none disabled:opacity-50 ${form.target === t.id ? "on" : ""}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Сервер ── */}
      <SectionLabel>Сервер{form.target === "subpage" ? " (страницы подписок)" : ""}</SectionLabel>
      <div className="grid grid-cols-2 gap-3">
        <Field label="IP-адрес" value={form.ip} onChange={v => set("ip", v)} placeholder="1.2.3.4" error={errors.ip} disabled={f} />
        <Field label="SSH логин" value={form.ssh_user} onChange={v => set("ssh_user", v)} placeholder="root" disabled={f} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="SSH пароль" value={form.ssh_password} onChange={v => set("ssh_password", v)} error={errors.ssh_password} disabled={f} secret />
        <Field label="SSH порт" value={form.ssh_port} onChange={v => set("ssh_port", v)} placeholder="22" error={errors.ssh_port} disabled={f} />
      </div>

      {/* ── Домены ── */}
      <SectionLabel>Домены</SectionLabel>
      {wantPanel && (
        <Field label="Домен панели" value={form.panel_domain} onChange={v => set("panel_domain", v)}
          placeholder="panel.example.com" error={errors.panel_domain} disabled={f} />
      )}
      {wantSub && (
        <Field label="Домен страницы подписок" value={form.sub_domain} onChange={v => set("sub_domain", v)}
          placeholder="sub.example.com" error={errors.sub_domain} disabled={f} />
      )}
      {wantSub && (
        <Field label="API-токен Remnawave" value={form.subpage_api_token}
          onChange={v => set("subpage_api_token", v)} type="password"
          placeholder="создайте в Remnawave → Settings → API Tokens"
          error={errors.subpage_api_token} disabled={f}
          hint="Обязателен: без него контейнер страницы подписок не стартует" />
      )}
      <Field label="Email (ACME)" value={form.email} onChange={v => set("email", v)} type="email"
        placeholder="you@example.com" error={errors.email} disabled={f}
        hint="Нужен для Let's Encrypt / ZeroSSL при nginx" />

      {/* ── Reverse-proxy и SSL ── */}
      <SectionLabel>Reverse-proxy и SSL</SectionLabel>
      <div className="seg accent">
        {([
          { id: "caddy" as ReverseProxy, label: "Caddy" },
          { id: "nginx" as ReverseProxy, label: "nginx" },
        ]).map(t => (
          <button key={t.id} type="button" disabled={f}
            onClick={() => setForm(prev => {
              const updated = { ...prev, reverse_proxy: t.id };
              if (touched) setErrors(validatePanelForm(updated));
              return updated;
            })}
            className={`flex-1 text-sm font-medium focus:outline-none disabled:opacity-50 ${form.reverse_proxy === t.id ? "on" : ""}`}>
            {t.label}
          </button>
        ))}
      </div>
      {form.reverse_proxy === "caddy" ? (
        <p className="text-[11px] flex items-center gap-1.5" style={{ color: "var(--t-faint)" }}>
          <AlertCircle size={11} /> Caddy сам управляет SSL (встроенный ACME) — токен/провайдер не требуются.
        </p>
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
              Провайдер сертификата
            </label>
            <select value={form.cert_provider} onChange={e => set("cert_provider", e.target.value as CertProvider)}
              disabled={f} className="selectbox transition-colors">
              <option value="cloudflare">Cloudflare (DNS-01)</option>
              <option value="letsencrypt">Let's Encrypt (HTTP-01)</option>
              <option value="zerossl">ZeroSSL (acme.sh + EAB)</option>
            </select>
          </div>
          {form.cert_provider === "cloudflare" && (
            <Field label="Cloudflare API токен" value={form.cf_api_key} onChange={v => set("cf_api_key", v)}
              placeholder="DNS:Edit permission" error={errors.cf_api_key} disabled={f} secret />
          )}
          {(form.cert_provider === "letsencrypt" || form.cert_provider === "zerossl") && (
            <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
              HTTP-01: домены должны уже указывать на этот сервер, порт 80 будет освобождён.
            </p>
          )}
        </>
      )}

      {/* ── Webhooks (сворачиваемая) ── */}
      <Collapsible title="Webhooks" open={sec.webhooks} onToggle={() => toggleSec("webhooks")}>
        <Toggle label="Включить webhooks Remnawave" checked={form.enable_webhooks}
          onChange={() => set("enable_webhooks", !form.enable_webhooks)} disabled={f} />
        {form.enable_webhooks && (
          <Field label="Webhook URL" value={form.webhook_url} onChange={v => set("webhook_url", v)}
            placeholder="https://example.com/webhook" error={errors.webhook_url} disabled={f}
            hint="Секрет подписи (HMAC) генерируется на сервере автоматически" />
        )}
      </Collapsible>

      {/* ── Доп. переменные .env (сворачиваемая) ── */}
      <Collapsible title="Дополнительные переменные .env" open={sec.env} onToggle={() => toggleSec("env")}>
        {form.extra_env.length === 0 && (
          <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
            Добавьте пары KEY=значение — попадут в .env панели (секретные ключи переопределять нельзя).
          </p>
        )}
        {form.extra_env.map((p, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
            <input value={p.key} onChange={e => setEnvPair(i, { key: e.target.value })}
              placeholder="KEY" disabled={f} autoComplete="off" spellCheck={false}
              className="input transition-colors" />
            <input value={p.value} onChange={e => setEnvPair(i, { value: e.target.value })}
              placeholder="значение" disabled={f} autoComplete="off" spellCheck={false}
              className="input transition-colors" />
            <button type="button" onClick={() => delEnvPair(i)} disabled={f}
              className="p-1.5 rounded text-[var(--t-faint)] hover:text-[var(--err)] hover:bg-[var(--bg3)] transition-colors">
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {errors.extra_env && <p className="errmsg">{errors.extra_env}</p>}
        <button type="button" onClick={addEnvPair} disabled={f}
          className="flex items-center gap-1.5 self-start px-2.5 py-1.5 rounded-md text-xs font-medium border transition-colors hover:bg-[var(--bg3)]"
          style={{ borderColor: "var(--line)", color: "var(--t-mid)", background: "var(--bg2)" }}>
          <Plus size={12} /> Добавить переменную
        </button>
      </Collapsible>

      {/* ── Отдельный сервер для подписки (только target=both) ── */}
      {form.target === "both" && (
        <>
          <SectionLabel>Отдельный сервер для подписки</SectionLabel>
          <Toggle label="Ставить страницу подписок на отдельный сервер" checked={form.use_sub_server}
            onChange={() => set("use_sub_server", !form.use_sub_server)} disabled={f} />
          {form.use_sub_server && (
            <div className="flex flex-col gap-3 rounded-lg border p-3" style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
              <div className="grid grid-cols-2 gap-3">
                <Field label="IP подписки" value={form.sub_ip} onChange={v => set("sub_ip", v)} placeholder="1.2.3.5" error={errors.sub_ip} disabled={f} />
                <Field label="SSH логин" value={form.sub_ssh_user} onChange={v => set("sub_ssh_user", v)} placeholder="root" disabled={f} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="SSH пароль" value={form.sub_ssh_password} onChange={v => set("sub_ssh_password", v)} error={errors.sub_ssh_password} disabled={f} secret />
                <Field label="SSH порт" value={form.sub_ssh_port} onChange={v => set("sub_ssh_port", v)} placeholder="22" error={errors.sub_ssh_port} disabled={f} />
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Страница подписок: HTML (target≠panel) ── */}
      {wantSub && (
        <>
          <SectionLabel>Страница подписок (Orion)</SectionLabel>
          {pages.length > 0 ? (
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
                Из каталога
              </label>
              <select value={form.subpage_source_id} onChange={e => pickCatalog(e.target.value)}
                disabled={f || loadingRaw} className="selectbox transition-colors">
                <option value="">— вставить вручную —</option>
                {pages.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          ) : (
            <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
              Каталог пуст — вставьте HTML ниже или загрузите файл (страницы сохраняются во вкладке «Страницы подписок»).
            </p>
          )}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <label className="text-[11px] font-medium uppercase tracking-widest flex-1" style={{ color: "var(--t-low)" }}>
                HTML {loadingRaw && <Loader2 size={10} className="inline animate-spin" />}
              </label>
              <button type="button" onClick={() => fileRef.current?.click()} disabled={f}
                className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-medium border transition-colors hover:bg-[var(--bg3)]"
                style={{ borderColor: "var(--line)", color: "var(--t-mid)", background: "var(--bg2)" }}>
                <Upload size={11} /> Загрузить файл
              </button>
              <input ref={fileRef} type="file" accept=".html,text/html" className="hidden"
                onChange={e => { const file = e.target.files?.[0]; if (file) uploadFile(file); e.target.value = ""; }} />
            </div>
            <textarea value={form.subpage_html}
              onChange={e => setForm(prev => ({ ...prev, subpage_html: e.target.value, subpage_source_id: "" }))}
              disabled={f} rows={4} spellCheck={false} placeholder="<!doctype html> …"
              className="input transition-colors" style={{ resize: "vertical", minHeight: "4rem", fontFamily: "var(--font-mono, monospace)" }} />
            {errors.subpage_html
              ? <p className="errmsg">{errors.subpage_html}</p>
              : <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
                  Необязательно — если пусто, ставится страница подписок по умолчанию.
                </p>}
          </div>
        </>
      )}

      {/* ── Инструменты тестирования ── */}
      <SectionLabel>Прочее</SectionLabel>
      <Toggle label="Установить инструменты тестирования" checked={form.install_test_tools}
        onChange={() => set("install_test_tools", !form.install_test_tools)} disabled={f} />

      {apiError && (
        <div className="mt-2 px-3 py-2 rounded-md border text-xs"
          style={{ background: "var(--err-dim)", borderColor: "var(--err-line)", color: "var(--err)" }}>{apiError}</div>
      )}

      <div className="mt-3 flex gap-2">
        <button type="submit" disabled={submitting}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg font-semibold text-sm transition-all
                     bg-[var(--accent)] text-[var(--primary-ink)] hover:bg-[var(--accent-hi)]
                     active:bg-[var(--accent)] disabled:bg-[var(--accent-dim)] disabled:cursor-not-allowed
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)]">
          {submitting
            ? <><Loader2 size={15} className="animate-spin" /> Запуск...</>
            : <><Rocket size={15} /> Установить</>}
        </button>
        {onCancel && !submitting && (
          <button type="button" onClick={onCancel}
            className="px-4 py-2.5 rounded-lg text-sm font-medium text-[var(--t-low)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors
                       focus:outline-none focus:ring-1 focus:ring-[var(--line)]">
            Отмена
          </button>
        )}
      </div>
    </form>
  );
}
