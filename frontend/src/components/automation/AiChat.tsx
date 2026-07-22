import { useEffect, useRef, useState } from "react";
import { Loader2, Send, Bot, Wrench, AlertCircle } from "lucide-react";

/** Только то, что нужно чату: гейт композера. Форма настроек живёт в
 *  «Настройки → Ассистент» (`settings/AiSettingsTab.tsx`) — эта страница НИЧЕГО
 *  не сохраняет, поэтому и полного конфига ей не требуется. */
interface AiChatConfig {
  enabled: boolean;
  has_key: boolean;
}

type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tools: { id?: string; name: string; ok?: boolean }[] };

export function AiChat() {
  const [cfg, setCfg] = useState<AiChatConfig | null>(null);
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
    // Полноэкранная колонка: лог занимает остаток и прокручивается сам, композер
    // приколот снизу. `min-h-0` обязателен — без него flex-ребёнок не сжимается
    // и лог выдавливает композер за экран (страница целиком не скроллится:
    // `body{overflow:hidden}` в index.css).
    //
    // `flex-1`, а НЕ `h-full`: родитель (`<Screen>` в App.tsx) — flex-колонка с
    // `flex:1; min-height:0`, поэтому процентная высота зависела бы от того,
    // разрешима ли высота выше по дереву, а flex-растяжение — нет.
    <div className="flex flex-col flex-1 min-h-0 ni-pagebody">
      <div className="shrink-0 flex items-center gap-2 h-11 px-4 ni-pagehead">
        <Bot size={16} className="text-[var(--accent-hi)]" />
        <span className="text-sm font-semibold text-[var(--t-hi)]">Встроенный ИИ-агент</span>
      </div>

      {!cfg.enabled && (
        <div className="shrink-0 mx-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertCircle size={14} /> Агент выключен — включите его в «Настройки → Ассистент».
        </div>
      )}
      {cfg.enabled && !cfg.has_key && (
        <div className="shrink-0 mx-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)] text-[var(--warn)] text-xs">
          <AlertCircle size={14} /> Не задан API-ключ — добавьте его в «Настройки → Ассистент».
        </div>
      )}

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-3 mx-4 rounded-lg border border-[var(--line-soft)] bg-[var(--bg2)] p-4" data-testid="ai-chat-log">
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

      <div className="shrink-0 flex items-center gap-2 p-4">
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
