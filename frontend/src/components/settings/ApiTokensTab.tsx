import { useEffect, useState } from "react";
import { Loader2, Copy, Check, KeyRound, AlertTriangle, Trash2, Plus } from "lucide-react";
import { toast } from "../infra/Toast";

interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  readonly: boolean;
  expires_at: number;   // epoch seconds, 0 = never
  created_at: number;
  last_used_at: number; // 0 = never used
}

function fmtError(data: any): string {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: any) => e?.msg ?? "ошибка").join("; ") || "Ошибка";
  return "Ошибка";
}

const fmtDate = (s: number) => (s ? new Date(s * 1000).toLocaleDateString() : "—");

export function ApiTokensTab() {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  // create form
  const [name, setName] = useState("");
  const [days, setDays] = useState("");
  const [readonly, setReadonly] = useState(false);

  // show-once plaintext + copy + revoke-confirm
  const [fresh, setFresh] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetch("/api/api-tokens");
      if (!r.ok) throw new Error("bad response");
      setTokens(await r.json());
    } catch { toast("Не удалось загрузить токены", "error"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    const nm = name.trim();
    if (!nm) { toast("Укажите имя токена", "error"); return; }
    const d = days.trim() ? Number(days) : null;
    if (d !== null && (!Number.isInteger(d) || d < 1 || d > 3650)) {
      toast("Срок — число дней 1–3650", "error"); return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/api-tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nm, readonly, expires_in_days: d }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(fmtError(data));
      setFresh(data.token);
      setCopied(false);
      setName(""); setDays(""); setReadonly(false);
      await load();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "error");
    } finally { setCreating(false); }
  };

  const copyFresh = async () => {
    if (!fresh) return;
    try { await navigator.clipboard.writeText(fresh); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { toast("Не удалось скопировать", "error"); }
  };

  const revoke = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); setTimeout(() => setConfirmId(c => (c === id ? null : c)), 3000); return; }
    setConfirmId(null);
    try {
      const r = await fetch(`/api/api-tokens/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("bad");
      toast("Токен отозван", "success");
      await load();
    } catch { toast("Не удалось отозвать токен", "error"); }
  };

  if (loading) {
    return <div className="flex items-center gap-2 text-[var(--t-faint)] text-sm py-10">
      <Loader2 size={16} className="animate-spin" /> Загрузка...
    </div>;
  }

  return (
    <div className="flex flex-col gap-4 max-w-2xl">
      <div className="card card-p flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <KeyRound size={16} className="text-[var(--accent-hi)]" />
          <span className="text-sm font-semibold text-[var(--t-hi)]">Токены API</span>
        </div>
        <p className="hint">
          Долгоживущие токены доступа к API этого аккаунта — для внешних интеграций
          (MCP, скрипты, ИИ) вместо браузерной сессии. Передавайте заголовком
          <span className="font-mono"> Authorization: Bearer &lt;токен&gt;</span>.
          Секрет показывается один раз — храните только хеш.
        </p>

        {/* create */}
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 flex-1 min-w-[160px]">
            <span className="micro">Имя</span>
            <input className="input" value={name} disabled={creating} placeholder="ci-bot"
              onChange={e => setName(e.target.value)} onKeyDown={e => e.key === "Enter" && create()} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="micro">Срок (дней, опц.)</span>
            <input className="input w-32" type="number" min={1} max={3650} value={days} disabled={creating}
              placeholder="бессрочно" onChange={e => setDays(e.target.value)} />
          </label>
          <label className="flex items-center gap-2 cursor-pointer select-none pb-2">
            <input type="checkbox" checked={readonly} disabled={creating}
              onChange={e => setReadonly(e.target.checked)} />
            <span className="text-sm text-[var(--t-mid)]">Только чтение</span>
          </label>
          <button type="button" className="btn btn-primary" disabled={creating} onClick={create}>
            {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Создать
          </button>
        </div>

        {/* show-once */}
        {fresh && (
          <div className="flex flex-col gap-2 px-3 py-3 rounded-lg bg-[var(--warn-dim)] border border-[var(--warn-line)]">
            <div className="flex items-center gap-2 text-[var(--warn)] text-xs">
              <AlertTriangle size={14} className="shrink-0" />
              Токен показан один раз — скопируйте сейчас, повторно он не отображается.
            </div>
            <div className="flex items-center gap-2">
              <input className="input font-mono text-xs" readOnly value={fresh}
                onFocus={e => e.currentTarget.select()} />
              <button type="button" onClick={copyFresh} title="Копировать"
                className="p-2 rounded-md border border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)]">
                {copied ? <Check size={14} className="text-[var(--ok)]" /> : <Copy size={14} />}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* list */}
      <div className="card card-p flex flex-col gap-2">
        <span className="text-xs font-semibold text-[var(--t-hi)]">Активные токены</span>
        {tokens && tokens.length === 0 && <p className="hint">Токенов пока нет.</p>}
        {tokens && tokens.map(t => (
          <div key={t.id} className="flex items-center gap-3 py-2 border-b border-[var(--line-soft)] last:border-0">
            <div className="flex flex-col min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm text-[var(--t-hi)] truncate">{t.name}</span>
                {t.readonly && <span className="chip" style={{ fontSize: 10 }}>только чтение</span>}
              </div>
              <span className="micro font-mono">{t.prefix}••••</span>
            </div>
            <div className="text-right text-[var(--t-low)] hidden sm:block" style={{ fontSize: 11 }}>
              <div>создан {fmtDate(t.created_at)}</div>
              <div>{t.expires_at ? `до ${fmtDate(t.expires_at)}` : "бессрочно"} · исп. {fmtDate(t.last_used_at)}</div>
            </div>
            <button type="button" onClick={() => revoke(t.id)} title="Отозвать"
              className={`p-2 rounded-md border ${confirmId === t.id ? "border-[var(--err-line)] text-[var(--err)] bg-[var(--err-dim)]" : "border-[var(--line)] text-[var(--t-mid)] hover:bg-[var(--bg3)]"}`}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
