import { useCallback, useEffect, useState } from "react";
import { Activity, CheckCircle2, Loader2, Save } from "lucide-react";
import { toast } from "../infra/Toast";

/**
 * «Latency Lab» — подключение внешнего сервиса замера доступности подсетей.
 * Ключ хранится на backend и НИКОГДА не возвращается наружу: GET отдаёт только
 * `has_key`, поэтому поле ключа всегда пустое, а пустое значение при сохранении
 * означает «не менять».
 */

interface LatencyConfig {
  enabled: boolean;
  has_key: boolean;
  base_url: string;
  node_id: string;
  default_operator: string;
  scan_limit: number;
  scan_window_hours: number;
  scan_count: number;
  reset_at: string;
  reset_in_seconds: number;
}

const CFG_INIT: LatencyConfig = {
  enabled: false,
  has_key: false,
  base_url: "",
  node_id: "orel",
  default_operator: "",
  scan_limit: 0,
  scan_window_hours: 24,
  scan_count: 0,
  reset_at: "",
  reset_in_seconds: 0,
};

function Fld({ label, value, onChange, type = "text", placeholder, hint, min }: {
  label: string; value: string; onChange: (v: string) => void;
  type?: string; placeholder?: string; hint?: string; min?: number;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="label">{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)}
        placeholder={placeholder} min={min} autoComplete="off" spellCheck={false} className="input" />
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

function fmtReset(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return m > 0 ? `${h} ч ${m} мин` : `${h} ч`;
  if (m > 0) return r > 0 ? `${m} мин ${r} с` : `${m} мин`;
  return `${r} с`;
}

export function LatencyTab() {
  const [cfg, setCfg] = useState<LatencyConfig>(CFG_INIT);
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checking, setChecking] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/latency/config");
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`);
      setCfg({
        enabled: !!d.enabled,
        has_key: !!d.has_key,
        base_url: d.base_url ?? "",
        node_id: d.node_id ?? "orel",
        default_operator: d.default_operator ?? "",
        scan_limit: typeof d.scan_limit === "number" ? d.scan_limit : 0,
        scan_window_hours: typeof d.scan_window_hours === "number" ? d.scan_window_hours : 24,
        scan_count: typeof d.scan_count === "number" ? d.scan_count : 0,
        reset_at: typeof d.reset_at === "string" ? d.reset_at : "",
        reset_in_seconds: typeof d.reset_in_seconds === "number" ? d.reset_in_seconds : 0,
      });
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch("/api/latency/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: cfg.enabled,
          base_url: cfg.base_url.trim(),
          node_id: cfg.node_id.trim(),
          default_operator: cfg.default_operator.trim(),
          scan_limit: Number(cfg.scan_limit) || 0,
          scan_window_hours: Number(cfg.scan_window_hours) || 24,
          ...(apiKey ? { api_key: apiKey } : {}),
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${res.status}`);
      toast("Настройки Latency Lab сохранены", "success");
      setApiKey("");
      await load();
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  };

  const check = async () => {
    setChecking(true);
    try {
      const res = await fetch("/api/latency/check", { method: "POST" });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || d.ok === false) {
        toast(typeof (d.error ?? d.detail) === "string" ? (d.error ?? d.detail) : `HTTP ${res.status}`, "error");
      } else {
        toast("Ключ принят — сервис отвечает", "success");
      }
    } catch (e) { toast((e as Error).message, "error"); }
    setChecking(false);
  };

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="flex items-center gap-2">
        <Activity size={15} className="text-[var(--accent)]" />
        <h3 className="text-sm font-semibold text-[var(--t-hi)]">Latency Lab</h3>
      </div>
      <p className="text-xs text-[var(--t-low)]">
        Внешний сервис замера доступности подсетей у мобильных операторов.
        Используется кнопкой «Скан Latency» в разделе «Подсети».
      </p>

      {loading ? (
        <div className="py-8 text-center text-[var(--t-faint)]"><Loader2 size={16} className="animate-spin inline" /></div>
      ) : (
        <div className="card card-p flex flex-col gap-3">
          <label className="flex items-center gap-2.5 cursor-pointer select-none">
            <button type="button" role="switch" aria-checked={cfg.enabled}
              data-testid="latency-enabled"
              onClick={() => setCfg(c => ({ ...c, enabled: !c.enabled }))}
              className={`switch ${cfg.enabled ? "on" : ""}`} />
            <span className="text-sm" style={{ color: "var(--t-low)" }}>Включить интеграцию</span>
          </label>

          <Fld label={cfg.has_key ? "API-ключ (сохранён)" : "API-ключ"}
            value={apiKey} onChange={setApiKey} type="password" placeholder="ll_..."
            hint={cfg.has_key
              ? "Ключ сохранён на сервере. Оставьте пустым, чтобы не менять."
              : "Ключ ещё не сохранён — интеграция работать не будет."} />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            <Fld label="Base URL" value={cfg.base_url}
              onChange={v => setCfg(c => ({ ...c, base_url: v }))}
              placeholder="https://latency.example.com" hint="Необязательно — по умолчанию адрес сервиса." />
            <Fld label="Node ID" value={cfg.node_id}
              onChange={v => setCfg(c => ({ ...c, node_id: v }))} placeholder="orel" />
          </div>

          <Fld label="Оператор по умолчанию" value={cfg.default_operator}
            onChange={v => setCfg(c => ({ ...c, default_operator: v }))}
            placeholder="mts" hint="Пусто — сканировать всеми доступными операторами." />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
            <Fld label="Лимит сканов (0 — без лимита)" value={String(cfg.scan_limit)}
              type="number" min={0}
              onChange={v => setCfg(c => ({ ...c, scan_limit: parseInt(v, 10) || 0 }))} />
            <Fld label="За N часов" value={String(cfg.scan_window_hours)}
              type="number" min={1}
              onChange={v => setCfg(c => ({ ...c, scan_window_hours: parseInt(v, 10) || 24 }))} />
          </div>
          {cfg.scan_limit > 0 && (
            <p className="hint">
              Использовано: {cfg.scan_count} из {cfg.scan_limit} за {cfg.scan_window_hours} ч
              {cfg.reset_in_seconds > 0 && (
                <> — Сброс лимита: через {fmtReset(cfg.reset_in_seconds)}</>
              )}
            </p>
          )}

          <div className="flex items-center gap-2">
            <button onClick={save} disabled={saving} className="btn btn-primary">
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />} Сохранить
            </button>
            <button onClick={check} disabled={checking || !cfg.has_key} className="btn btn-soft">
              {checking ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />} Проверить
            </button>
            <span className={`chip ${cfg.has_key ? "ok" : "neutral"}`} style={{ fontSize: 10 }}>
              {cfg.has_key ? "ключ сохранён" : "ключ не задан"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
