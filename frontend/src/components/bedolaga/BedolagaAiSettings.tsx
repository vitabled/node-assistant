import { useState, useEffect, useCallback } from "react";
import { Bot, Save, Loader2, CheckCircle2, XCircle, Eye, ShieldCheck } from "lucide-react";
import { bedolagaApi, type BedolagaConfig } from "./api";
import { toast } from "../infra/Toast";
import { Page, PageHeader, Field } from "../infra/ui";

export function BedolagaAiSettings() {
  const [cfg, setCfg] = useState<BedolagaConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  // подключение
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [authHeader, setAuthHeader] = useState("X-API-Key");

  // AI
  const [aiEnabled, setAiEnabled] = useState(false);
  const [shadowMode, setShadowMode] = useState(true);
  const [aiProviderUrl, setAiProviderUrl] = useState("");
  const [aiProviderKey, setAiProviderKey] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [maxReplies, setMaxReplies] = useState(2);
  const [tgChatId, setTgChatId] = useState("");
  const [tgThreadId, setTgThreadId] = useState("");
  const [allowedDomains, setAllowedDomains] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const c = await bedolagaApi.getConfig();
      setCfg(c);
      setBaseUrl(c.base_url); setAuthHeader(c.auth_header);
      setAiEnabled(c.ai_enabled); setShadowMode(c.shadow_mode);
      setAiProviderUrl(c.ai_provider_base_url); setAiModel(c.ai_model);
      setMaxReplies(c.max_ai_replies_per_ticket);
      setTgChatId(c.telegram_topic_chat_id); setTgThreadId(c.telegram_topic_thread_id);
      setAllowedDomains((c.allowed_domains || []).join(", "));
    } catch (e) { toast((e as Error).message, "error"); }
    setLoading(false);
  }, []);
  useEffect(() => { load(); }, [load]);

  const saveConnection = async () => {
    setSaving(true);
    try {
      await bedolagaApi.saveConfig(baseUrl, token || undefined, authHeader);
      toast("Подключение сохранено", "success");
      setToken("");
      await load();
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  };

  const test = async () => {
    setTesting(true);
    try {
      const r = await bedolagaApi.testConnection();
      if (r.ok) toast("Соединение установлено ✓", "success");
      else toast(r.error || "Ошибка соединения", "error");
    } catch (e) { toast((e as Error).message, "error"); }
    setTesting(false);
  };

  const saveAi = async () => {
    setSaving(true);
    try {
      await bedolagaApi.saveAiConfig({
        ai_enabled: aiEnabled, shadow_mode: shadowMode,
        ai_provider_base_url: aiProviderUrl,
        ai_provider_key: aiProviderKey || undefined,
        ai_model: aiModel, max_ai_replies_per_ticket: maxReplies,
        telegram_topic_chat_id: tgChatId, telegram_topic_thread_id: tgThreadId,
        allowed_domains: allowedDomains.split(",").map(s => s.trim()).filter(Boolean),
      });
      toast("Настройки AI сохранены", "success");
      setAiProviderKey("");
      await load();
    } catch (e) { toast((e as Error).message, "error"); }
    setSaving(false);
  };

  if (loading) {
    return (
      <Page>
        <PageHeader icon={<Bot size={16} className="text-[var(--accent-hi)]" />} title="AI Провайдеры" />
        <div className="py-16 text-center text-[var(--t-faint)]"><Loader2 size={18} className="animate-spin inline" /></div>
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader icon={<Bot size={16} className="text-[var(--accent-hi)]" />} title="AI Провайдеры"
        subtitle="Подключение к webapi бедолаги и настройки AI-автоответчика" />

      {/* Подключение к webapi */}
      <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-5 mb-5">
        <p className="text-sm font-semibold text-[var(--t-hi)] mb-1">Подключение к Bedolaga Web API</p>
        <p className="text-xs text-[var(--t-faint)] mb-4">
          Сервисный токен создаётся в самом боте: `.env` → `WEB_API_DEFAULT_TOKEN`, либо новый токен в админке бота.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
          <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} placeholder="https://mydoza.com/api" />
          <Field label="Заголовок авторизации" value={authHeader} onChange={setAuthHeader} placeholder="X-API-Key" />
        </div>
        <Field label={cfg?.has_token ? `Токен (сохранён, ${cfg.token_hint})` : "Токен"} value={token}
          onChange={setToken} placeholder={cfg?.has_token ? "оставьте пустым, чтобы не менять" : "сервисный токен"} type="password" />
        <div className="flex gap-2 mt-4">
          <button onClick={saveConnection} disabled={saving}
            className="px-3 py-2 rounded-md bg-[var(--accent)] text-[var(--accent-ink)] text-xs font-medium flex items-center gap-1.5 disabled:opacity-50">
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Сохранить
          </button>
          <button onClick={test} disabled={testing || !cfg?.has_token}
            className="px-3 py-2 rounded-md bg-[var(--bg3)] text-[var(--t-mid)] text-xs font-medium flex items-center gap-1.5 disabled:opacity-50">
            {testing ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />} Проверить соединение
          </button>
        </div>
      </div>

      {/* AI автоответчик */}
      <div className="rounded-xl border border-[var(--line-soft)] bg-[var(--bg2)] p-5">
        <p className="text-sm font-semibold text-[var(--t-hi)] mb-1">AI Автоответчик</p>
        <p className="text-xs text-[var(--t-faint)] mb-4">
          Отвечает по базе знаний с детерминированными воротами и пост-фильтром. Деньги, возвраты и всё, чего нет в базе — эскалирует оператору.
        </p>

        <ToggleRow label="Включить AI-автоответчик" checked={aiEnabled} onChange={setAiEnabled} />
        <ToggleRow label="Shadow-режим (черновики без отправки клиенту)" checked={shadowMode} onChange={setShadowMode}
          hint={shadowMode ? "Клиенту ничего не уходит — черновики можно посмотреть в Telegram-топике" : "⚠️ Ответы уходят клиенту напрямую"} />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
          <Field label="AI Provider Base URL (OpenAI-совместимый)" value={aiProviderUrl} onChange={setAiProviderUrl}
            placeholder="http://127.0.0.1:8317/v1" />
          <Field label="Модель" value={aiModel} onChange={setAiModel} placeholder="claude-sonnet-5" />
        </div>
        <Field label={cfg?.has_ai_provider_key ? "API-ключ провайдера (сохранён)" : "API-ключ провайдера"}
          value={aiProviderKey} onChange={setAiProviderKey}
          placeholder={cfg?.has_ai_provider_key ? "оставьте пустым, чтобы не менять" : "sk-…"} type="password" />

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
          <Field label="Лимит AI-ответов на тикет" value={String(maxReplies)}
            onChange={v => setMaxReplies(Number(v) || 0)} placeholder="2" />
          <Field label="Telegram чат для черновиков" value={tgChatId} onChange={setTgChatId} placeholder="-100…" />
          <Field label="ID топика" value={tgThreadId} onChange={setTgThreadId} placeholder="12345" />
        </div>
        <div className="mt-3">
          <Field label="Разрешённые домены в ответах (через запятую)" value={allowedDomains}
            onChange={setAllowedDomains} placeholder="mydoza.com, t.me" />
        </div>

        <div className="mt-4 rounded-lg border border-[var(--line-soft)] bg-[var(--bg1)] p-3 flex items-start gap-2">
          <ShieldCheck size={14} className="text-[var(--accent-hi)] mt-0.5 flex-none" />
          <p className="text-[11px] text-[var(--t-faint)]">
            Пост-фильтр: перед отправкой ответ проверяется на ссылки/домены вне белого списка и денежные темы —
            если найдено, ответ не уходит и тикет поднимается оператору.
          </p>
        </div>

        <button onClick={saveAi} disabled={saving}
          className="mt-4 px-3 py-2 rounded-md bg-[var(--accent)] text-[var(--accent-ink)] text-xs font-medium flex items-center gap-1.5 disabled:opacity-50">
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Сохранить настройки AI
        </button>
      </div>
    </Page>
  );
}

function ToggleRow({ label, checked, onChange, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-[var(--line-soft)] last:border-0">
      <div>
        <p className="text-xs text-[var(--t-mid)]">{label}</p>
        {hint && <p className="text-[10px] text-[var(--t-faint)] mt-0.5 flex items-center gap-1"><Eye size={9} />{hint}</p>}
      </div>
      <button onClick={() => onChange(!checked)}
        className={`w-9 h-5 rounded-full flex-none transition-colors relative ${checked ? "bg-[var(--accent)]" : "bg-[var(--bg3)]"}`}>
        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </button>
    </div>
  );
}
