import { useState, useEffect } from "react";
import { Save, CheckCircle2, XCircle, Loader2, Wifi, Check, Sun, Moon, Monitor } from "lucide-react";
import { MultiSelect, type SelectOption } from "./MultiSelect";
import {
  ACCENTS, THEME_MODES, SKINS, type AccentKey, type Density, type ThemeMode, type AppSkin,
  applyAccent, applyDensity, applyThemeMode, applySkin,
  loadAccent, loadDensity, loadThemeMode, loadSkin,
  saveAccent, saveDensity, saveThemeMode, saveSkin,
} from "../theme/tweaks";
import { getActiveId } from "../auth/store";
import { CheckerControls } from "./monitoring/CheckerControls";
import { CheckerRegistry } from "./monitoring/CheckerRegistry";
import { TestServers } from "./settings/TestServers";

// ── Types ─────────────────────────────────────────────────────

interface RemnavaveConfig {
  panel_url: string;
  api_token: string;
  default_internal_squad_ids: string[];
  default_external_squad_ids: string[];
}

interface DeployDefaults {
  ssh_user: string;
  email: string;
  cloudflare_api_key: string;
  current_ssh_port: number;
  new_ssh_port: number;
  open_ports: string;
  change_ssh_port: boolean;
  remnanode_port: number;
  xhttp_path: string;
  whitelist_ips: string;
  // HAProxy relay defaults
  haproxy_source_port: number;
  haproxy_dest_port: number;
  haproxy_maxconn: number;
  haproxy_log: string;
  haproxy_mode: string;
  haproxy_timeout_connect: string;
  haproxy_timeout_client: string;
  haproxy_timeout_server: string;
  haproxy_timeout_tunnel: string;
}

const REMNAWAVE_INIT: RemnavaveConfig = {
  panel_url: "",
  api_token: "",
  default_internal_squad_ids: [],
  default_external_squad_ids: [],
};

const DEFAULTS_INIT: DeployDefaults = {
  ssh_user: "root",
  email: "",
  cloudflare_api_key: "",
  current_ssh_port: 22,
  new_ssh_port: 2222,
  open_ports: "80,443,8443",
  change_ssh_port: true,
  remnanode_port: 2222,
  xhttp_path: "",
  whitelist_ips: "",
  haproxy_source_port: 443,
  haproxy_dest_port: 443,
  haproxy_maxconn: 200000,
  haproxy_log: "global",
  haproxy_mode: "tcp",
  haproxy_timeout_connect: "5s",
  haproxy_timeout_client: "50s",
  haproxy_timeout_server: "50s",
  haproxy_timeout_tunnel: "1h",
};

// ── Reusable field components ─────────────────────────────────

function SettingField({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className="input"
      />
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

// ── Remnawave sub-tab ─────────────────────────────────────────

function RemnavaveTab() {
  const [cfg,      setCfg]      = useState<RemnavaveConfig>(REMNAWAVE_INIT);
  const [squads,   setSquads]   = useState<SelectOption[]>([]);
  const [extSquads,setExtSquads]= useState<SelectOption[]>([]);
  const [sqLoading,setSqLoading]= useState(false);
  const [saving,   setSaving]   = useState(false);
  const [checking, setChecking] = useState(false);
  const [saved,    setSaved]    = useState(false);
  const [checkResult, setCheckResult] = useState<
    { ok: boolean; msg: string } | null
  >(null);

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(d => {
        const r = d.remnawave ?? {};
        setCfg({
          panel_url:                  r.panel_url ?? "",
          api_token:                  r.api_token ?? "",
          default_internal_squad_ids: r.default_internal_squad_ids ?? [],
          default_external_squad_ids: r.default_external_squad_ids ?? [],
        });
        // Load squads on mount if Remnawave is configured
        if (r.panel_url && r.api_token) {
          loadSquads();
        }
      })
      .catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSquads = async () => {
    setSqLoading(true);
    try {
      const [int, ext] = await Promise.all([
        fetch("/api/remnawave/squads/internal").then(r => r.json()),
        fetch("/api/remnawave/squads/external").then(r => r.json()).catch(() => []),
      ]);
      const toOptions = (arr: unknown[]) =>
        (Array.isArray(arr) ? arr : []).map((s: unknown) => {
          const squad = s as { uuid: string; name: string };
          return { value: squad.uuid, label: squad.name };
        });
      setSquads(toOptions(int));
      setExtSquads(toOptions(ext));
    } catch {}
    setSqLoading(false);
  };

  const checkConnection = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      const res = await fetch("/api/settings/remnawave/check", { method: "POST" });
      if (res.ok) {
        setCheckResult({ ok: true, msg: "Соединение установлено" });
        await loadSquads();
      } else {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        setCheckResult({ ok: false, msg: String(err.detail ?? "Ошибка") });
      }
    } catch (e) {
      setCheckResult({ ok: false, msg: String(e) });
    }
    setChecking(false);
  };

  const save = async () => {
    setSaving(true);
    try {
      await fetch("/api/settings/remnawave", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {}
    setSaving(false);
  };

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <SettingField
        label="URL панели Remnawave"
        value={cfg.panel_url}
        onChange={v => setCfg(c => ({ ...c, panel_url: v }))}
        placeholder="https://panel.example.com"
      />
      <SettingField
        label="API токен"
        value={cfg.api_token}
        onChange={v => setCfg(c => ({ ...c, api_token: v }))}
        type="password"
        placeholder="Bearer token"
        hint="Не хранится в открытом виде — только на сервере"
      />

      {/* Check connection */}
      <div className="flex items-center gap-3">
        <button
          onClick={checkConnection}
          disabled={checking || !cfg.panel_url || !cfg.api_token}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium
                     bg-[var(--bg3)] text-[var(--t-mid)] border border-[var(--line)]
                     transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {checking ? <Loader2 size={12} className="animate-spin" /> : <Wifi size={12} />}
          Проверить соединение
        </button>
        {checkResult && (
          <span
            className="flex items-center gap-1.5 text-xs"
            style={{ color: checkResult.ok ? "var(--ok)" : "var(--err)" }}
          >
            {checkResult.ok
              ? <CheckCircle2 size={12} />
              : <XCircle size={12} />
            }
            {checkResult.msg}
          </span>
        )}
      </div>

      {/* Squad multi-selectors — always visible */}
      <MultiSelect
        label="Сквады по умолчанию (внутренние)"
        selected={cfg.default_internal_squad_ids}
        onChange={v => setCfg(c => ({ ...c, default_internal_squad_ids: v }))}
        options={squads}
        loading={sqLoading}
        placeholder="— без сквадов —"
      />
      <MultiSelect
        label="Сквады по умолчанию (внешние)"
        selected={cfg.default_external_squad_ids}
        onChange={v => setCfg(c => ({ ...c, default_external_squad_ids: v }))}
        options={extSquads}
        loading={sqLoading}
        placeholder="— без сквадов —"
      />

      <button
        onClick={save}
        disabled={saving}
        className="self-start flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium
                   bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors
                   disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saved
          ? <><CheckCircle2 size={14} /> Сохранено</>
          : saving
          ? <><Loader2 size={14} className="animate-spin" /> Сохранение...</>
          : <><Save size={14} /> Сохранить</>
        }
      </button>
    </div>
  );
}

// ── Deploy defaults sub-tab ───────────────────────────────────

function DeployDefaultsTab() {
  const [cfg,    setCfg]    = useState<DeployDefaults>(DEFAULTS_INIT);
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(d => {
        if (d.deploy_defaults) setCfg(d.deploy_defaults);
      })
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await fetch("/api/settings/deploy-defaults", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {}
    setSaving(false);
  };

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <SettingField
        label="SSH пользователь"
        value={cfg.ssh_user}
        onChange={v => setCfg(c => ({ ...c, ssh_user: v }))}
        placeholder="root"
      />
      <SettingField
        label="Email (Let's Encrypt)"
        value={cfg.email}
        onChange={v => setCfg(c => ({ ...c, email: v }))}
        type="email"
        placeholder="you@example.com"
      />
      <SettingField
        label="Cloudflare API токен (по умолчанию)"
        value={cfg.cloudflare_api_key}
        onChange={v => setCfg(c => ({ ...c, cloudflare_api_key: v }))}
        type="password"
        placeholder="DNS:Edit token"
        hint="Будет подставлен в форму деплоя автоматически"
      />
      <div className="grid grid-cols-2 gap-4">
        <SettingField
          label="Текущий SSH порт"
          value={String(cfg.current_ssh_port)}
          onChange={v => setCfg(c => ({ ...c, current_ssh_port: parseInt(v) || 22 }))}
          placeholder="22"
        />
        <SettingField
          label="Новый SSH порт"
          value={String(cfg.new_ssh_port)}
          onChange={v => setCfg(c => ({ ...c, new_ssh_port: parseInt(v) || 2222 }))}
          placeholder="2222"
        />
      </div>
      <SettingField
        label="Порты UFW (по умолчанию)"
        value={cfg.open_ports}
        onChange={v => setCfg(c => ({ ...c, open_ports: v }))}
        placeholder="80,443,8443"
        hint="Через запятую — будут открыты при деплое"
      />
      <SettingField
        label="Whitelist IP / CIDR (по умолчанию)"
        value={cfg.whitelist_ips}
        onChange={v => setCfg(c => ({ ...c, whitelist_ips: v }))}
        placeholder="1.2.3.4, 10.0.0.0/24"
        hint="Префилл поля whitelist в форме деплоя (fail2ban/UFW)"
      />
      <OptCheckbox
        label="Сменять порт SSH по умолчанию"
        checked={cfg.change_ssh_port}
        onChange={v => setCfg(c => ({ ...c, change_ssh_port: v }))}
        hint="Если выключено, порт SSH останется прежним; поле «Новый порт SSH» в форме деплоя будет неактивно"
      />
      <div className="grid grid-cols-2 gap-4">
        <SettingField
          label="Порт remnanode по умолчанию"
          value={String(cfg.remnanode_port)}
          onChange={v => setCfg(c => ({ ...c, remnanode_port: parseInt(v) || 2222 }))}
          placeholder="2222"
        />
        <SettingField
          label="Путь XHTTP по умолчанию"
          value={cfg.xhttp_path}
          onChange={v => setCfg(c => ({ ...c, xhttp_path: v }))}
          placeholder="/xray/"
          hint="Опционально — оставьте пустым, если не используется"
        />
      </div>

      {/* ── HAProxy relay defaults ── */}
      <p className="text-[11px] font-semibold text-[var(--t-faint)] uppercase tracking-widest mt-2">
        HAProxy (реле) — значения по умолчанию
      </p>
      <div className="grid grid-cols-2 gap-4">
        <SettingField label="Порт HAProxy" value={String(cfg.haproxy_source_port)}
          onChange={v => setCfg(c => ({ ...c, haproxy_source_port: parseInt(v) || 443 }))} placeholder="443" />
        <SettingField label="Целевой порт" value={String(cfg.haproxy_dest_port)}
          onChange={v => setCfg(c => ({ ...c, haproxy_dest_port: parseInt(v) || 443 }))} placeholder="443" />
        <SettingField label="Лимит подключений" value={String(cfg.haproxy_maxconn)}
          onChange={v => setCfg(c => ({ ...c, haproxy_maxconn: parseInt(v) || 200000 }))} placeholder="200000" />
        <SettingField label="Тип лога" value={cfg.haproxy_log}
          onChange={v => setCfg(c => ({ ...c, haproxy_log: v }))} placeholder="global" />
        <SettingField label="Режим" value={cfg.haproxy_mode}
          onChange={v => setCfg(c => ({ ...c, haproxy_mode: v }))} placeholder="tcp" />
        <SettingField label="Timeout подключения" value={cfg.haproxy_timeout_connect}
          onChange={v => setCfg(c => ({ ...c, haproxy_timeout_connect: v }))} placeholder="5s" />
        <SettingField label="Timeout клиента" value={cfg.haproxy_timeout_client}
          onChange={v => setCfg(c => ({ ...c, haproxy_timeout_client: v }))} placeholder="50s" />
        <SettingField label="Timeout сервера" value={cfg.haproxy_timeout_server}
          onChange={v => setCfg(c => ({ ...c, haproxy_timeout_server: v }))} placeholder="50s" />
        <SettingField label="Timeout туннеля" value={cfg.haproxy_timeout_tunnel}
          onChange={v => setCfg(c => ({ ...c, haproxy_timeout_tunnel: v }))} placeholder="1h" />
      </div>

      <button
        onClick={save}
        disabled={saving}
        className="self-start flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium
                   bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors
                   disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {saved
          ? <><CheckCircle2 size={14} /> Сохранено</>
          : saving
          ? <><Loader2 size={14} className="animate-spin" /> Сохранение...</>
          : <><Save size={14} /> Сохранить</>
        }
      </button>
    </div>
  );
}

// ── Optimization sub-tab ─────────────────────────────────────

interface OptimizationConfig {
  network_tuning: boolean;
  bbr: boolean;
  system_limits: boolean;
  dns: boolean;
  dns_servers: string;
}

const OPT_INIT: OptimizationConfig = {
  network_tuning: true,
  bbr: true,
  system_limits: true,
  dns: true,
  dns_servers: "1.1.1.1,8.8.8.8",
};

function OptCheckbox({ label, checked, onChange, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <label className="flex items-start gap-3 cursor-pointer select-none group">
      <input
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="mt-0.5 w-4 h-4 rounded border-[var(--line)] bg-[var(--bg3)] accent-[var(--accent)]"
      />
      <div>
        <span className="text-sm text-[var(--t-mid)] group-hover:text-[var(--t-hi)] transition-colors">
          {label}
        </span>
        {hint && <p className="text-[11px] text-[var(--t-faint)] mt-0.5">{hint}</p>}
      </div>
    </label>
  );
}

function OptimizationTab() {
  const [cfg,    setCfg]    = useState<OptimizationConfig>(OPT_INIT);
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);

  useEffect(() => {
    fetch("/api/settings")
      .then(r => r.json())
      .then(d => { if (d.optimization) setCfg(d.optimization); })
      .catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await fetch("/api/settings/optimization", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {}
    setSaving(false);
  };

  return (
    <div className="flex flex-col gap-5 max-w-lg">
      <p className="text-xs text-[var(--t-low)]">
        Настройки node-accelerator применяются при деплое до всех остальных шагов.
        Форма деплоя наследует эти значения, но позволяет переопределить их для конкретной ноды.
      </p>

      <OptCheckbox
        label="Сетевая оптимизация TCP/UDP"
        checked={cfg.network_tuning}
        onChange={v => setCfg(c => ({ ...c, network_tuning: v }))}
        hint="Увеличение буферов сокетов, somaxconn, TIME_WAIT reuse"
      />
      <OptCheckbox
        label="Алгоритм контроля перегрузки Google BBR"
        checked={cfg.bbr}
        onChange={v => setCfg(c => ({ ...c, bbr: v }))}
        hint="net.core.default_qdisc=fq + net.ipv4.tcp_congestion_control=bbr"
      />
      <OptCheckbox
        label="Системные лимиты (fd/nofile)"
        checked={cfg.system_limits}
        onChange={v => setCfg(c => ({ ...c, system_limits: v }))}
        hint="nofile=1 000 000 в /etc/security/limits.conf и systemd/system.conf"
      />
      <OptCheckbox
        label="Быстрые DNS-резолверы"
        checked={cfg.dns}
        onChange={v => setCfg(c => ({ ...c, dns: v }))}
        hint="Принудительная замена серверов в /etc/resolv.conf"
      />

      {cfg.dns && (
        <SettingField
          label="DNS-серверы (через запятую)"
          value={cfg.dns_servers}
          onChange={v => setCfg(c => ({ ...c, dns_servers: v }))}
          placeholder="1.1.1.1,8.8.8.8"
          hint="Первые 3 значения будут записаны как nameserver"
        />
      )}

      <button onClick={save} disabled={saving}
        className="self-start flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium
                   bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] transition-colors
                   disabled:opacity-50 disabled:cursor-not-allowed">
        {saved
          ? <><CheckCircle2 size={14} /> Сохранено</>
          : saving
          ? <><Loader2 size={14} className="animate-spin" /> Сохранение...</>
          : <><Save size={14} /> Сохранить</>
        }
      </button>
    </div>
  );
}


// ── Theme tab ─────────────────────────────────────────────────
// Mode is per-account (keyed by the active account); accent + density are
// device-global. Controls apply + persist imperatively — App re-reads the
// persisted values on mount / account switch (see App.tsx).

const MODE_ICON: Record<ThemeMode, typeof Sun> = { system: Monitor, light: Sun, dark: Moon };

export function ThemeTab() {
  const accountId = getActiveId();
  const [skin, setSkin]       = useState<AppSkin>(() => loadSkin(accountId));
  const [mode, setMode]       = useState<ThemeMode>(() => loadThemeMode(accountId));
  const [accent, setAccent]   = useState<AccentKey>(loadAccent);
  const [density, setDensity] = useState<Density>(loadDensity);

  const pickSkin = (s: AppSkin) => { setSkin(s); applySkin(s); saveSkin(accountId, s); };
  const pickMode = (m: ThemeMode) => { setMode(m); applyThemeMode(m); saveThemeMode(accountId, m); };
  const pickAccent = (a: AccentKey) => { setAccent(a); applyAccent(a); saveAccent(a); };
  const pickDensity = (d: Density) => { setDensity(d); applyDensity(d); saveDensity(d); };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 26, maxWidth: 460 }}>
      <div>
        <p className="micro" style={{ marginBottom: 10 }}>Стиль</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
          {SKINS.map(s => {
            const on = skin === s.key;
            return (
              <button key={s.key} onClick={() => pickSkin(s.key)} className="card"
                style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 5,
                  padding: "16px 8px", cursor: "pointer", textAlign: "center",
                  borderColor: on ? "var(--accent-line)" : "var(--line-soft)",
                  background: on ? "var(--accent-dim)" : "var(--bg2)",
                  color: on ? "var(--accent-hi)" : "var(--t-mid)",
                }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>{s.label}</span>
                <span style={{ fontSize: 11, color: "var(--t-low)" }}>
                  {s.key === "apple" ? "Системный вид macOS/iOS" : "Моноширинный, консольный"}
                </span>
              </button>
            );
          })}
        </div>
        <p className="hint">Apple — по умолчанию. «Консоль» возвращает моноширинный вид JetBrains Mono.</p>
      </div>

      <div>
        <p className="micro" style={{ marginBottom: 10 }}>Режим</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 8 }}>
          {THEME_MODES.map(m => {
            const Icon = MODE_ICON[m.key];
            const on = mode === m.key;
            return (
              <button key={m.key} onClick={() => pickMode(m.key)}
                className="card"
                style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 7,
                  padding: "16px 8px", cursor: "pointer",
                  borderColor: on ? "var(--accent-line)" : "var(--line-soft)",
                  background: on ? "var(--accent-dim)" : "var(--bg2)",
                  color: on ? "var(--accent-hi)" : "var(--t-mid)",
                }}>
                <Icon size={20} />
                <span style={{ fontSize: 12.5, fontWeight: 600 }}>{m.label}</span>
              </button>
            );
          })}
        </div>
        <p className="hint">«Системная» следует настройке светлой/тёмной темы вашей ОС и переключается на лету.</p>
      </div>

      <div>
        <p className="micro" style={{ marginBottom: 10 }}>Акцентный цвет</p>
        <div style={{ display: "flex", gap: 10 }}>
          {(Object.keys(ACCENTS) as AccentKey[]).map(k => (
            <button key={k} onClick={() => pickAccent(k)} title={k}
              style={{
                width: 30, height: 30, borderRadius: 8, background: ACCENTS[k].base, cursor: "pointer",
                border: accent === k ? "2px solid var(--t-hi)" : "2px solid transparent",
                display: "grid", placeItems: "center",
              }}>
              {accent === k && <Check size={15} color={ACCENTS[k].ink} strokeWidth={3} />}
            </button>
          ))}
        </div>
      </div>

      <div>
        <p className="micro" style={{ marginBottom: 10 }}>Плотность</p>
        <div className="seg" style={{ maxWidth: 260 }}>
          <button className={density === "comfortable" ? "on" : ""} onClick={() => pickDensity("comfortable")}>Обычная</button>
          <button className={density === "compact" ? "on" : ""} onClick={() => pickDensity("compact")}>Плотная</button>
        </div>
      </div>
    </div>
  );
}


// ── Monitoring tab (Ф2) ───────────────────────────────────────

function MonitoringTab() {
  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <CheckerControls />
      <CheckerRegistry />
    </div>
  );
}


// ── Test servers tab (Ф1, wave1) ──────────────────────────────

// Default metric level for the «Тесты скорости» run form (device-global,
// localStorage `ni_speedtest_metrics`). Prefills SpeedTests.tsx on mount.
const SPEEDTEST_METRICS_KEY = "ni_speedtest_metrics";
const SPEEDTEST_METRIC_LEVELS = [
  { level: 1, label: "Скорость" },
  { level: 2, label: "+пинг/джиттер" },
  { level: 3, label: "+трассировка" },
];

function SpeedtestDefaults() {
  const [level, setLevel] = useState(() => {
    const v = parseInt(localStorage.getItem(SPEEDTEST_METRICS_KEY) || "1", 10);
    return v >= 1 && v <= 3 ? v : 1;
  });
  const pick = (l: number) => {
    setLevel(l);
    try { localStorage.setItem(SPEEDTEST_METRICS_KEY, String(l)); } catch {}
  };
  return (
    <div className="card card-p">
      <span className="micro flex items-center gap-2 mb-1">Метрики по умолчанию</span>
      <p className="hint mb-3">Набор метрик, предвыбранный в разделе «Тесты скорости» (кумулятивно).</p>
      <div className="flex items-center gap-1">
        {SPEEDTEST_METRIC_LEVELS.map(m => (
          <button key={m.level} type="button" onClick={() => pick(m.level)}
            className={`px-2 py-1 rounded border text-[11px] transition-colors ${
              level === m.level
                ? "bg-[var(--accent-dim)] border-[var(--accent-line)] text-[var(--accent-hi)]"
                : "bg-[var(--bg2)] border-[var(--line)] text-[var(--t-low)] hover:bg-[var(--bg3)]"
            }`}>
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function TestServersTab() {
  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <TestServers />
      <SpeedtestDefaults />
    </div>
  );
}


// ── Main Settings page ────────────────────────────────────────

type SubTab = "remnawave" | "defaults" | "optimization" | "monitoring" | "testservers" | "theme";

export function Settings() {
  const [sub, setSub] = useState<SubTab>("remnawave");

  const tabs: { id: SubTab; label: string }[] = [
    { id: "remnawave",   label: "Remnawave" },
    { id: "defaults",    label: "Деплой (умолчания)" },
    { id: "optimization", label: "Оптимизация ОС" },
    { id: "monitoring",  label: "Мониторинг" },
    { id: "testservers", label: "Сервера для тестирования" },
    { id: "theme",       label: "Тема" },
  ];

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-6">

        <div className="mb-6">
          <h1 className="h1">Настройки</h1>
          <p className="sub">Параметры подключения и значения по умолчанию</p>
        </div>

        <div className="seg" style={{ width: "fit-content", marginBottom: 24 }}>
          {tabs.map(t => (
            <button key={t.id} className={sub === t.id ? "on" : ""} onClick={() => setSub(t.id)}>
              {t.label}
            </button>
          ))}
        </div>

        {sub === "remnawave"    && <RemnavaveTab />}
        {sub === "defaults"     && <DeployDefaultsTab />}
        {sub === "optimization" && <OptimizationTab />}
        {sub === "monitoring"   && <MonitoringTab />}
        {sub === "testservers"  && <TestServersTab />}
        {sub === "theme"        && <ThemeTab />}
      </div>
    </div>
  );
}
