import { useState } from "react";
import { Loader2, ShieldCheck, Eye, EyeOff } from "lucide-react";

export interface CertsFormData {
  ip:            string;
  ssh_user:      string;
  ssh_password:  string;
  ssh_port:      string;
  domain:        string;
  cert_provider: string;   // cloudflare | letsencrypt | zerossl
  email:         string;   // required for letsencrypt/zerossl (ACME/EAB)
  cf_api_key:    string;   // only for cloudflare
  force:         boolean;  // redeploy even if a valid cert is present
}

const DEFAULT: CertsFormData = {
  ip:            "",
  ssh_user:      "root",
  ssh_password:  "",
  ssh_port:      "22",
  domain:        "",
  cert_provider: "cloudflare",
  email:         "",
  cf_api_key:    "",
  force:         false,
};

const CERT_PROVIDERS: { value: string; label: string }[] = [
  { value: "cloudflare",  label: "Cloudflare (DNS-01)" },
  { value: "letsencrypt", label: "Let's Encrypt (HTTP-01)" },
  { value: "zerossl",     label: "ZeroSSL (acme.sh + EAB)" },
];

const IPv4   = /^(\d{1,3}\.){3}\d{1,3}$/;
const DOMAIN = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$/;
const EMAIL  = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validate(f: CertsFormData): Partial<Record<keyof CertsFormData, string>> {
  const e: Partial<Record<keyof CertsFormData, string>> = {};
  if (!IPv4.test(f.ip) || f.ip.split(".").some((o) => parseInt(o) > 255))
    e.ip = "Неверный IPv4";
  if (!f.ssh_user.trim())     e.ssh_user = "Обязательное поле";
  if (!f.ssh_password)        e.ssh_password = "Обязательное поле";
  if (!DOMAIN.test(f.domain)) e.domain = "Неверный домен";
  const port = parseInt(f.ssh_port, 10);
  if (isNaN(port) || port < 1 || port > 65535) e.ssh_port = "1–65535";
  // Cloudflare token required only for cloudflare; email required for the others.
  if (f.cert_provider === "cloudflare" && !f.cf_api_key.trim())
    e.cf_api_key = "Обязательное поле для Cloudflare";
  if (f.cert_provider !== "cloudflare" && !EMAIL.test(f.email))
    e.email = "Неверный email";
  return e;
}

interface FieldProps {
  label:        string;
  name:         keyof CertsFormData;
  value:        string;
  onChange:     (n: keyof CertsFormData, v: string) => void;
  error?:       string;
  hint?:        string;
  type?:        string;
  placeholder?: string;
  disabled?:    boolean;
  secret?:      boolean;
}

function Field({ label, name, value, onChange, error, hint, type = "text", placeholder, disabled, secret }: FieldProps) {
  const [show, setShow] = useState(false);
  const inputType = secret ? (show ? "text" : "password") : type;

  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <div className={secret ? "relative" : undefined}>
        <input
          type={inputType}
          value={value}
          onChange={(e) => onChange(name, e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          className={`input ${secret ? "pr-9" : ""} ${error ? "err" : ""}`}
        />
        {secret && (
          <button type="button" tabIndex={-1} onClick={() => setShow(v => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2.5
                       text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors">
            {show ? <EyeOff size={13} /> : <Eye size={13} />}
          </button>
        )}
      </div>
      {error && <p className="errmsg">{error}</p>}
      {hint  && !error && <p className="hint">{hint}</p>}
    </div>
  );
}

interface Props {
  onSubmit: (data: CertsFormData) => Promise<void>;
  disabled: boolean;
}

export function CertsForm({ onSubmit, disabled }: Props) {
  const [form,    setForm]    = useState<CertsFormData>(DEFAULT);
  const [errors,  setErrors]  = useState<Partial<Record<keyof CertsFormData, string>>>({});
  const [touched, setTouched] = useState(false);

  const set = (name: keyof CertsFormData, value: string) =>
    setForm((f) => {
      const next = { ...f, [name]: value };
      if (touched) setErrors(validate(next));
      return next;
    });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    const errs = validate(form);
    if (Object.keys(errs).length > 0) { setErrors(errs); return; }
    setErrors({});
    await onSubmit(form);
  };

  const f = disabled;

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-3">
      <p className="text-[11px] font-semibold uppercase tracking-widest mt-1" style={{ color: "var(--t-faint)" }}>
        Подключение
      </p>

      <div className="grid grid-cols-2 gap-3">
        <Field label="IP-адрес" name="ip" value={form.ip} onChange={set}
          placeholder="1.2.3.4" error={errors.ip} disabled={f} />
        <Field label="SSH логин" name="ssh_user" value={form.ssh_user} onChange={set}
          placeholder="root" error={errors.ssh_user} disabled={f} />
      </div>

      <Field label="SSH пароль" name="ssh_password" value={form.ssh_password} onChange={set}
        error={errors.ssh_password} disabled={f} secret />

      <div className="grid grid-cols-2 gap-3">
        <Field label="Порт подключения" name="ssh_port" value={form.ssh_port} onChange={set}
          placeholder="22" error={errors.ssh_port} disabled={f} />
        <Field label="Домен" name="domain" value={form.domain} onChange={set}
          placeholder="node1.example.com" error={errors.domain} disabled={f} />
      </div>

      <p className="text-[11px] font-semibold uppercase tracking-widest mt-1" style={{ color: "var(--t-faint)" }}>
        Сертификат
      </p>

      <div className="flex flex-col gap-1">
        <label className="label">Провайдер сертификата</label>
        <select
          value={form.cert_provider}
          onChange={e => set("cert_provider", e.target.value)}
          disabled={f}
          className="selectbox transition-colors"
        >
          {CERT_PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
      </div>

      {form.cert_provider === "cloudflare" ? (
        <Field label="Cloudflare API токен" name="cf_api_key" value={form.cf_api_key}
          onChange={set} placeholder="DNS:Edit permission" error={errors.cf_api_key}
          disabled={f} secret />
      ) : (
        <Field label="Email (ACME)" name="email" value={form.email} onChange={set}
          type="email" placeholder="you@example.com" error={errors.email} disabled={f}
          hint="Для Let's Encrypt / ZeroSSL — регистрация ACME/EAB" />
      )}

      <label className={`flex items-center gap-2.5 cursor-pointer select-none mt-1 ${f ? "opacity-40 pointer-events-none" : ""}`}>
        <button type="button" role="switch" aria-checked={form.force}
          onClick={() => set("force", (!form.force) as unknown as string)}
          className={`relative w-9 h-5 rounded-full transition-colors focus:outline-none
                      focus:ring-2 focus:ring-[var(--accent-line)]
                      ${form.force ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow
                            transition-transform duration-200 ${form.force ? "translate-x-4" : "translate-x-0"}`} />
        </button>
        <span className="text-sm" style={{ color: "var(--t-low)" }}>Переустановить, даже если серт уже есть</span>
      </label>

      {form.cert_provider !== "cloudflare" && (
        <div className="px-3 py-2.5 rounded-lg border text-xs leading-relaxed"
             style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
          HTTP-01 (порт 80): домен должен уже указывать на этот сервер — мы не управляем DNS для этого провайдера.
        </div>
      )}

      <button
        type="submit"
        disabled={disabled}
        className="mt-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                   font-semibold text-sm transition-all bg-[var(--accent)] text-[var(--primary-ink)]
                   hover:bg-[var(--accent-hi)] disabled:cursor-not-allowed
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)]"
        style={disabled ? { background: "var(--bg3)", color: "var(--t-faint)" } : undefined}
      >
        {disabled
          ? <><Loader2 size={15} className="animate-spin" /> Выполняется...</>
          : <><ShieldCheck size={15} /> Задеплоить сертификат</>
        }
      </button>
    </form>
  );
}
