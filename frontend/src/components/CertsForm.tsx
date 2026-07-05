import { useState } from "react";
import { Loader2, RefreshCw, ChevronDown, ChevronUp, Eye, EyeOff } from "lucide-react";

export interface CertsFormData {
  ip:           string;
  ssh_user:     string;
  ssh_password: string;
  ssh_port:     string;
  domain:       string;
  cf_api_key:   string;   // optional; empty = use stored token
}

const DEFAULT: CertsFormData = {
  ip:           "",
  ssh_user:     "root",
  ssh_password: "",
  ssh_port:     "22",
  domain:       "",
  cf_api_key:   "",
};

const IPv4   = /^(\d{1,3}\.){3}\d{1,3}$/;
const DOMAIN = /^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$/;

function validate(f: CertsFormData): Partial<Record<keyof CertsFormData, string>> {
  const e: Partial<Record<keyof CertsFormData, string>> = {};
  if (!IPv4.test(f.ip) || f.ip.split(".").some((o) => parseInt(o) > 255))
    e.ip = "Неверный IPv4";
  if (!f.ssh_user.trim())     e.ssh_user = "Обязательное поле";
  if (!f.ssh_password)        e.ssh_password = "Обязательное поле";
  if (!DOMAIN.test(f.domain)) e.domain = "Неверный домен";
  const port = parseInt(f.ssh_port, 10);
  if (isNaN(port) || port < 1 || port > 65535) e.ssh_port = "1–65535";
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
      <label className="label">
        {label}
      </label>
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
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShow(v => !v)}
            className="absolute inset-y-0 right-0 flex items-center px-2.5
                       text-[var(--t-faint)] hover:text-[var(--t-mid)] transition-colors"
          >
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
  const [showAdv, setShowAdv] = useState(false);

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
          placeholder="example.com" error={errors.domain} disabled={f} />
      </div>

      {/* Advanced: optional CF token override */}
      <button
        type="button"
        onClick={() => setShowAdv((v) => !v)}
        disabled={f}
        className="flex items-center gap-1 text-[11px] text-[var(--t-faint)] hover:text-[var(--t-low)]
                   transition-colors self-start disabled:opacity-40"
      >
        {showAdv ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        Дополнительно
      </button>

      {showAdv && (
        <Field
          label="Cloudflare API токен (опционально)"
          name="cf_api_key"
          value={form.cf_api_key}
          onChange={set}
          placeholder="Оставьте пустым — используется сохранённый токен"
          hint="Нужен только если токен изменился после первичного деплоя."
          disabled={f}
          secret
        />
      )}

      <div className="px-3 py-2.5 rounded-lg border text-xs leading-relaxed"
           style={{ background: "var(--warn-dim)", borderColor: "var(--warn-line)", color: "var(--warn)" }}>
        Обновление использует acme.sh и CF_Token, сохранённые при деплое.
        Откройте «Дополнительно» чтобы переопределить токен.
      </div>

      <button
        type="submit"
        disabled={disabled}
        className="mt-1 flex items-center justify-center gap-2 py-2.5 rounded-lg
                   font-semibold text-sm transition-all
                   bg-teal-700 hover:bg-teal-600 active:bg-teal-800
                   disabled:cursor-not-allowed
                   focus:outline-none focus:ring-2 focus:ring-teal-500/50"
        style={disabled ? { background: "var(--bg3)", color: "var(--t-faint)" } : undefined}
      >
        {disabled
          ? <><Loader2 size={15} className="animate-spin" /> Выполняется...</>
          : <><RefreshCw size={15} /> Обновить сертификаты</>
        }
      </button>
    </form>
  );
}
