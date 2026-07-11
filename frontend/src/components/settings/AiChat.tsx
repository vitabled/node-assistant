import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Bot, Wrench, AlertCircle } from "lucide-react";
import { toast } from "../infra/Toast";

interface AiConfig {
  enabled: boolean;
  provider: "openai" | "anthropic";
  base_url: string;
  model: string;
  max_steps: number;
  readonly: boolean;
  has_key: boolean;
}

type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tools: { id?: string; name: string; ok?: boolean }[] };

const DEFAULTS: Record<AiConfig["provider"], { base_url: string; model: string }> = {
  openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  anthropic: { base_url: "https://api.anthropic.com/v1", model: "claude-haiku-4-5-20251001" },
};

export function AiChat() {
  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [saving, setSaving] = useState(false);

  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadErr, setLoadErr] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    fetch("/api/ai/config")
      .then(r => { if (!r.ok) throw new Error("bad"); return r.json(); })
      .then(setCfg)
      .catch(() => setLoadErr(true));
    // Abort an in-flight chat stream if the user leaves the tab.
    return () => abortRef.current?.abort();
  }, []);
  useEffect(() => { scrollRef.current?.scrollTo?.(0, scrollRef.current.scrollHeight); }, [msgs]);

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
      toast("Настройки ИИ сохранены", "success");
    } catch { toast("Не удалось сохранить настройки ИИ", "error"); }
    finally { setSaving(false); }
  };

  const send = async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    setMsgs(m => [...m, { role: "user", text: prompt }, { role: "assistant", text: "", tools: [] }]);
    setBusy(true);

    // Pure updater: replace the last assistant message with a NEW object (no
    // in-place mutation — safe under React StrictMode double-invoke).
    const patchLast = (fn: (m: Extract<Msg, { role: "assistant" }>) => void) =>
      setMsgs(m => {
        const last = m[m.length - 1];
        if (!last || last.role !== "assistant") return m;
        const next = { ...last, tools: last.tools.map(t => ({ ...t })) };
        fn(next);
        return [...m.slice(0, -1), next];
      });

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const res = await fetch("/api/ai/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }), signal: ac.signal,
      });
      if (!res.ok || !res.body) throw new Error("stream failed");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const ln of lines) {
          if (!ln.trim()) continue;
          let ev: any;
          try { ev = JSON.parse(ln); } catch { continue; }
          if (ev.type === "text") patchLast(a => { a.text += ev.delta; });
          else if (ev.type === "tool_call") patchLast(a => { a.tools.push({ id: ev.id, name: ev.name }); });
          else if (ev.type === "tool_result")
            patchLast(a => { const t = a.tools.find(x => (ev.id ? x.id === ev.id : x.name === ev.name && x.ok === undefined)); if (t) t.ok = ev.ok; });
          else if (ev.type === "error") patchLast(a => { a.text += `\n⚠️ ${ev.message}`; });
        }
      }
    } catch {
      if (!ac.signal.aborted) patchLast(a => { a.text += "\n⚠️ Ошибка соединения с ИИ."; });
    } finally { if (abortRef.current === ac) abortRef.current = null; setBusy(false); }
  };

  if (loadErr) return <p className="text-sm text-[var(--err)]">Не удалось загрузить конфигурацию ИИ.</p>;
  if (!cfg) return null;

  return (
    <div className="card card-p flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Bot size={16} className="text-[var(--accent-hi)]" />
        <span className="text-sm font-semibold text-[var(--t-hi)]">Встроенный ИИ-агент</span>
      </div>

      {/* provider config */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <span className="micro">Провайдер</span>
          <select className="selectbox" value={cfg.provider} disabled={saving}
            onChange={e => {
              const p = e.target.value as AiConfig["provider"];
              patchCfg({ provider: p, ...DEFAULTS[p] });
            }}>
            <option value="openai">OpenAI-совместимый</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="micro">Модель</span>
          <input className="input" value={cfg.model} disabled={saving}
            onChange={e => patchCfg({ model: e.target.value })} />
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

      {!cfg.enabled && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertCircle size={14} /> Агент выключен — включите и сохраните, чтобы начать чат.
        </div>
      )}

      {/* chat */}
      <div ref={scrollRef} className="flex flex-col gap-3 max-h-80 overflow-y-auto rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-3" data-testid="ai-chat-log">
        {msgs.length === 0 && <p className="text-[12px] text-[var(--t-faint)]">Спросите про ноды, правила, подписки или доступность.</p>}
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "self-end max-w-[85%]" : "self-start max-w-[90%]"}>
            {m.role === "user" ? (
              <div className="px-3 py-2 rounded-lg bg-[var(--accent-dim)] text-[var(--t-hi)] text-sm">{m.text}</div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {m.tools.map((t, j) => (
                  <div key={j} className="flex items-center gap-1.5 text-[11px] text-[var(--t-low)]">
                    <Wrench size={11} /> {t.name}
                    {t.ok === true && <span className="text-[var(--ok)]">✓</span>}
                    {t.ok === false && <span className="text-[var(--err)]">✗</span>}
                    {t.ok === undefined && <Loader2 size={10} className="animate-spin" />}
                  </div>
                ))}
                {m.text && <div className="px-3 py-2 rounded-lg bg-[var(--bg1)] border border-[var(--line-soft)] text-[var(--t-hi)] text-sm whitespace-pre-wrap">{m.text}</div>}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <input className="input flex-1" value={input} disabled={busy || !cfg.enabled}
          placeholder="Сообщение агенту..." onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") send(); }} />
        <button onClick={send} disabled={busy || !cfg.enabled || !input.trim()}
          className="p-2.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hi)] text-[var(--primary-ink)] disabled:opacity-40">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </div>
    </div>
  );
}
