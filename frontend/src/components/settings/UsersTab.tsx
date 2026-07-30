// «Настройки → Пользователи» (Волна 13).
//
// Экран управления людьми в установке. Работает с `/api/users` (нужна привилегия
// `admin.users`; вкладку прячет Settings.tsx, но настоящая граница — сервер).
//
// ⚠️ Ошибки бэкенда показываются ДОСЛОВНО. «Нельзя удалить последнего
// суперпользователя» или «Нельзя выключить себя» объясняют человеку, что
// произошло; подмена этого на «Ошибка» заставила бы гадать. Поэтому здесь нет
// собственных формулировок для 403/409 — только то, что сказал сервер.
import { useCallback, useEffect, useState } from "react";
import {
  Users, Plus, KeyRound, Trash2, Copy, Check, AlertTriangle, Loader2,
  ShieldCheck, ChevronDown, Power, Archive,
} from "lucide-react";
import { MultiSelect } from "../MultiSelect";

interface UserRow {
  id: string;
  login: string;
  is_superuser: boolean;
  disabled: boolean;
  role_ids: string[];
  workspace_id: string;
  legacy_workspace_id: string;
  created_at: number;
  last_login: number;
}

interface RoleRef { id: string; name: string }

interface Props {
  /** Кто смотрит: чтобы отметить свою строку. */
  meId: string;
  /** Флаг суперпользователя выдаёт ТОЛЬКО суперпользователь (гейт в api/users.py),
   *  поэтому не-суперпользователю переключатель не показываем вовсе — иначе
   *  единственным ответом на нажатие был бы 403. */
  meIsSuperuser: boolean;
}

/** Минимум пароля из `users._check_password` — проверяем на клиенте, чтобы
 *  очевидная опечатка не стоила запроса. Сервер проверяет всё равно. */
const MIN_PASSWORD = 10;

const JSONH = { "Content-Type": "application/json" };

/** Текст ошибки FastAPI: строковый `detail` (наши 403/409) или список pydantic. */
function fmtError(data: unknown): string {
  const d = (data as { detail?: unknown } | null)?.detail;
  if (typeof d === "string" && d.trim()) return d;
  if (Array.isArray(d)) {
    const parts = d.map(e => (e as { msg?: string })?.msg).filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return "Не удалось выполнить операцию";
}

const msg = (e: unknown) => (e instanceof Error ? e.message : "Не удалось выполнить операцию");

const fmtDate = (ts: number) =>
  ts ? new Date(ts * 1000).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
  }) : "—";

/** Пароль для нового человека.
 *
 *  Генератор локальный, а не импорт `generatePassword` из `auth/store`: тот модуль
 *  переписывается параллельной задачей, и завязка на его экспорт сломала бы сборку.
 *  CSPRNG обязателен — `Math.random` для пароля не годится. */
function makePassword(length = 20): string {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*-_";
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < length; i++) out += alphabet[bytes[i] % alphabet.length];
  return out;
}

export function UsersTab({ meId, meIsSuperuser }: Props) {
  const [rows, setRows] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<RoleRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // создание
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [newRoles, setNewRoles] = useState<string[]>([]);
  const [newSuper, setNewSuper] = useState(false);
  // Пароль показывается ОДИН раз: сервер возвращает запись без него (приём из
  // «Токенов API»), восстановить его потом нечем — только задать новый.
  const [fresh, setFresh] = useState<{ login: string; password: string } | null>(null);
  const [copied, setCopied] = useState(false);

  // строка списка
  const [openId, setOpenId] = useState("");
  const [draftRoles, setDraftRoles] = useState<string[]>([]);
  const [pw, setPw] = useState("");
  const [confirmId, setConfirmId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/users");
      const d = await r.json().catch(() => null);
      if (!r.ok) throw new Error(fmtError(d));
      setRows((d?.users ?? []) as UserRow[]);
      setRoles((d?.roles ?? []) as RoleRef[]);
      setErr("");
    } catch (e) {
      setErr(msg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  /** Один канал для всех мутаций: держит `busy`, перечитывает список и не
   *  переписывает сообщение сервера своим. */
  const send = async (url: string, init: RequestInit): Promise<boolean> => {
    setBusy(true);
    setErr("");
    try {
      const r = await fetch(url, init);
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        throw new Error(fmtError(d));
      }
      await load();
      return true;
    } catch (e) {
      setErr(msg(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    const l = login.trim();
    if (!l) { setErr("Укажите логин"); return; }
    if (password.length < MIN_PASSWORD) {
      setErr(`Пароль короче ${MIN_PASSWORD} символов`); return;
    }
    const ok = await send("/api/users", {
      method: "POST", headers: JSONH,
      body: JSON.stringify({
        login: l, password, role_ids: newRoles, is_superuser: newSuper,
      }),
    });
    if (!ok) return;
    setFresh({ login: l, password });
    setCopied(false);
    setLogin(""); setPassword(""); setNewRoles([]); setNewSuper(false);
  };

  const copyFresh = async () => {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(`${fresh.login} / ${fresh.password}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setErr("Буфер обмена недоступен — выделите и скопируйте вручную");
    }
  };

  const openRow = (u: UserRow) => {
    if (openId === u.id) { setOpenId(""); return; }
    setOpenId(u.id);
    setDraftRoles(u.role_ids);
    setPw("");
    setConfirmId("");
  };

  const saveRoles = (u: UserRow) => send(`/api/users/${u.id}/roles`, {
    method: "PUT", headers: JSONH, body: JSON.stringify({ role_ids: draftRoles }),
  });

  const savePassword = async (u: UserRow) => {
    if (pw.length < MIN_PASSWORD) {
      setErr(`Пароль короче ${MIN_PASSWORD} символов`); return;
    }
    const ok = await send(`/api/users/${u.id}/password`, {
      method: "PUT", headers: JSONH, body: JSON.stringify({ password: pw }),
    });
    if (ok) { setFresh({ login: u.login, password: pw }); setPw(""); setCopied(false); }
  };

  const setFlag = (u: UserRow, flag: "disabled" | "superuser", value: boolean) =>
    send(`/api/users/${u.id}/${flag}`, {
      method: "PUT", headers: JSONH, body: JSON.stringify({ value }),
    });

  const remove = async (u: UserRow) => {
    // Удаление — второй подтверждаемый шаг: выключение мягче и обратимо, поэтому
    // оно стоит рядом и предлагается первым.
    if (confirmId !== u.id) { setConfirmId(u.id); return; }
    setConfirmId("");
    await send(`/api/users/${u.id}`, { method: "DELETE" });
  };

  const roleName = (id: string) => roles.find(r => r.id === id)?.name ?? id;
  const roleOptions = roles.map(r => ({ value: r.id, label: r.name }));

  return (
    <div className="flex flex-col gap-4 max-w-3xl">

      {/* ── ошибка любой операции: ровно то, что ответил сервер ── */}
      {err && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg text-xs"
          style={{ background: "var(--err-dim)", border: "1px solid var(--err-line)", color: "var(--err)" }}>
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>{err}</span>
        </div>
      )}

      {/* ── новый пользователь ── */}
      <div className="card card-p flex flex-col gap-4">
        <div className="flex items-center gap-2">
          <Users size={16} style={{ color: "var(--accent-hi)" }} />
          <span className="text-sm font-semibold" style={{ color: "var(--t-hi)" }}>
            Новый пользователь
          </span>
        </div>
        <p className="hint">
          Права даются только ролями: человек получает объединение привилегий своих
          ролей. Без роли он войдёт, но не увидит ничего, кроме личных настроек.
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 flex-1" style={{ minWidth: 150 }}>
            <span className="micro">Логин</span>
            <input className="input" value={login} disabled={busy} placeholder="ivan"
              onChange={e => setLogin(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 flex-1" style={{ minWidth: 200 }}>
            <span className="micro">Пароль (минимум {MIN_PASSWORD})</span>
            <input className="input font-mono text-xs" value={password} disabled={busy}
              onChange={e => setPassword(e.target.value)} />
          </label>
          <button type="button" className="btn btn-soft" disabled={busy}
            onClick={() => setPassword(makePassword())}>
            <KeyRound size={14} /> Сгенерировать
          </button>
        </div>

        <div style={{ maxWidth: 340 }}>
          <MultiSelect label="Роли нового пользователя" selected={newRoles}
            onChange={setNewRoles} options={roleOptions} disabled={busy}
            placeholder="— без ролей —" />
        </div>

        {meIsSuperuser && (
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <button type="button" role="switch" aria-checked={newSuper} disabled={busy}
              onClick={() => setNewSuper(v => !v)}
              className={`switch ${newSuper ? "on" : ""}`} />
            <span className="text-sm" style={{ color: "var(--t-mid)" }}>
              Суперпользователь (все привилегии, минуя роли)
            </span>
          </label>
        )}

        <div>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={create}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Создать
          </button>
        </div>

        {/* показ-один-раз */}
        {fresh && (
          <div className="flex flex-col gap-2 px-3 py-3 rounded-lg"
            style={{ background: "var(--warn-dim)", border: "1px solid var(--warn-line)" }}>
            <div className="flex items-start gap-2 text-xs" style={{ color: "var(--warn)" }}>
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>
                Пароль показан один раз — передайте его человеку сейчас. Сервер хранит
                только хеш, повторно показать пароль нечем: можно лишь задать новый.
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input className="input font-mono text-xs" readOnly
                value={`${fresh.login} / ${fresh.password}`}
                onFocus={e => e.currentTarget.select()} />
              <button type="button" onClick={copyFresh} title="Копировать"
                className="p-2 rounded-md"
                style={{ border: "1px solid var(--line)", color: "var(--t-mid)" }}>
                {copied ? <Check size={14} style={{ color: "var(--ok)" }} /> : <Copy size={14} />}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setFresh(null)}>
                Скрыть
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── список ── */}
      <div className="card card-p flex flex-col gap-1">
        <span className="text-xs font-semibold mb-1" style={{ color: "var(--t-hi)" }}>
          Пользователи установки
        </span>

        {loading && <p className="micro">Загрузка…</p>}
        {!loading && rows.length === 0 && <p className="hint">Пользователей пока нет.</p>}

        {rows.map(u => {
          const open = openId === u.id;
          return (
            <div key={u.id} className="flex flex-col"
              style={{ borderBottom: "1px solid var(--line-soft)" }}>

              <div className="flex items-center gap-3 py-2">
                <button type="button" onClick={() => openRow(u)}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left">
                  <ChevronDown size={13} className="shrink-0"
                    style={{ color: "var(--t-low)", transform: open ? "rotate(180deg)" : "none" }} />
                  <div className="flex flex-col min-w-0">
                    <span className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm truncate" style={{ color: "var(--t-hi)" }}>{u.login}</span>
                      {u.id === meId && <span className="chip" style={{ fontSize: 10 }}>это вы</span>}
                      {u.is_superuser && (
                        <span className="chip accent" style={{ fontSize: 10 }}>
                          <ShieldCheck size={10} /> суперпользователь
                        </span>
                      )}
                      {u.disabled && (
                        <span className="chip warn" style={{ fontSize: 10 }}>выключен</span>
                      )}
                    </span>
                    <span className="flex items-center gap-1 flex-wrap mt-1">
                      {u.role_ids.length === 0
                        ? <span className="micro" style={{ color: "var(--t-faint)" }}>без ролей</span>
                        : u.role_ids.map(rid => (
                          <span key={rid} className="chip" style={{ fontSize: 10 }}>{roleName(rid)}</span>
                        ))}
                    </span>
                  </div>
                </button>
                <span className="text-right hidden sm:block" style={{ fontSize: 11, color: "var(--t-low)" }}>
                  вход {fmtDate(u.last_login)}
                </span>
              </div>

              {/* Архив прежней рабочей области: при миграции с прежней модели данные
                  аккаунтов 2..N остались на диске и НЕ были слиты. Не сказать об этом —
                  значит сделать вид, что их нет. */}
              {u.legacy_workspace_id && (
                <div className="flex items-start gap-2 mb-2 px-2 py-1.5 rounded-md"
                  style={{ background: "var(--bg3)", fontSize: 11, color: "var(--t-low)" }}>
                  <Archive size={12} className="shrink-0 mt-0.5" />
                  <span>
                    Данные прежнего аккаунта сохранены на диске отдельной папкой
                    (<span className="font-mono">{u.legacy_workspace_id}</span>) и не слиты
                    с текущей рабочей областью.
                  </span>
                </div>
              )}

              {open && (
                <div className="flex flex-col gap-3 pb-3 pl-5">
                  <div style={{ maxWidth: 340 }}>
                    <MultiSelect label="Роли" selected={draftRoles} onChange={setDraftRoles}
                      options={roleOptions} disabled={busy} placeholder="— без ролей —" />
                  </div>
                  <div>
                    <button type="button" className="btn btn-soft" disabled={busy}
                      onClick={() => saveRoles(u)}>Сохранить роли</button>
                  </div>

                  <div className="flex flex-wrap items-end gap-2">
                    <label className="flex flex-col gap-1 flex-1" style={{ minWidth: 200 }}>
                      <span className="micro">Новый пароль</span>
                      <input className="input font-mono text-xs" value={pw} disabled={busy}
                        onChange={e => setPw(e.target.value)} />
                    </label>
                    <button type="button" className="btn btn-soft" disabled={busy}
                      onClick={() => setPw(makePassword())}>
                      <KeyRound size={14} /> Сгенерировать
                    </button>
                    <button type="button" className="btn btn-warn" disabled={busy}
                      onClick={() => savePassword(u)}>Сменить пароль</button>
                  </div>
                  <p className="hint">
                    Смена пароля немедленно завершит все сессии этого человека — и
                    браузерные, и его API-токены.
                  </p>

                  {meIsSuperuser && (
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <button type="button" role="switch" aria-checked={u.is_superuser}
                        disabled={busy} className={`switch ${u.is_superuser ? "on" : ""}`}
                        onClick={() => setFlag(u, "superuser", !u.is_superuser)} />
                      <span className="text-sm" style={{ color: "var(--t-mid)" }}>
                        Суперпользователь
                      </span>
                    </label>
                  )}

                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className="btn btn-soft" disabled={busy}
                      onClick={() => setFlag(u, "disabled", !u.disabled)}>
                      <Power size={14} /> {u.disabled ? "Включить" : "Выключить"}
                    </button>
                    <button type="button"
                      className={confirmId === u.id ? "btn btn-danger" : "btn btn-ghost"}
                      disabled={busy} onClick={() => remove(u)}>
                      <Trash2 size={14} /> {confirmId === u.id ? "Точно удалить?" : "Удалить"}
                    </button>
                    <span className="hint" style={{ marginTop: 0 }}>
                      Выключение обратимо и сохраняет данные — удаление нет.
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
