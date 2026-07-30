import { useEffect, useState } from "react";
import { Loader2, Bot, Globe, AlertTriangle } from "lucide-react";
import { toast } from "../infra/Toast";
import { PromptPresets } from "./PromptPresets";

type WebProvider = "duckduckgo" | "tavily" | "brave" | "searxng";

interface AiConfig {
  enabled: boolean;
  provider: "openai" | "anthropic";
  base_url: string;
  model: string;
  max_steps: number;
  /** Потолок ВЫВОДА за один тёрн. Не хватало 1024 — тело одной карточки
   *  хостинга обрывалось посреди JSON, и в чат приходил пустой пузырь. */
  max_tokens: number;
  readonly: boolean;
  has_key: boolean;
  gateway: "none" | "cliproxy";
  active_preset_id: string;
  web_enabled: boolean;
  web_provider: WebProvider;
  web_base_url: string;
  web_max_results: number;
  // Относится к СОХРАНЁННОМУ провайдеру, а не к выбранному в селекторе прямо
  // сейчас, — поэтому видимость поля ключа считается локально (см. needsWebKey).
  web_needs_key: boolean;
  has_web_key: boolean;
  /** Адрес выводится из провайдера, а не хранится. Закрывает класс ошибок
   *  «сменил провайдера — адрес остался прежним — 401 с верным ключом». */
  base_url_auto: boolean;
  /** Штатные адреса провайдеров ОТ СЕРВЕРА. Своей копии не держим: она отстанет,
   *  а цена рассинхрона — запрос с верным ключом по чужому адресу. */
  provider_defaults?: Record<string, string>;
}

/** Адрес выбранного провайдера. Источник — серверный каталог; литералы ниже
 *  нужны лишь на случай старого бэкенда, который поля ещё не отдаёт. */
function providerUrl(cfg: AiConfig, provider: AiConfig["provider"]): string {
  return cfg.provider_defaults?.[provider] ?? FALLBACK_URLS[provider];
}

const FALLBACK_URLS: Record<AiConfig["provider"], string> = {
  openai: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com/v1",
};

/** Модель по умолчанию — чтобы не перетирать ту, что человек выбрал сам. */
const DEFAULT_MODELS: Record<AiConfig["provider"], string> = {
  openai: "gpt-4o-mini",
  anthropic: "claude-haiku-4-5-20251001",
};

// Зеркало `ai_web.WEB_PROVIDERS` + `ai_web.needs_key`. Дублируем на клиенте
// осознанно: поле ключа должно появиться СРАЗУ при выборе провайдера, а серверный
// `web_needs_key` описывает уже сохранённую конфигурацию и до сохранения отстаёт —
// без этого ключ негде было бы ввести.
const WEB_PROVIDERS: { id: WebProvider; label: string; needsKey?: boolean }[] = [
  { id: "duckduckgo", label: "DuckDuckGo — без ключа" },
  { id: "tavily", label: "Tavily — нужен ключ", needsKey: true },
  { id: "brave", label: "Brave Search — нужен ключ", needsKey: true },
  { id: "searxng", label: "SearXNG — свой инстанс" },
];

/** Тумблер. Вынесен, потому что их здесь три штуки с одинаковой разметкой;
 *  белый кружок на цветной дорожке — намеренный хардкод (он белый в обеих темах,
 *  как в iOS), гейт `theme/contrast.test.ts` его пропускает. */
function Toggle({ checked, onChange, disabled, label }: {
  checked: boolean; onChange: () => void; disabled?: boolean; label: string;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-[var(--t-mid)] cursor-pointer select-none">
      {/* aria-label, а не только текст рядом: у тумблера нет собственного
          содержимого, и без явного имени он озвучивается как безымянный switch. */}
      <button type="button" role="switch" aria-checked={checked} aria-label={label}
        disabled={disabled} onClick={onChange}
        className={`relative shrink-0 w-9 h-5 rounded-full transition-colors ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${checked ? "translate-x-4" : ""}`} />
      </button>
      {label}
    </label>
  );
}

/** Настройки ИИ-агента. Вынесены из страницы чата (Волна 6, План C Ф1): чат
 *  остался только чатом, а вся конфигурация — здесь.
 *
 *  POST отправляет ПОЛНЫЙ объект: ручка `/api/ai/config` делает full-replace,
 *  и частичное тело сбросило бы base_url/model/max_steps/web_* в дефолты pydantic. */
export function AiSettingsTab() {
  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [webKeyInput, setWebKeyInput] = useState("");
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
      // Оба ключа write-only и симметричны: пустое поле НЕ уезжает на сервер,
      // иначе сохранение любой соседней настройки стирало бы ключ.
      if (keyInput.trim()) body.api_key = keyInput.trim();
      if (webKeyInput.trim()) body.web_api_key = webKeyInput.trim();
      const r = await fetch("/api/ai/config", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error("save failed");
      setCfg(data);
      setKeyInput("");
      setWebKeyInput("");
      // Явный рефетч: если ключи эффекта не изменились (например сохранили тот
      // же base_url), сам он не перезапустится, и каталог остался бы прежним.
      await loadModels();
      toast("Настройки ИИ сохранены", "success");
    } catch { toast("Не удалось сохранить настройки ИИ", "error"); }
    finally { setSaving(false); }
  };

  if (loadErr) return <p className="text-sm text-[var(--err)]">Не удалось загрузить конфигурацию ИИ.</p>;
  if (!cfg) return null;

  const needsWebKey = !!WEB_PROVIDERS.find(p => p.id === cfg.web_provider)?.needsKey;

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
                // В авторежиме адрес выводит сервер — здесь только показываем,
                // куда он поедет. Модель подставляем лишь когда текущая явно
                // «чужая»: перетереть выбранную модель при смене формата
                // протокола раздражает больше, чем помогает.
                const next: Partial<AiConfig> = { provider: p };
                if (cfg.base_url_auto) next.base_url = providerUrl(cfg, p);
                if (cfg.model === DEFAULT_MODELS[cfg.provider]) next.model = DEFAULT_MODELS[p];
                patchCfg(next);
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
            <span className="micro flex items-center gap-2">
              Base URL
              <label className="normal-case tracking-normal font-normal flex items-center gap-1">
                <input type="checkbox" checked={cfg.base_url_auto} disabled={saving}
                  onChange={e => {
                    const auto = e.target.checked;
                    patchCfg(auto ? { base_url_auto: true, base_url: providerUrl(cfg, cfg.provider) }
                                  : { base_url_auto: false });
                  }} />
                по провайдеру
              </label>
            </span>
            <input className="input font-mono text-xs" value={cfg.base_url}
              disabled={saving || cfg.base_url_auto}
              onChange={e => patchCfg({ base_url: e.target.value })} />
            <p className="hint">
              {cfg.gateway === "cliproxy"
                // Локальный шлюз подменяет адрес на стороне сервера, поэтому поле
                // здесь ни на что не влияет — сказать об этом честнее, чем дать
                // человеку править значение, которое не применится.
                ? "При работе через шлюз запрос уходит в контейнер CLIProxyAPI — этот адрес используется только если шлюз внешний."
                : cfg.base_url_auto
                  ? "Адрес подставляется по выбранному провайдеру. Снимите галочку для стороннего OpenAI-совместимого эндпоинта (OpenRouter, локальная модель)."
                  : "Ручной режим: убедитесь, что ключ выдан именно для этого адреса."}
            </p>
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="micro">API-ключ {cfg.has_key && <span className="text-[var(--ok)]">(сохранён)</span>}</span>
            <input className="input" type="password" autoComplete="off" value={keyInput} disabled={saving}
              placeholder={cfg.has_key ? "•••• (оставьте пустым, чтобы не менять)" : "sk-..."}
              onChange={e => setKeyInput(e.target.value)} />
          </label>
        </div>

        {/* ── Интернет ─────────────────────────────────────────── */}
        <div className="border-t border-[var(--line-soft)] pt-4 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Globe size={15} className="text-[var(--accent-hi)]" />
            <span className="text-sm font-semibold text-[var(--t-hi)]">Интернет</span>
          </div>

          <Toggle checked={cfg.web_enabled} disabled={saving} label="Разрешить поиск и чтение страниц"
            onChange={() => patchCfg({ web_enabled: !cfg.web_enabled })} />

          {cfg.web_enabled && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="flex flex-col gap-1">
                <span className="micro">Поисковик</span>
                <select className="selectbox" value={cfg.web_provider} disabled={saving}
                  onChange={e => patchCfg({ web_provider: e.target.value as WebProvider })}>
                  {WEB_PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="micro">Результатов на запрос</span>
                <input type="number" min={1} max={10} className="input" value={cfg.web_max_results} disabled={saving}
                  onChange={e => patchCfg({ web_max_results: Number(e.target.value) })} />
              </label>

              {needsWebKey && (
                <label className="flex flex-col gap-1 sm:col-span-2">
                  <span className="micro">
                    Ключ поисковика {cfg.has_web_key && <span className="text-[var(--ok)]">(сохранён)</span>}
                  </span>
                  <input className="input" type="password" autoComplete="off" value={webKeyInput} disabled={saving}
                    placeholder={cfg.has_web_key ? "•••• (оставьте пустым, чтобы не менять)" : "ключ провайдера поиска"}
                    onChange={e => setWebKeyInput(e.target.value)} />
                  <span className="hint">
                    Пустое поле не затирает сохранённый ключ — как у API-ключа провайдера.
                    Сам ключ обратно не отдаётся, видно только факт его наличия.
                  </span>
                </label>
              )}

              {cfg.web_provider === "searxng" && (
                <label className="flex flex-col gap-1 sm:col-span-2">
                  <span className="micro">Адрес инстанса SearXNG</span>
                  <input className="input font-mono text-xs" value={cfg.web_base_url} disabled={saving}
                    placeholder="https://searx.example.org"
                    onChange={e => patchCfg({ web_base_url: e.target.value })} />
                  <span className="hint">Только публичный http(s)-адрес: внутренние адреса отклоняет SSRF-гард.</span>
                </label>
              )}
            </div>
          )}
        </div>

        {/* ── Режим и сохранение ───────────────────────────────── */}
        <div className="border-t border-[var(--line-soft)] pt-4 flex flex-col gap-3">
          <div className="flex items-center gap-4 flex-wrap">
            <Toggle checked={cfg.enabled} disabled={saving} label="Включить агента"
              onChange={() => patchCfg({ enabled: !cfg.enabled })} />
            <Toggle checked={cfg.readonly} disabled={saving} label="Только чтение"
              onChange={() => patchCfg({ readonly: !cfg.readonly })} />
            <label className="flex flex-col gap-1 w-28">
              <span className="micro" title="Сколько обращений к модели за один ответ">
                Шагов агента
              </span>
              <input type="number" min={1} max={40} className="input" value={cfg.max_steps} disabled={saving}
                onChange={e => patchCfg({ max_steps: Number(e.target.value) })} />
            </label>
            <label className="flex flex-col gap-1 w-32">
              <span className="micro" title="Больше — длиннее ответ и крупнее тело запроса за раз">
                Токенов на ответ
              </span>
              <input type="number" min={256} max={64000} step={1024} className="input"
                value={cfg.max_tokens} disabled={saving}
                onChange={e => patchCfg({ max_tokens: Number(e.target.value) })} />
            </label>
            <button onClick={save} disabled={saving}
              className="ml-auto self-end flex items-center gap-2 px-4 py-2 rounded-lg font-semibold text-sm bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-50">
              {saving ? <Loader2 size={14} className="animate-spin" /> : null} Сохранить
            </button>
          </div>

          {/* Предупреждение показываем в состоянии риска — когда запись уже
              разрешена. Границы перечислены явно: человек должен понимать объём
              доступа до того, как задаст вопрос, а не узнавать его из отказа. */}
          {!cfg.readonly && (
            <div className="flex gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div className="flex flex-col gap-1">
                <span>Запись включена: ассистент может менять данные панели через её же API — правила, хосты, заметки, серверы.</span>
                <span>Всегда запрещены: деньги и покупки, деплой и перезапуск, секреты, удаление чего угодно и настройки самого ассистента.</span>
                <span>После обращения в интернет запись в панель в этом же ответе блокируется (кроме заметок в Библиотеке): страница могла содержать указания, адресованные ассистенту.</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <PromptPresets
        activeId={cfg.active_preset_id}
        onPickActive={id => patchCfg({ active_preset_id: id })}
      />
    </>
  );
}
