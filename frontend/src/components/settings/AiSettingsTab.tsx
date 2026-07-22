import { useEffect, useState } from "react";
import { Loader2, Bot } from "lucide-react";
import { toast } from "../infra/Toast";
import { PromptPresets } from "./PromptPresets";

interface AiConfig {
  enabled: boolean;
  provider: "openai" | "anthropic";
  base_url: string;
  model: string;
  max_steps: number;
  readonly: boolean;
  has_key: boolean;
  gateway: "none" | "cliproxy";
  active_preset_id: string;
}

const DEFAULTS: Record<AiConfig["provider"], { base_url: string; model: string }> = {
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  anthropic: { base_url: "https://api.anthropic.com/v1", model: "claude-haiku-4-5-20251001" },
};

/** Настройки ИИ-агента. Вынесены из страницы чата (Волна 6, План C Ф1): чат
 *  остался только чатом, а вся конфигурация — здесь.
 *
 *  POST отправляет ПОЛНЫЙ объект: ручка `/api/ai/config` делает full-replace,
 *  и частичное тело сбросило бы base_url/model/max_steps в дефолты pydantic. */
export function AiSettingsTab() {
  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [loadErr, setLoadErr] = useState(false);

  useEffect(() => {
    fetch("/api/ai/config")
      .then(r => { if (!r.ok) throw new Error("bad"); return r.json(); })
      .then(setCfg)
      .catch(() => setLoadErr(true));
  }, []);

  // Каталог моделей. Бэкенд решает сам (Волна 6, План C Ф2: гейт по gateway
  // снят), пустой список = «вводите вручную».
  const loadModels = () =>
    fetch("/api/ai/models")
      .then(r => (r.ok ? r.json() : { models: [] }))
      .then(d => setModels(d.models || []))
      .catch(() => setModels([]));

  // Ключи эффекта — то, от чего реально зависит ответ сервера. `has_key` тут
  // обязателен: без ключа сервер отдаёт [], и после первого сохранения ключа
  // список должен появиться сам.
  useEffect(() => { if (cfg) loadModels(); }, [cfg?.base_url, cfg?.provider, cfg?.has_key]);

  const patchCfg = (p: Partial<AiConfig>) => setCfg(c => (c ? { ...c, ...p } : c));

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      const body: any = { ...cfg };
      if (keyInput.trim()) body.api_key = keyInput.trim();
      const r = await fetch("/api/ai/config", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error("save failed");
      setCfg(data);
      setKeyInput("");
      // Явный рефетч: если ключи эффекта не изменились (например сохранили тот
      // же base_url), сам он не перезапустится, и каталог остался бы прежним.
      await loadModels();
      toast("Настройки ИИ сохранены", "success");
    } catch { toast("Не удалось сохранить настройки ИИ", "error"); }
    finally { setSaving(false); }
  };

  if (loadErr) return <p className="text-sm text-[var(--err)]">Не удалось загрузить конфигурацию ИИ.</p>;
  if (!cfg) return null;

  return (
    <>
      <div className="card card-p flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Bot size={16} className="text-[var(--accent-hi)]" />
          <span className="text-sm font-semibold text-[var(--t-hi)]">Встроенный ИИ-агент</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1">
            <span className="micro">Шлюз</span>
            <select className="selectbox" value={cfg.gateway} disabled={saving}
              onChange={e => patchCfg({ gateway: e.target.value as AiConfig["gateway"] })}>
              <option value="none">Прямой провайдер</option>
              <option value="cliproxy">CLIProxyAPI (шлюз)</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="micro">Формат протокола</span>
            <select className="selectbox" value={cfg.provider} disabled={saving}
              onChange={e => {
                const p = e.target.value as AiConfig["provider"];
                // Keep base_url when routing via a gateway (points at CLIProxyAPI, not the provider).
                patchCfg(cfg.gateway === "cliproxy" ? { provider: p } : { provider: p, ...DEFAULTS[p] });
              }}>
              <option value="openai">OpenAI-совместимый</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="micro flex items-center gap-2">
              Модель{models.length === 0 && " (список пуст — введите вручную)"}
              <button type="button" onClick={loadModels} disabled={saving}
                className="normal-case tracking-normal font-normal text-[var(--accent-hi)] hover:underline">
                Обновить список
              </button>
            </span>
            {models.length > 0 ? (
              <select className="selectbox" value={cfg.model} disabled={saving}
                onChange={e => patchCfg({ model: e.target.value })}>
                {!models.includes(cfg.model) && <option value={cfg.model}>{cfg.model}</option>}
                {models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input className="input" value={cfg.model} disabled={saving}
                onChange={e => patchCfg({ model: e.target.value })} />
            )}
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="micro">Base URL</span>
            <input className="input font-mono text-xs" value={cfg.base_url} disabled={saving}
              onChange={e => patchCfg({ base_url: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="micro">API-ключ {cfg.has_key && <span className="text-[var(--ok)]">(сохранён)</span>}</span>
            <input className="input" type="password" autoComplete="off" value={keyInput} disabled={saving}
              placeholder={cfg.has_key ? "•••• (оставьте пустым, чтобы не менять)" : "sk-..."}
              onChange={e => setKeyInput(e.target.value)} />
          </label>
        </div>

        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer">
            <button type="button" role="switch" aria-checked={cfg.enabled} disabled={saving}
              onClick={() => patchCfg({ enabled: !cfg.enabled })}
              className={`relative w-9 h-5 rounded-full transition-colors ${cfg.enabled ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${cfg.enabled ? "translate-x-4" : ""}`} />
            </button>
            Включить агента
          </label>
          <label className="flex flex-col gap-1 w-28">
            <span className="micro">Лимит шагов</span>
            <input type="number" min={1} max={20} className="input" value={cfg.max_steps} disabled={saving}
              onChange={e => patchCfg({ max_steps: Number(e.target.value) })} />
          </label>
          <button onClick={save} disabled={saving}
            className="ml-auto self-end flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
            {saving ? <Loader2 size={14} className="animate-spin" /> : null} Сохранить
          </button>
        </div>
      </div>

      <PromptPresets
        activeId={cfg.active_preset_id}
        onPickActive={id => patchCfg({ active_preset_id: id })}
      />
    </>
  );
}
