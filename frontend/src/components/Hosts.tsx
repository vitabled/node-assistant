import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, X, Loader2, Check, Server, Eye, EyeOff } from "lucide-react";
import { MultiSelect } from "./MultiSelect";
import { loadDeployNodes } from "./infra/ui";

// ── Types (mirrors backend Pydantic HostTemplateBody exactly) ──

export interface HostTemplate {
  id?: string;
  visible: boolean;
  remark: string;
  inbound: string;
  address: string;
  port: number;
  tag: string;
  nodes: string[];
  exclude_squads: string[];
  sni: string;
  sni_from_address: boolean;
  sni_empty: boolean;
  host: string;
  path: string;
  security_layer: string;
  alpn: string;
  fingerprint: string;
  vless_route_id: number;
  hide_host: boolean;
  exclude_sub_types: string[];
  xray_json_template: string;
  xhttp: Record<string, unknown> | null;
  mux: Record<string, unknown> | null;
  sockopt: Record<string, unknown> | null;
  final_mask: Record<string, unknown> | null;
  server_description: string;
  shuffle_host: boolean;
  allow_insecure: boolean;
  x25519mlkem768: boolean;
}

// Editing form: same shape, but numeric fields are edited as text so the
// input can be empty/partial while typing; parsed back to numbers on save.
interface FormState {
  visible: boolean;
  remark: string;
  inbound: string;
  address: string;
  port: string;
  tag: string;
  nodes: string[];
  exclude_squads: string[];
  sni: string;
  sni_from_address: boolean;
  sni_empty: boolean;
  host: string;
  path: string;
  security_layer: string;
  alpn: string;
  fingerprint: string;
  vless_route_id: string;
  hide_host: boolean;
  exclude_sub_types: string[];
  xray_json_template: string;
  xhttp: Record<string, unknown> | null;
  mux: Record<string, unknown> | null;
  sockopt: Record<string, unknown> | null;
  final_mask: Record<string, unknown> | null;
  server_description: string;
  shuffle_host: boolean;
  allow_insecure: boolean;
  x25519mlkem768: boolean;
}

const FORM_DEFAULT: FormState = {
  visible: true,
  remark: "",
  inbound: "",
  address: "",
  port: "",
  tag: "ROUTING_HOST",
  nodes: [],
  exclude_squads: [],
  sni: "",
  sni_from_address: false,
  sni_empty: false,
  host: "",
  path: "",
  security_layer: "default",
  alpn: "",
  fingerprint: "",
  vless_route_id: "",
  hide_host: false,
  exclude_sub_types: [],
  xray_json_template: "",
  xhttp: null,
  mux: null,
  sockopt: null,
  final_mask: null,
  server_description: "",
  shuffle_host: false,
  allow_insecure: false,
  x25519mlkem768: false,
};

function fromTemplate(t: HostTemplate): FormState {
  return {
    visible: t.visible,
    remark: t.remark,
    inbound: t.inbound,
    address: t.address,
    port: String(t.port ?? ""),
    tag: t.tag,
    nodes: t.nodes ?? [],
    exclude_squads: t.exclude_squads ?? [],
    sni: t.sni,
    sni_from_address: t.sni_from_address,
    sni_empty: t.sni_empty,
    host: t.host,
    path: t.path,
    security_layer: t.security_layer,
    alpn: t.alpn,
    fingerprint: t.fingerprint,
    vless_route_id: t.vless_route_id ? String(t.vless_route_id) : "",
    hide_host: t.hide_host,
    exclude_sub_types: t.exclude_sub_types ?? [],
    xray_json_template: t.xray_json_template,
    xhttp: t.xhttp ?? null,
    mux: t.mux ?? null,
    sockopt: t.sockopt ?? null,
    final_mask: t.final_mask ?? null,
    server_description: t.server_description,
    shuffle_host: t.shuffle_host,
    allow_insecure: t.allow_insecure,
    x25519mlkem768: t.x25519mlkem768,
  };
}

function toPayload(f: FormState): Omit<HostTemplate, "id"> {
  const port = parseInt(f.port, 10);
  const routeRaw = f.vless_route_id.trim();
  const vless_route_id = routeRaw === "" ? 0 : parseInt(routeRaw, 10);
  return {
    visible: f.visible,
    remark: f.remark.trim(),
    inbound: f.inbound,
    address: f.address.trim(),
    port: isNaN(port) ? 0 : port,
    tag: f.tag,
    nodes: f.nodes,
    exclude_squads: f.exclude_squads,
    sni: f.sni,
    sni_from_address: f.sni_from_address,
    sni_empty: f.sni_empty,
    host: f.host,
    path: f.path,
    security_layer: f.security_layer,
    alpn: f.alpn,
    fingerprint: f.fingerprint,
    vless_route_id: isNaN(vless_route_id) ? 0 : vless_route_id,
    hide_host: f.hide_host,
    exclude_sub_types: f.exclude_sub_types,
    xray_json_template: f.xray_json_template,
    xhttp: f.xhttp,
    mux: f.mux,
    sockopt: f.sockopt,
    final_mask: f.final_mask,
    server_description: f.server_description,
    shuffle_host: f.shuffle_host,
    allow_insecure: f.allow_insecure,
    x25519mlkem768: f.x25519mlkem768,
  };
}

// ── Static option lists (no backing Remnawave API for these) ──

const TAG_OPTIONS = [
  { value: "ROUTING_HOST", label: "ROUTING_HOST" },
  { value: "DIRECT",       label: "DIRECT" },
  { value: "BLOCK",        label: "BLOCK" },
];

const SECURITY_LAYERS = [
  { value: "default", label: "По умолчанию" },
  { value: "tls",      label: "TLS" },
  { value: "reality",  label: "REALITY" },
];

const ALPN_OPTIONS = [
  { value: "",         label: "Авто" },
  { value: "h2",       label: "h2" },
  { value: "http/1.1", label: "http/1.1" },
  { value: "h3",       label: "h3" },
];

const FINGERPRINT_OPTIONS = [
  { value: "",           label: "Авто" },
  { value: "chrome",     label: "Chrome" },
  { value: "firefox",    label: "Firefox" },
  { value: "safari",     label: "Safari" },
  { value: "randomized", label: "Randomized" },
];

const SUB_TYPES: { value: string; label: string }[] = [
  { value: "xray_json",   label: "Xray JSON" },
  { value: "xray_base64", label: "Xray Base64" },
  { value: "mihomo",      label: "Mihomo" },
  { value: "stash",       label: "Stash" },
  { value: "singbox",     label: "Singbox" },
  { value: "clash",       label: "Clash" },
];

// ── Small field primitives (theme-aware, mirrors DeployForm/CertsForm) ──

interface FieldProps {
  label:        string;
  name:         keyof FormState;
  value:        string;
  onChange:     (n: keyof FormState, v: string) => void;
  type?:        string;
  placeholder?: string;
  hint?:        string;
  error?:       string;
  maxLength?:   number;
}

function Field({ label, name, value, onChange, type = "text", placeholder, hint, error, maxLength }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(name, e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
        autoComplete="off"
        spellCheck={false}
        className={`input ${error ? "err" : ""}`}
      />
      {error && <p className="errmsg">{error}</p>}
      {hint && !error && <p className="hint">{hint}</p>}
    </div>
  );
}

function SelectField({ label, value, onChange, options, hint }: {
  label: string; value: string; onChange: (v: string) => void;
  options: { value: string; label: string }[]; hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)} className="selectbox">
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: () => void }) {
  return (
    <label className="flex items-center gap-2.5 cursor-pointer select-none">
      <button type="button" role="switch" aria-checked={checked} onClick={onChange}
        className={`switch ${checked ? "on" : ""}`} />
      <span className="text-sm" style={{ color: "var(--t-low)" }}>{label}</span>
    </label>
  );
}

function CheckboxRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: () => void }) {
  return (
    <button type="button" onClick={onChange}
      className="flex items-center gap-2 text-left text-sm"
      style={{ color: "var(--t-low)" }}>
      <span className={`ck ${checked ? "on" : ""}`}>
        {checked && <Check size={10} />}
      </span>
      {label}
    </button>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return <p className="micro mt-1">{children}</p>;
}

// Minimal raw-JSON editor for the xhttp/mux/sockopt/final_mask sub-configs:
// a toggle button that reveals a textarea; parsed to an object on blur,
// or set back to null when left empty.
function JsonSubConfig({ label, value, onChange, onError }: {
  label: string;
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, unknown> | null) => void;
  onError: (hasError: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value ? JSON.stringify(value, null, 2) : "");
  const [err,  setErr]  = useState<string | null>(null);

  const toggle = () => {
    if (!open) setText(value ? JSON.stringify(value, null, 2) : "");
    setOpen(o => !o);
  };

  const handleBlur = () => {
    const t = text.trim();
    if (!t) { onChange(null); setErr(null); onError(false); return; }
    try {
      const parsed = JSON.parse(t);
      onChange(parsed);
      setErr(null);
      onError(false);
    } catch (e) {
      setErr((e as Error).message);
      onError(true);  // block save until the JSON is valid — don't silently drop it
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <button type="button" onClick={toggle} className="btn btn-soft">
        {label}{value ? " •" : ""}
      </button>
      {open && (
        <div className="flex flex-col gap-1">
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            onBlur={handleBlur}
            rows={4}
            spellCheck={false}
            placeholder="{}"
            className={`input font-mono text-xs ${err ? "err" : ""}`}
            style={{ resize: "vertical" }}
          />
          {err && <p className="errmsg">Некорректный JSON: {err}</p>}
        </div>
      )}
    </div>
  );
}

// ── Host card ─────────────────────────────────────────────────

function HostCard({ host, onEdit, onDelete }: {
  host: HostTemplate; onEdit: () => void; onDelete: () => void;
}) {
  return (
    <div className="card card-p flex items-center gap-3">
      <span className="dot" style={{ background: host.visible ? "var(--ok)" : "var(--t-faint)" }}
        title={host.visible ? "Виден" : "Скрыт"} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium trunc" style={{ color: "var(--t-hi)" }}>{host.remark}</p>
        <p className="text-xs trunc" style={{ color: "var(--t-low)" }}>{host.address}:{host.port}</p>
      </div>
      <span className="tag">{host.tag}</span>
      <button className="iconbtn" onClick={onEdit} title="Редактировать"><Pencil size={14} /></button>
      <button className="iconbtn danger" onClick={onDelete} title="Удалить"><Trash2 size={14} /></button>
    </div>
  );
}

// ── Editor modal ──────────────────────────────────────────────

function HostEditorModal({ initial, onClose, onSave }: {
  initial?: HostTemplate;
  onClose:  () => void;
  onSave:   (payload: Omit<HostTemplate, "id">) => Promise<void>;
}) {
  const [form,    setForm]    = useState<FormState>(initial ? fromTemplate(initial) : FORM_DEFAULT);
  const [tab,     setTab]     = useState<"basic" | "advanced">("basic");
  const [saving,  setSaving]  = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [nodeOptions] = useState(() => loadDeployNodes());

  const set = <K extends keyof FormState>(name: K, value: FormState[K]) =>
    setForm(f => ({ ...f, [name]: value }));

  // Sub-config JSON editors report parse errors here so a malformed one blocks
  // save (otherwise the bad edit is silently dropped and the old value saved).
  const [subErrors, setSubErrors] = useState<Record<string, boolean>>({});
  const subError = (label: string) => (has: boolean) =>
    setSubErrors(s => ({ ...s, [label]: has }));

  const port = parseInt(form.port, 10);
  const routeId = form.vless_route_id.trim() === "" ? 0 : parseInt(form.vless_route_id, 10);
  const canSave = form.remark.trim().length > 0
    && form.address.trim().length > 0
    && port > 0 && port <= 65535
    && (isNaN(routeId) || (routeId >= 0 && routeId <= 65535))
    && !Object.values(subErrors).some(Boolean);

  const handleSave = async () => {
    if (!canSave) return;
    setApiError(null);
    setSaving(true);
    try {
      await onSave(toPayload(form));
    } catch (e) {
      setApiError(e instanceof Error ? e.message : "Ошибка сервера");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal max-w-lg">
        <div className="sticky top-0 flex items-center justify-between px-5 py-3.5 z-10"
             style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--bg1)" }}>
          <div className="flex items-center gap-2">
            <Server size={14} style={{ color: "var(--accent-hi)" }} />
            <h2 className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
              {initial ? "Редактировать хост" : "Новый хост"}
            </h2>
          </div>
          <button onClick={onClose} className="iconbtn"><X size={15} /></button>
        </div>

        <div className="p-5 flex flex-col gap-3 overflow-y-auto">
          <Toggle label="Хост виден" checked={form.visible} onChange={() => set("visible", !form.visible)} />

          <div className="seg accent">
            <button type="button" onClick={() => setTab("basic")} className={tab === "basic" ? "on" : ""}>
              Основные
            </button>
            <button type="button" onClick={() => setTab("advanced")} className={tab === "advanced" ? "on" : ""}>
              Расширенные
            </button>
          </div>

          {tab === "basic" && (
            <>
              <GroupLabel>Базовые параметры</GroupLabel>
              <Field label="Примечание *" name="remark" value={form.remark} onChange={set} placeholder="Мой хост" />
              <Field label="Инбаунд" name="inbound" value={form.inbound} onChange={set}
                placeholder="Инбаунд не выбран" hint="Имя инбаунда Remnawave (задаётся вручную — API не используется)" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Адрес *" name="address" value={form.address} onChange={set} placeholder="example.com" />
                <Field label="Порт *" name="port" value={form.port} onChange={set} type="number" placeholder="443" />
              </div>
              <SelectField label="Tag" value={form.tag} onChange={v => set("tag", v)} options={TAG_OPTIONS}
                hint="Теги не видны конечным пользователям. Тег будет отправлен только с RAW подпиской." />
              <div className="flex flex-col gap-1">
                <MultiSelect label="Ноды" selected={form.nodes} onChange={v => set("nodes", v)}
                  options={nodeOptions} placeholder="— не выбрано —" />
                <p className="hint">Влияет только на визуальное отображение</p>
              </div>
              <MultiSelect label="Исключить из внутренних сквадов" selected={form.exclude_squads}
                onChange={v => set("exclude_squads", v)} options={[]} placeholder="— нет данных сквадов —" />
            </>
          )}

          {tab === "advanced" && (
            <>
              <GroupLabel>Переопределения соединений</GroupLabel>
              <Field label="SNI" name="sni" value={form.sni} onChange={set} placeholder="example.com" />
              <Toggle label="Переопределить SNI из адреса" checked={form.sni_from_address}
                onChange={() => set("sni_from_address", !form.sni_from_address)} />
              <Toggle label="Оставить SNI пустым" checked={form.sni_empty}
                onChange={() => set("sni_empty", !form.sni_empty)} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Хост" name="host" value={form.host} onChange={set} placeholder="example.com" />
                <Field label="Путь" name="path" value={form.path} onChange={set} placeholder="/path" />
              </div>
              <SelectField label="Security Layer" value={form.security_layer}
                onChange={v => set("security_layer", v)} options={SECURITY_LAYERS} />
              <div className="grid grid-cols-2 gap-3">
                <SelectField label="ALPN" value={form.alpn} onChange={v => set("alpn", v)} options={ALPN_OPTIONS} />
                <SelectField label="Отпечаток" value={form.fingerprint}
                  onChange={v => set("fingerprint", v)} options={FINGERPRINT_OPTIONS} />
              </div>
              <Field label="Vless Route ID" name="vless_route_id" value={form.vless_route_id} onChange={set}
                type="number" placeholder="0" hint="1–65535, пусто/0 = выкл" />
              <Toggle label="Скрыть хост" checked={form.hide_host} onChange={() => set("hide_host", !form.hide_host)} />

              <GroupLabel>Исключить из типа подписки</GroupLabel>
              <div className="grid grid-cols-2 gap-2">
                {SUB_TYPES.map(t => (
                  <CheckboxRow key={t.value} label={t.label}
                    checked={form.exclude_sub_types.includes(t.value)}
                    onChange={() => set("exclude_sub_types",
                      form.exclude_sub_types.includes(t.value)
                        ? form.exclude_sub_types.filter(v => v !== t.value)
                        : [...form.exclude_sub_types, t.value])} />
                ))}
              </div>

              <GroupLabel>Xray Json &amp; Raw</GroupLabel>
              <Field label="Шаблон Xray JSON" name="xray_json_template" value={form.xray_json_template}
                onChange={set} placeholder="Шаблон не выбран" hint="Имя шаблона Xray JSON (вручную)" />
              <div className="grid grid-cols-2 gap-2">
                <JsonSubConfig label="xHTTP"      value={form.xhttp}      onChange={v => set("xhttp", v)}      onError={subError("xhttp")} />
                <JsonSubConfig label="Mux"        value={form.mux}        onChange={v => set("mux", v)}        onError={subError("mux")} />
                <JsonSubConfig label="SockOpt"    value={form.sockopt}    onChange={v => set("sockopt", v)}    onError={subError("sockopt")} />
                <JsonSubConfig label="Final Mask" value={form.final_mask} onChange={v => set("final_mask", v)} onError={subError("final_mask")} />
              </div>

              <GroupLabel>Прочие настройки</GroupLabel>
              <Field label="Server Description" name="server_description" value={form.server_description}
                onChange={set} placeholder="Описание" hint="макс 30" maxLength={30} />
              <Toggle label="Перемешать хост" checked={form.shuffle_host}
                onChange={() => set("shuffle_host", !form.shuffle_host)} />
              <Toggle label="Разрешить небезопасные" checked={form.allow_insecure}
                onChange={() => set("allow_insecure", !form.allow_insecure)} />

              <GroupLabel>Настройки для Mihomo</GroupLabel>
              <Toggle label="Включение x25519mlkem768" checked={form.x25519mlkem768}
                onChange={() => set("x25519mlkem768", !form.x25519mlkem768)} />
            </>
          )}

          {apiError && (
            <div className="mt-1 px-3 py-2 rounded-md border text-xs"
                 style={{ background: "var(--err-dim)", borderColor: "var(--err-line)", color: "var(--err)" }}>
              {apiError}
            </div>
          )}
        </div>

        <div className="shrink-0 flex justify-end gap-2 px-5 py-3.5" style={{ borderTop: "1px solid var(--line-soft)" }}>
          <button onClick={onClose} className="btn btn-soft">Отмена</button>
          <button onClick={handleSave} disabled={!canSave || saving} className="btn btn-primary">
            {saving ? <><Loader2 size={13} className="spin" /> Сохранение...</> : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────

export function Hosts() {
  const [hosts,   setHosts]   = useState<HostTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [modal,   setModal]   = useState<{ editing?: HostTemplate } | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetch("/api/hosts").then(r => r.json());
      setHosts(Array.isArray(data) ? data : []);
    } catch { /* keep previous list on transient failure */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const save = async (payload: Omit<HostTemplate, "id">, id?: string) => {
    const res = await fetch(id ? `/api/hosts/${id}` : "/api/hosts", {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || "Ошибка сервера");
    }
    setModal(null);
    load();
  };

  const remove = async (id: string) => {
    if (!window.confirm("Удалить хост?")) return;
    await fetch(`/api/hosts/${id}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="h1">Хосты</h1>
            <p className="sub">Локальный редактор шаблонов хостов Remnawave</p>
          </div>
          <button onClick={() => setModal({})} className="btn btn-primary">
            <Plus size={13} /> Новый хост
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={20} className="spin" style={{ color: "var(--t-faint)" }} />
          </div>
        ) : hosts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <Server size={32} className="mb-4" style={{ color: "var(--t-faint)" }} />
            <p className="text-sm" style={{ color: "var(--t-low)" }}>
              Нет шаблонов хостов — создайте первый.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {hosts.map(h => (
              <HostCard key={h.id} host={h}
                onEdit={() => setModal({ editing: h })}
                onDelete={() => remove(h.id!)} />
            ))}
          </div>
        )}
      </div>

      {modal !== null && (
        <HostEditorModal
          initial={modal.editing}
          onClose={() => setModal(null)}
          onSave={payload => save(payload, modal.editing?.id)}
        />
      )}
    </div>
  );
}
