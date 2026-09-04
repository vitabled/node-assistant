import { useState } from "react";
import { Loader2, ShieldCheck, ScanSearch, Square } from "lucide-react";
import { ScanDomainsModal } from "./ScanDomainsModal";
import { Field as FieldShell, InputShell, Select, Toggle } from "../theme/ui";

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
  return (
    <FieldShell label={label} error={error} hint={hint}>
      <InputShell
        type={type}
        value={value}
        onChange={(e) => onChange(name, e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        secret={secret}
        error={!!error}
      />
    </FieldShell>
  );
}

interface Props {
  onSubmit: (data: CertsFormData) => Promise<void>;
  disabled: boolean;
  /** «Авто» добавил домены — родитель обновляет «Домены». */
  onDomainsAdded?: () => void;
  /** Идёт деплой — на месте кнопки старта показываем «Остановить» (Wave-5 PR-4). */
  onStop?: () => void;
}

export function CertsForm({ onSubmit, disabled, onDomainsAdded, onStop }: Props) {
  const [form,    setForm]    = useState<CertsFormData>(DEFAULT);
  const [errors,  setErrors]  = useState<Partial<Record<keyof CertsFormData, string>>>({});
  const [touched, setTouched] = useState(false);
  const [scanOpen, setScanOpen] = useState(false);

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
        <div>
          <div className="flex items-center justify-between">
            <span />
            {/* «Авто» — сервис сам собирает домены сервера по SSH (Wave-4 PR-3) */}
            <button type="button" className="text-[11px] font-semibold flex items-center gap-1
                       hover:underline disabled:opacity-40"
              style={{ color: "var(--accent-hi)" }}
              disabled={f}
              onClick={() => setScanOpen(true)}>
              <ScanSearch size={11} /> Авто
            </button>
          </div>
          <Field label="Домен" name="domain" value={form.domain} onChange={set}
            placeholder="node1.example.com" error={errors.domain} disabled={f} />
        </div>
      </div>

      {scanOpen && (
        <ScanDomainsModal
          defaults={{ ip: form.ip, ssh_user: form.ssh_user,
                      ssh_password: form.ssh_password, ssh_port: form.ssh_port }}
          onClose={() => setScanOpen(false)}
          onAdded={() => onDomainsAdded?.()}
        />
      )}

      <p className="text-[11px] font-semibold uppercase tracking-widest mt-1" style={{ color: "var(--t-faint)" }}>
        Сертификат
      </p>

      <FieldShell label="Провайдер сертификата">
        <Select
          value={form.cert_provider}
          onChange={v => set("cert_provider", v)}
          disabled={f}
          options={CERT_PROVIDERS.map(p => ({ value: p.value, label: p.label }))}
        />
      </FieldShell>

      {form.cert_provider === "cloudflare" ? (
        <Field label="Cloudflare API токен" name="cf_api_key" value={form.cf_api_key}
          onChange={set} placeholder="DNS:Edit permission" error={errors.cf_api_key}
          disabled={f} secret />
      ) : (
        <Field label="Email (ACME)" name="email" value={form.email} onChange={set}
          type="email" placeholder="you@example.com" error={errors.email} disabled={f}
          hint="Для Let's Encrypt / ZeroSSL — регистрация ACME/EAB" />
      )}

      <Toggle label="Переустановить, даже если серт уже есть" checked={form.force}
        onChange={() => set("force", (!form.force) as unknown as string)} disabled={f} />

      {form.cert_provider !== "cloudflare" && (
        <div className="px-3 py-2.5 rounded-lg border text-xs leading-relaxed"
             style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
          HTTP-01 (порт 80): домен должен уже указывать на этот сервер — мы не управляем DNS для этого провайдера.
        </div>
      )}

      <button
        type={disabled && onStop ? "button" : "submit"}
        onClick={disabled && onStop ? onStop : undefined}
        className="mt-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                   font-semibold text-sm transition-all bg-[var(--accent)] text-[var(--primary-ink)]
                   hover:bg-[var(--accent-hi)] disabled:cursor-not-allowed
                   focus:outline-none focus:ring-2 focus:ring-[var(--accent-line)]"
        style={disabled && !onStop ? { background: "var(--bg3)", color: "var(--t-faint)" }
             : disabled && onStop ? { background: "var(--err-dim)", color: "var(--err)", border: "1px solid var(--err-line)" } : undefined}
        disabled={disabled && !onStop}
      >
        {disabled
          ? (onStop
            ? <><Square size={14} /> Остановить деплой</>
            : <><Loader2 size={15} className="animate-spin" /> Выполняется...</>)
          : <><ShieldCheck size={15} /> Задеплоить сертификат</>
        }
      </button>
    </form>
  );
}
