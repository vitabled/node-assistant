import { useState, useEffect, useRef } from "react";
import { Rocket, Loader2, Eye, EyeOff, AlertCircle, ChevronDown, Zap } from "lucide-react";
import { MultiSelect, type SelectOption } from "./MultiSelect";
import { CountrySelect } from "./CountrySelect";

export type DeployMode = "remnanode" | "haproxy";

export interface FormData {
  mode:                DeployMode;
  ip:                  string;
  ssh_user:            string;
  ssh_password:        string;
  domain:              string;
  cert_provider:       string;   // cloudflare | letsencrypt | zerossl
  cloudflare_api_key:  string;
  email:               string;
  remnanode_token:     string;
  open_ports:          string;
  whitelist_ips:       string;
  allow_ssh_all:       boolean;
  current_ssh_port:    string;
  new_ssh_port:        string;
  change_ssh_port:     boolean;
  remnanode_port:      string;
  xhttp_path:          string;
  country_code:        string;
  behind_cdn:          boolean;
  install_warp:        boolean;
  install_vnstat:      boolean;
  install_trafficguard: boolean;
  update_system:       boolean;
  create_in_remnawave: boolean;
  internal_squad_ids:  string[];
  external_squad_ids:  string[];
  plugin_uuid:         string;
  template_id:         string;
  // OS optimization (node-accelerator)
  optimize:            boolean;
  opt_network_tuning:  boolean;
  opt_bbr:             boolean;
  opt_system_limits:   boolean;
  opt_dns:             boolean;
  opt_dns_servers:     string;
  // HAProxy relay mode
  haproxy_source_port:     string;
  haproxy_dest_ip:         string;
  haproxy_dest_port:       string;
  haproxy_maxconn:         string;
  haproxy_log:             string;
  haproxy_mode:            string;
  haproxy_timeout_connect: string;
  haproxy_timeout_client:  string;
  haproxy_timeout_server:  string;
  haproxy_timeout_tunnel:  string;
}

interface Template { id: string; name: string; is_default: boolean }

const CERT_PROVIDERS: { value: string; label: string }[] = [
  { value: "cloudflare",  label: "Cloudflare (DNS-01)" },
  { value: "letsencrypt", label: "Let's Encrypt (HTTP-01)" },
  { value: "zerossl",     label: "ZeroSSL (acme.sh + EAB)" },
];

export const FORM_DEFAULT: FormData = {
  mode:                "remnanode",
  ip:                  "",
  ssh_user:            "root",
  ssh_password:        "",
  domain:              "",
  cert_provider:       "cloudflare",
  cloudflare_api_key:  "",
  email:               "",
  remnanode_token:     "",
  open_ports:          "80,443,8443",
  whitelist_ips:       "",
  allow_ssh_all:       false,
  current_ssh_port:    "22",
  new_ssh_port:        "2222",
  change_ssh_port:     true,
  remnanode_port:      "2222",
  xhttp_path:          "",
  country_code:        "",
  behind_cdn:          false,
  install_warp:        false,
  install_vnstat:      true,
  install_trafficguard: true,
  update_system:       false,
  create_in_remnawave: false,
  internal_squad_ids:  [],
  external_squad_ids:  [],
  plugin_uuid:         "",
  template_id:         "",
  optimize:            true,
  opt_network_tuning:  true,
  opt_bbr:             true,
  opt_system_limits:   true,
  opt_dns:             true,
  opt_dns_servers:     "1.1.1.1,8.8.8.8",
  haproxy_source_port:     "443",
  haproxy_dest_ip:         "",
  haproxy_dest_port:       "443",
  haproxy_maxconn:         "200000",
  haproxy_log:             "global",
  haproxy_mode:            "tcp",
  haproxy_timeout_connect: "5s",
  haproxy_timeout_client:  "50s",
  haproxy_timeout_server:  "50s",
  haproxy_timeout_tunnel:  "1h",
};

// ── Validators ────────────────────────────────────────────────
const IPv4   = /^(\d{1,3}\.){3}\d{1,3}$/;
const DOMAIN = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validatePorts(s: string): string | null {
  const parts = s.split(",").map(p => p.trim()).filter(Boolean);
  if (!parts.length) return "Укажите хотя бы один порт";
  for (const p of parts) {
    const n = parseInt(p, 10);
    if (isNaN(n) || n < 1 || n > 65535) return `Неверный порт: ${p}`;
  }
  return null;
}

export function validateForm(f: FormData): Partial<Record<keyof FormData, string>> {
  const e: Partial<Record<keyof FormData, string>> = {};
  // ── Shared fields (both modes) ──
  if (!IPv4.test(f.ip) || f.ip.split(".").some(o => parseInt(o) > 255)) e.ip = "Неверный IPv4";
  if (!f.ssh_user.trim())  e.ssh_user  = "Обязательное поле";
  if (!f.ssh_password)     e.ssh_password = "Обязательное поле";
  const portsErr = validatePorts(f.open_ports);
  if (portsErr) e.open_ports = portsErr;
  const cur = parseInt(f.current_ssh_port, 10);
  if (isNaN(cur) || cur < 1 || cur > 65535) e.current_ssh_port = "1–65535";
  if (f.change_ssh_port) {
    const nxt = parseInt(f.new_ssh_port, 10);
    if (isNaN(nxt) || nxt < 1024 || nxt > 65535) e.new_ssh_port = "1024–65535";
  }
  // whitelist_ips accepts anything (normalized server-side); no validation here.

  if (f.mode === "haproxy") {
    // ── HAProxy mode ──
    const sp = parseInt(f.haproxy_source_port, 10);
    if (isNaN(sp) || sp < 1 || sp > 65535) e.haproxy_source_port = "1–65535";
    if (!f.haproxy_dest_ip.trim()) e.haproxy_dest_ip = "Обязательное поле";
    const dp = parseInt(f.haproxy_dest_port, 10);
    if (isNaN(dp) || dp < 1 || dp > 65535) e.haproxy_dest_port = "1–65535";
    const mc = parseInt(f.haproxy_maxconn, 10);
    if (isNaN(mc) || mc < 1) e.haproxy_maxconn = "≥ 1";
  } else {
    // ── Remnanode mode ──
    if (!DOMAIN.test(f.domain)) e.domain = "Неверный домен";
    // Cloudflare token is only required for the cloudflare (DNS-01) provider.
    if (f.cert_provider === "cloudflare" && !f.cloudflare_api_key.trim())
      e.cloudflare_api_key = "Обязательное поле";
    if (!EMAIL_RE.test(f.email)) e.email = "Неверный email";
    if (!f.create_in_remnawave && !f.remnanode_token.trim())
      e.remnanode_token = "Обязательное поле";
    if (f.create_in_remnawave && !f.template_id)
      e.template_id = "Выберите шаблон конфигурации";
    const np = parseInt(f.remnanode_port, 10);
    if (isNaN(np) || np < 1 || np > 65535) e.remnanode_port = "1–65535";
    if (!f.country_code || f.country_code.length !== 2) e.country_code = "Выберите страну";
  }
  return e;
}

// Which collapsible section each errorable field lives in (so a failed submit
// can auto-open the section hiding the error).
type SectionKey = "domain" | "network" | "remnawave";
const FIELD_SECTION: Partial<Record<keyof FormData, SectionKey>> = {
  domain: "domain", email: "domain", cloudflare_api_key: "domain",
  current_ssh_port: "network", new_ssh_port: "network", open_ports: "network",
  remnanode_token: "remnawave", template_id: "remnawave",
};

// ── Field components ──────────────────────────────────────────

interface FieldProps {
  label:       string;
  name:        keyof FormData;
  value:       string;
  onChange:    (n: keyof FormData, v: string) => void;
  error?:      string;
  type?:       string;
  placeholder?: string;
  disabled?:   boolean;
  secret?:     boolean;
  hint?:       string;
}

function Field({ label, name, value, onChange, error, type = "text",
                 placeholder, disabled, secret, hint }: FieldProps) {
  const [show, setShow] = useState(false);
  const inputType = secret ? (show ? "text" : "password") : type;

  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
        {label}
      </label>
      <div className={secret ? "relative" : undefined}>
        <input
          type={inputType}
          value={value}
          onChange={e => onChange(name, e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          className={`input transition-colors ${secret ? "pr-9" : ""} ${error ? "err" : ""}`}
        />
        {secret && (
          <button type="button" tabIndex={-1} onClick={() => setShow(v => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2.5
                       text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
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
    <label className={`flex items-center gap-3 cursor-pointer select-none group mt-1
                       ${disabled ? "opacity-40 pointer-events-none" : ""}`}>
      <button type="button" role="switch" aria-checked={checked} onClick={onChange}
        className={`relative w-9 h-5 rounded-full transition-colors focus:outline-none
                    focus:ring-2 focus:ring-[var(--accent-line)]
                    ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                          transition-transform duration-200 ${checked ? "translate-x-4" : "translate-x-0"}`} />
      </button>
      <span className="text-sm text-[var(--t-low)] group-hover:text-[var(--t-hi)] transition-colors">{label}</span>
    </label>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-widest mt-1" style={{ color: "var(--t-faint)" }}>
      {children}
    </p>
  );
}

// Collapsible section shell (matches the existing Оптимизация pattern).
function Collapsible({ title, icon, open, onToggle, children }: {
  title: string; icon?: React.ReactNode; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border" style={{ borderColor: "var(--line-soft)", background: "var(--bg2)" }}>
      <button type="button" onClick={onToggle}
        className="w-full flex items-center justify-between gap-2 px-3 py-2.5
                   text-left hover:bg-[var(--bg3)] transition-colors rounded-lg">
        <span className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
          {icon}{title}
        </span>
        <ChevronDown size={14}
          className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`}
          style={{ color: "var(--t-faint)" }} />
      </button>
      {open && (
        <div className="px-3 pb-3 flex flex-col gap-3 border-t pt-3" style={{ borderColor: "var(--line-soft)" }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────

interface Props {
  onSubmit:   (data: FormData) => Promise<void>;
  onCancel?:  () => void;
  initial?:   Partial<FormData>;
}

export function DeployForm({ onSubmit, onCancel, initial }: Props) {
  const [form,       setForm]       = useState<FormData>({ ...FORM_DEFAULT, ...initial });
  const [errors,     setErrors]     = useState<Partial<Record<keyof FormData, string>>>({});
  const [touched,    setTouched]    = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [apiError,   setApiError]   = useState<string | null>(null);

  // Collapsible section open-state. Required-field sections default open;
  // optional ones (Remnawave, Оптимизация) default collapsed.
  const [sec, setSec] = useState({ domain: true, network: true, remnawave: false, opt: false });
  const toggleSec = (k: keyof typeof sec) => setSec(s => ({ ...s, [k]: !s[k] }));

  // Remnawave state
  const [squadsInt,      setSquadsInt]      = useState<SelectOption[]>([]);
  const [squadsExt,      setSquadsExt]      = useState<SelectOption[]>([]);
  const [plugins,        setPlugins]        = useState<SelectOption[]>([]);
  const [templates,      setTemplates]      = useState<Template[]>([]);
  const [remnavaveReady, setRemnavaveReady] = useState(false);
  const [squadsLoading,  setSquadsLoading]  = useState(false);
  // Tracks the "intended" new_ssh_port so toggling change_ssh_port off and
  // back on restores the original value rather than staying at current_ssh_port.
  const intendedNewPort = useRef(initial?.new_ssh_port ?? FORM_DEFAULT.new_ssh_port);

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(data => {
        const d = data.deploy_defaults ?? {};
        const r = data.remnawave ?? {};
        // Only apply settings defaults when no `initial` override
        if (!initial) {
          const opt = data.optimization ?? {};
          // Sync ref so toggle restore uses the settings value (default: 2222)
          if (d.new_ssh_port) intendedNewPort.current = String(d.new_ssh_port);
          setForm(prev => ({
            ...prev,
            ssh_user:           d.ssh_user            || prev.ssh_user,
            email:              d.email               || prev.email,
            cloudflare_api_key: d.cloudflare_api_key  || prev.cloudflare_api_key,
            open_ports:         d.open_ports           || prev.open_ports,
            whitelist_ips:      d.whitelist_ips        ?? prev.whitelist_ips,
            current_ssh_port:   d.current_ssh_port     ? String(d.current_ssh_port) : prev.current_ssh_port,
            new_ssh_port:       d.new_ssh_port         ? String(d.new_ssh_port)     : prev.new_ssh_port,
            change_ssh_port:    d.change_ssh_port      ?? prev.change_ssh_port,
            remnanode_port:     d.remnanode_port       ? String(d.remnanode_port)   : prev.remnanode_port,
            xhttp_path:         d.xhttp_path           ?? prev.xhttp_path,
            internal_squad_ids: r.default_internal_squad_ids ?? prev.internal_squad_ids,
            external_squad_ids: r.default_external_squad_ids ?? prev.external_squad_ids,
            // Inherit global optimization defaults
            opt_network_tuning: opt.network_tuning ?? prev.opt_network_tuning,
            opt_bbr:            opt.bbr            ?? prev.opt_bbr,
            opt_system_limits:  opt.system_limits  ?? prev.opt_system_limits,
            opt_dns:            opt.dns            ?? prev.opt_dns,
            opt_dns_servers:    opt.dns_servers    || prev.opt_dns_servers,
            // Inherit HAProxy defaults
            haproxy_source_port:     d.haproxy_source_port     ? String(d.haproxy_source_port) : prev.haproxy_source_port,
            haproxy_dest_port:       d.haproxy_dest_port       ? String(d.haproxy_dest_port)   : prev.haproxy_dest_port,
            haproxy_maxconn:         d.haproxy_maxconn         ? String(d.haproxy_maxconn)     : prev.haproxy_maxconn,
            haproxy_log:             d.haproxy_log             ?? prev.haproxy_log,
            haproxy_mode:            d.haproxy_mode            ?? prev.haproxy_mode,
            haproxy_timeout_connect: d.haproxy_timeout_connect ?? prev.haproxy_timeout_connect,
            haproxy_timeout_client:  d.haproxy_timeout_client  ?? prev.haproxy_timeout_client,
            haproxy_timeout_server:  d.haproxy_timeout_server  ?? prev.haproxy_timeout_server,
            haproxy_timeout_tunnel:  d.haproxy_timeout_tunnel  ?? prev.haproxy_timeout_tunnel,
          }));
        }

        const configured = !!(r.panel_url && r.api_token);
        setRemnavaveReady(configured);

        if (configured) {
          setSquadsLoading(true);
          const toOpts = (arr: unknown) =>
            (Array.isArray(arr) ? arr : []).map((s: { uuid: string; name: string }) =>
              ({ value: s.uuid, label: s.name }));
          Promise.all([
            fetch("/api/remnawave/squads/internal").then(r => r.json()).catch(() => []),
            fetch("/api/remnawave/squads/external").then(r => r.json()).catch(() => []),
            fetch("/api/remnawave/node-plugins").then(r => r.json()).catch(() => []),
          ]).then(([int, ext, plug]) => {
            setSquadsInt(toOpts(int));
            setSquadsExt(toOpts(ext));
            setPlugins(toOpts(plug));
          }).finally(() => setSquadsLoading(false));
        }
      })
      .catch(() => {});

    fetch("/api/templates")
      .then(r => r.json())
      .then(list => {
        if (!Array.isArray(list)) return;
        setTemplates(list);
        if (!initial?.template_id) {
          const def = list.find((t: Template) => t.is_default);
          if (def) setForm(prev => ({ ...prev, template_id: def.id }));
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (name: keyof FormData, value: string | boolean | string[]) =>
    setForm(f => {
      const next = { ...f, [name]: value };
      if (touched) setErrors(validateForm(next));
      return next;
    });

  const toggleRemnawave = () =>
    setForm(prev => {
      const next = {
        ...prev,
        create_in_remnawave: !prev.create_in_remnawave,
        remnanode_token: !prev.create_in_remnawave ? "" : prev.remnanode_token,
      };
      if (touched) setErrors(validateForm(next));
      return next;
    });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const errs = validateForm(form);
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      // Open any collapsed section that hides an errored field.
      setSec(s => {
        const n = { ...s };
        for (const k of Object.keys(errs) as (keyof FormData)[]) {
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
      await onSubmit(form);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : "Ошибка сервера");
    } finally {
      setSubmitting(false);
    }
  };

  const f = submitting;
  const isRemna = form.mode === "remnanode";

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3">

      {/* ── Режим деплоя (горизонтальные вкладки) ── */}
      <div className="seg accent">
        {([
          { id: "remnanode" as DeployMode, label: "Remnanode" },
          { id: "haproxy"   as DeployMode, label: "HAProxy" },
        ]).map(t => (
          <button
            key={t.id}
            type="button"
            onClick={() => setForm(prev => {
              const updated = { ...prev, mode: t.id };
              if (touched) setErrors(validateForm(updated));
              return updated;
            })}
            disabled={f}
            className={`flex-1 text-sm font-medium
                        focus:outline-none disabled:opacity-50
                        ${form.mode === t.id ? "on" : ""}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Сервер ── */}
      <SectionLabel>Сервер</SectionLabel>
      <div className="grid grid-cols-2 gap-3">
        <Field label="IP-адрес"  name="ip"       value={form.ip}       onChange={set}
          placeholder="1.2.3.4" error={errors.ip} disabled={f} />
        <Field label="SSH логин" name="ssh_user"  value={form.ssh_user} onChange={set}
          placeholder="root" error={errors.ssh_user} disabled={f} />
      </div>
      <Field label="SSH пароль" name="ssh_password" value={form.ssh_password}
        onChange={set} error={errors.ssh_password} disabled={f} secret />
      <Toggle label="Обновить систему перед стартом"
        checked={form.update_system}
        onChange={() => set("update_system", !form.update_system)} disabled={f} />

      {/* ── Remnanode (только Remnanode) ── */}
      {isRemna && (
      <>
      <SectionLabel>Remnanode</SectionLabel>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Порт remnanode" name="remnanode_port" value={form.remnanode_port}
          onChange={set} placeholder="2222" error={errors.remnanode_port} disabled={f} />
        <Field label="Путь XHTTP" name="xhttp_path" value={form.xhttp_path}
          onChange={set} placeholder="/xray/" disabled={f} hint="Опционально" />
      </div>
      <CountrySelect
        label="Страна ноды"
        value={form.country_code}
        onChange={v => set("country_code", v)}
        error={errors.country_code}
        disabled={f}
      />
      <Toggle label="Установить WARP Native"
        checked={form.install_warp}
        onChange={() => set("install_warp", !form.install_warp)} disabled={f} />

      {/* ── Remnawave (сворачиваемая) ── */}
      <Collapsible title="Remnawave" open={sec.remnawave} onToggle={() => toggleSec("remnawave")}>
        <Field label="Токен Remnanode" name="remnanode_token" value={form.remnanode_token}
          onChange={set} error={errors.remnanode_token}
          disabled={f || form.create_in_remnawave} secret
          hint={form.create_in_remnawave
            ? "Токен будет получен автоматически из панели Remnawave"
            : undefined} />

        {/* Remnawave integration block */}
        <div
          className={`rounded-lg border p-3 flex flex-col gap-3 ${remnavaveReady ? "" : "opacity-60"}`}
          style={{ borderColor: remnavaveReady ? "var(--line)" : "var(--line-soft)", background: "var(--bg2)" }}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
              Регистрация в панели
            </span>
            {!remnavaveReady && (
              <span className="flex items-center gap-1 text-[11px]" style={{ color: "var(--warn)" }}>
                <AlertCircle size={11} /> Не настроено
              </span>
            )}
          </div>

          <div
            className="flex flex-col gap-3"
            title={!remnavaveReady
              ? "Для активации полей настройте валидное подключение в разделе Настройки → Remnawave"
              : undefined}
          >
            <Toggle
              label="Зарегистрировать ноду в панели Remnawave"
              checked={form.create_in_remnawave}
              onChange={toggleRemnawave}
              disabled={f || !remnavaveReady}
            />

            {/* Template */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-medium uppercase tracking-widest"
                     style={{ color: !remnavaveReady ? "var(--t-faint)" : "var(--t-low)" }}>
                Шаблон конфигурации
                {form.create_in_remnawave && remnavaveReady && (
                  <span className="ml-0.5" style={{ color: "var(--err)" }}>*</span>
                )}
              </label>
              <select
                value={form.template_id}
                onChange={e => set("template_id", e.target.value)}
                disabled={f || !remnavaveReady}
                className="selectbox transition-colors"
                style={errors.template_id ? { borderColor: "var(--err-line)" } : undefined}
              >
                <option value="">— выберите шаблон —</option>
                {templates.map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
              {errors.template_id
                ? <p className="errmsg">{errors.template_id}</p>
                : <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>Xray JSON с подстановкой $domain, $name, $privkey, $shortid</p>
              }
            </div>

            {/* Internal squads multi-select */}
            <MultiSelect
              label="Внутренние сквады"
              selected={form.internal_squad_ids}
              onChange={v => set("internal_squad_ids", v)}
              options={squadsInt}
              placeholder={squadsLoading ? "Загрузка..." : "— без сквадов —"}
              disabled={f || !remnavaveReady || squadsLoading}
            />

            {/* External squads multi-select */}
            <MultiSelect
              label="Внешние сквады"
              selected={form.external_squad_ids}
              onChange={v => set("external_squad_ids", v)}
              options={squadsExt}
              placeholder={squadsLoading ? "Загрузка..." : "— без сквадов —"}
              disabled={f || !remnavaveReady || squadsLoading}
            />

            {/* Node plugin single-select */}
            <div className="flex flex-col gap-1">
              <label className="text-[11px] font-medium uppercase tracking-widest"
                     style={{ color: !remnavaveReady ? "var(--t-faint)" : "var(--t-low)" }}>
                Плагин ноды
              </label>
              <select
                value={form.plugin_uuid}
                onChange={e => set("plugin_uuid", e.target.value)}
                disabled={f || !remnavaveReady || squadsLoading}
                className="selectbox transition-colors"
              >
                <option value="">{squadsLoading ? "Загрузка..." : "Не использовать плагин"}</option>
                {plugins.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </Collapsible>

      {/* ── Домен и SSL (сворачиваемая) ── */}
      <Collapsible title="Домен и SSL" open={sec.domain} onToggle={() => toggleSec("domain")}>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
            Провайдер сертификата
          </label>
          <select
            value={form.cert_provider}
            onChange={e => set("cert_provider", e.target.value)}
            disabled={f}
            className="selectbox transition-colors"
          >
            {CERT_PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Домен ноды" name="domain" value={form.domain} onChange={set}
            placeholder="node1.example.com" error={errors.domain} disabled={f} />
          <Field label="Email (ACME)" name="email" value={form.email}
            onChange={set} type="email" placeholder="you@example.com" error={errors.email} disabled={f} />
        </div>
        {form.cert_provider === "cloudflare" && (
          <Field label="Cloudflare API токен" name="cloudflare_api_key"
            value={form.cloudflare_api_key} onChange={set}
            placeholder="DNS:Edit permission" error={errors.cloudflare_api_key} disabled={f} secret />
        )}
      </Collapsible>
      </>
      )}

      {/* ── Настройки HAProxy (только HAProxy) — выше «Сети» ── */}
      {!isRemna && (
      <>
      <SectionLabel>Настройки HAProxy</SectionLabel>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Порт HAProxy" name="haproxy_source_port" value={form.haproxy_source_port}
          onChange={set} placeholder="443" error={errors.haproxy_source_port} disabled={f} />
        <Field label="Целевой IP" name="haproxy_dest_ip" value={form.haproxy_dest_ip}
          onChange={set} placeholder="10.0.0.5" error={errors.haproxy_dest_ip} disabled={f} />
        <Field label="Целевой порт" name="haproxy_dest_port" value={form.haproxy_dest_port}
          onChange={set} placeholder="443" error={errors.haproxy_dest_port} disabled={f} />
        <Field label="Лимит подключений" name="haproxy_maxconn" value={form.haproxy_maxconn}
          onChange={set} placeholder="200000" error={errors.haproxy_maxconn} disabled={f} />
        <Field label="Тип лога" name="haproxy_log" value={form.haproxy_log}
          onChange={set} placeholder="global" disabled={f} />
        <Field label="Режим" name="haproxy_mode" value={form.haproxy_mode}
          onChange={set} placeholder="tcp" disabled={f} />
        <Field label="Timeout подключения" name="haproxy_timeout_connect" value={form.haproxy_timeout_connect}
          onChange={set} placeholder="5s" disabled={f} />
        <Field label="Timeout клиента" name="haproxy_timeout_client" value={form.haproxy_timeout_client}
          onChange={set} placeholder="50s" disabled={f} />
        <Field label="Timeout сервера" name="haproxy_timeout_server" value={form.haproxy_timeout_server}
          onChange={set} placeholder="50s" disabled={f} />
        <Field label="Timeout туннеля" name="haproxy_timeout_tunnel" value={form.haproxy_timeout_tunnel}
          onChange={set} placeholder="1h" disabled={f} />
      </div>
      </>
      )}

      {/* ── Сеть (сворачиваемая) ── */}
      <Collapsible title="Сеть" open={sec.network} onToggle={() => toggleSec("network")}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Текущий SSH порт" name="current_ssh_port" value={form.current_ssh_port}
            onChange={(name, v) => {
              set(name, v);
              if (!form.change_ssh_port) set("new_ssh_port", v);
            }}
            placeholder="22" error={errors.current_ssh_port} disabled={f} />
          <Field label="Новый SSH порт"   name="new_ssh_port"     value={form.new_ssh_port}
            onChange={(name, v) => { set(name, v); intendedNewPort.current = v; }}
            placeholder="2222" error={errors.new_ssh_port}
            disabled={f || !form.change_ssh_port} />
        </div>
        <Toggle
          label="Сменить порт SSH"
          checked={form.change_ssh_port}
          onChange={() => {
            const next = !form.change_ssh_port;
            setForm(prev => {
              const updated = {
                ...prev,
                change_ssh_port: next,
                new_ssh_port: next ? intendedNewPort.current : prev.current_ssh_port,
              };
              if (touched) setErrors(validateForm(updated));
              return updated;
            });
          }}
          disabled={f}
        />
        <Field label="Порты UFW" name="open_ports" value={form.open_ports}
          onChange={set} placeholder="80,443" error={errors.open_ports} disabled={f} />
      </Collapsible>

      {/* ── Оптимизация ОС (сворачиваемая, оба режима) ── */}
      <Collapsible title="Оптимизация ОС" open={sec.opt} onToggle={() => toggleSec("opt")}
        icon={<Zap size={12} style={{ color: form.optimize ? "var(--warn)" : "var(--t-faint)" }} />}>
        <Toggle
          label="Применить оптимизацию ОС"
          checked={form.optimize}
          onChange={() => set("optimize", !form.optimize)}
          disabled={f}
        />
        <div className={`flex flex-col gap-2 ${!form.optimize ? "opacity-40 pointer-events-none" : ""}`}>
          <Toggle label="BBR (TCP congestion control)"
            checked={form.opt_bbr} onChange={() => set("opt_bbr", !form.opt_bbr)}
            disabled={f || !form.optimize} />
          <Toggle label="TCP/UDP буферы (network tuning)"
            checked={form.opt_network_tuning} onChange={() => set("opt_network_tuning", !form.opt_network_tuning)}
            disabled={f || !form.optimize} />
          <Toggle label="Системные лимиты (nofile 1 000 000)"
            checked={form.opt_system_limits} onChange={() => set("opt_system_limits", !form.opt_system_limits)}
            disabled={f || !form.optimize} />
          <Toggle label="DNS-серверы (переписать /etc/resolv.conf)"
            checked={form.opt_dns} onChange={() => set("opt_dns", !form.opt_dns)}
            disabled={f || !form.optimize} />
          {form.opt_dns && form.optimize && (
            <Field label="DNS-серверы" name="opt_dns_servers" value={form.opt_dns_servers}
              onChange={set} placeholder="1.1.1.1,8.8.8.8" disabled={f}
              hint="Через запятую, например: 1.1.1.1,8.8.8.8" />
          )}
        </div>

        <div className="h-px my-1" style={{ background: "var(--line-soft)" }} />

        {isRemna && (
          <Toggle label="Нода за CDN"
            checked={form.behind_cdn}
            onChange={() => set("behind_cdn", !form.behind_cdn)} disabled={f} />
        )}
        <Toggle label="Установить vnstat (учёт трафика)"
          checked={form.install_vnstat}
          onChange={() => set("install_vnstat", !form.install_vnstat)} disabled={f} />
        <Toggle label="Установить TrafficGuard"
          checked={form.install_trafficguard}
          onChange={() => set("install_trafficguard", !form.install_trafficguard)} disabled={f} />

        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium uppercase tracking-widest" style={{ color: "var(--t-low)" }}>
            Whitelist IP / CIDR
          </label>
          <textarea
            value={form.whitelist_ips}
            onChange={e => set("whitelist_ips", e.target.value)}
            disabled={f}
            rows={2}
            spellCheck={false}
            placeholder="1.2.3.4, 10.0.0.0/24"
            className="input transition-colors"
            style={{ resize: "vertical", minHeight: "2.4rem" }}
          />
          <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
            Доверенные адреса для fail2ban/UFW. Через запятую, пробел или с новой строки.
          </p>
        </div>
        <Toggle label="Разрешить SSH-подключение для всех"
          checked={form.allow_ssh_all}
          onChange={() => set("allow_ssh_all", !form.allow_ssh_all)} disabled={f} />
      </Collapsible>

      {apiError && (
        <div className="mt-2 px-3 py-2 rounded-md border text-xs"
             style={{ background: "var(--err-dim)", borderColor: "var(--err-line)", color: "var(--err)" }}>{apiError}</div>
      )}

      <div className="mt-3 flex gap-2">
        <button type="submit" disabled={submitting}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                     font-semibold text-sm transition-all bg-[var(--accent)] text-[var(--primary-ink)]
                     hover:bg-[var(--accent-hi)]
                     active:bg-[var(--accent)] disabled:bg-[var(--accent-dim)] disabled:cursor-not-allowed
                     focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)]">
          {submitting
            ? <><Loader2 size={15} className="animate-spin" /> Запуск...</>
            : <><Rocket size={15} /> Запустить деплой</>
          }
        </button>
        {onCancel && !submitting && (
          <button type="button" onClick={onCancel}
            className="px-4 py-2.5 rounded-lg text-sm font-medium
                       text-[var(--t-low)] hover:text-[var(--t-hi)] hover:bg-[var(--bg3)] transition-colors
                       focus:outline-none focus:ring-1 focus:ring-[var(--line)]">
            Отмена
          </button>
        )}
      </div>
    </form>
  );
}
