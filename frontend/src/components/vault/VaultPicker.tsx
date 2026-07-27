import { useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, Lock, Loader2, ChevronDown } from "lucide-react";

/**
 * «Взять из Хранилища» — контролируемый пикер записей волта для любых форм,
 * которым нужен секрет (SSH-пароль, SSH-ключ, API-ключ, логин).
 *
 * Свой минимальный fetch-хелпер вместо общего `./api` — намеренно: пикер должен
 * собираться и работать независимо от страницы Хранилища. Заголовок авторизации
 * добавляет глобальный интерцептор (auth/apiClient.ts).
 */

// Публичная форма записи (`vault_store._public`) — секрета в ней нет никогда,
// только маска `hint`.
export interface VaultEntryLite {
  id: string;
  name: string;
  kind: string;
  resource: string;
  username: string;
  hint: string;
  has_secret: boolean;
  broken: boolean;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/vault${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
  }
  return res.json() as Promise<T>;
}

const KIND_LABEL: Record<string, string> = {
  api_key:        "API-ключ",
  ssh_password:   "SSH-пароль",
  ssh_key:        "SSH-ключ",
  login:          "Логин и пароль",
  provider_creds: "Доступ к хостингу",
  note:           "Заметка",
};

// Какое поле записи подставляется в поле формы (схемы — backend/api/vault.py).
// Для `login` это именно `password`: имя пользователя заполняется отдельно.
const VALUE_FIELD: Record<string, string> = {
  api_key:      "token",
  ssh_password: "password",
  login:        "password",
  note:         "text",
};

function valueOf(kind: string, fields: Record<string, string>): string {
  const preferred = fields[VALUE_FIELD[kind] ?? ""] ?? "";
  if (preferred) return preferred;
  // Запись могли создать с нестандартным именем поля — берём первое непустое,
  // чтобы пикер не оказался бесполезным из-за опечатки в названии.
  return Object.values(fields).find(v => typeof v === "string" && v) ?? "";
}

interface Props {
  /** Типы записей, которые имеет смысл подставлять в это поле. */
  kinds: string[];
  /** Секрет раскрыт и готов к подстановке в поле формы. */
  onPickValue: (v: string) => void;
  /** Выбран SSH-ключ: наружу уходит только id записи + её имя для подписи. */
  onPickKeyRef: (ref: string, label: string) => void;
  disabled?: boolean;
  /**
   * Отдавать ТОЛЬКО ссылку на запись, ни для одного типа не вызывая reveal.
   * Нужно там, где секрет читает бэкенд (адаптеры хостингов в инфра-биллинге):
   * значению в браузере взяться незачем, а reveal лишний раз светил бы секрет.
   */
  pickRefOnly?: boolean;
}

export function VaultPicker({ kinds, onPickValue, onPickKeyRef, disabled, pickRefOnly }: Props) {
  const [open,    setOpen]    = useState(false);
  const [rows,    setRows]    = useState<VaultEntryLite[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId,  setBusyId]  = useState("");
  const [err,     setErr]     = useState("");
  const box = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try { setRows(await req<VaultEntryLite[]>("")); }
    catch (e) {
      setRows([]);
      setErr(e instanceof Error ? e.message : "Не удалось загрузить Хранилище");
    } finally { setLoading(false); }
  }, []);

  // Закрытие по клику вне списка и по Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && rows === null) void load();
  };

  const pick = async (entry: VaultEntryLite) => {
    setErr("");
    // ⚠️ ПРИНЦИПИАЛЬНО: для SSH-ключа reveal НЕ вызывается. Наружу уходит только
    // id записи (`ssh_key_ref`), потому что `savedForm` карточки деплоя целиком
    // персистится в localStorage — приватный ключ попал бы туда навсегда.
    // Ключ разрешает бэкенд (services/ssh_auth.py) при каждом подключении.
    if (pickRefOnly || entry.kind === "ssh_key") {
      onPickKeyRef(entry.id, entry.name);
      setOpen(false);
      return;
    }
    setBusyId(entry.id);
    try {
      const r = await req<{ fields: Record<string, string> }>(`/${entry.id}/reveal`, { method: "POST" });
      const value = valueOf(entry.kind, r.fields ?? {});
      if (!value) { setErr("В записи нет значения для подстановки"); return; }
      onPickValue(value);
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось получить секрет");
    } finally { setBusyId(""); }
  };

  const shown = (rows ?? []).filter(r => kinds.includes(r.kind));

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        onClick={toggle}
        disabled={disabled}
        title="Подставить секрет из Хранилища"
        className="flex items-center gap-1.5 px-2 py-1 rounded-md border text-[11px] font-medium
                   transition-colors hover:bg-[var(--bg3)]
                   disabled:opacity-40 disabled:cursor-not-allowed"
        style={{ borderColor: "var(--line-soft)", background: "var(--bg2)", color: "var(--t-mid)" }}
      >
        <KeyRound size={11} /> Взять из Хранилища
        <ChevronDown size={11} className={`transition-transform ${open ? "rotate-180" : ""}`}
          style={{ color: "var(--t-faint)" }} />
      </button>

      {open && (
        <div
          className="absolute right-0 mt-1 z-40 w-72 max-h-64 overflow-y-auto
                     rounded-lg border shadow-2xl p-1.5 flex flex-col gap-0.5"
          style={{ borderColor: "var(--line)", background: "var(--bg1)" }}
        >
          {loading && (
            <p className="flex items-center gap-1.5 px-2 py-2 text-[11px]" style={{ color: "var(--t-low)" }}>
              <Loader2 size={11} className="animate-spin" /> Загрузка…
            </p>
          )}

          {!loading && shown.length === 0 && (
            <div className="px-2 py-2 flex flex-col gap-1">
              <p className="text-[11px]" style={{ color: "var(--t-low)" }}>
                Подходящих записей нет.
              </p>
              <p className="text-[11px]" style={{ color: "var(--t-faint)" }}>
                Добавьте их в разделе «Справка» → «Хранилище».
              </p>
            </div>
          )}

          {!loading && shown.map(e => (
            <button
              key={e.id}
              type="button"
              onClick={() => void pick(e)}
              disabled={!!busyId || e.broken || !e.has_secret}
              className="w-full flex items-start gap-2 px-2 py-1.5 rounded-md text-left
                         transition-colors hover:bg-[var(--bg3)]
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <span className="mt-0.5 shrink-0" style={{ color: "var(--t-faint)" }}>
                {e.kind === "ssh_key" ? <KeyRound size={12} /> : <Lock size={12} />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-xs truncate" style={{ color: "var(--t-hi)" }}>{e.name}</span>
                <span className="block text-[11px] truncate" style={{ color: "var(--t-faint)" }}>
                  {KIND_LABEL[e.kind] ?? e.kind}
                  {e.resource ? ` · ${e.resource}` : ""}
                  {e.kind !== "ssh_key" && e.hint ? ` · ${e.hint}` : ""}
                </span>
                {e.broken && (
                  <span className="block text-[11px]" style={{ color: "var(--err)" }}>
                    секрет не расшифровывается
                  </span>
                )}
                {!e.broken && !e.has_secret && (
                  <span className="block text-[11px]" style={{ color: "var(--warn)" }}>
                    запись без секрета
                  </span>
                )}
              </span>
              {busyId === e.id && (
                <Loader2 size={11} className="animate-spin mt-1 shrink-0" style={{ color: "var(--t-low)" }} />
              )}
            </button>
          ))}

          {err && <p className="px-2 pt-1 pb-0.5 text-[11px]" style={{ color: "var(--err)" }}>{err}</p>}
        </div>
      )}
    </div>
  );
}
