import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  LogIn, Loader2, KeyRound, Copy, Check, Server, ArrowLeft, Trash2, ShieldCheck,
} from "lucide-react";
import { useAuth } from "./useAuth";
import { addAccount, switchTo, forget, generatePassword, type DeviceAccount } from "./store";

const inputCls =
  "w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2 text-sm text-gray-100 " +
  "placeholder:text-gray-700 focus:outline-none focus:ring-1 focus:border-violet-500/70 focus:ring-violet-500/20";

const btnPrimary =
  "flex items-center justify-center gap-2 px-5 py-2 rounded-full text-[15px] font-semibold " +
  "bg-white text-black hover:bg-gray-200 disabled:opacity-50 transition-colors";

// Зеркалит `users._MIN_PASSWORD`: пусть человек узнаёт о требовании до запроса.
const MIN_PASSWORD = 10;

async function authRequest(path: string, body: unknown): Promise<DeviceAccount> {
  const res = await fetch(`/api/auth/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const err = await res.json();
      detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    } catch {}
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return res.json();
}

export function AuthScreen({ overlay, onClose }: { overlay?: boolean; onClose?: () => void }) {
  const { accounts } = useAuth();
  const [view, setView] = useState<"chooser" | "login">(
    overlay || !accounts.length ? "login" : "chooser");

  // Нужна ли первичная настройка. `null` = ещё не знаем — и тогда НЕ рисуем ни
  // одной формы: смена «Создать владельца» на «Вход» на первом запуске читается
  // как сбой панели.
  // В overlay-режиме мы уже внутри панели: владелец по определению создан,
  // спрашивать сервер незачем (и лишней задержки при открытии не нужно).
  const [bootstrap, setBootstrap] = useState<boolean | null>(overlay ? false : null);
  useEffect(() => {
    if (overlay) return;
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/api/auth/state");
        const data = res.ok ? await res.json() : null;
        if (alive) setBootstrap(!!data?.bootstrap);
      } catch {
        // Сервер недоступен — показываем вход: попытка входа объяснит настоящую
        // причину, а форма создания владельца на рабочей установке была бы ложью.
        if (alive) setBootstrap(false);
      }
    })();
    return () => { alive = false; };
  }, [overlay]);

  const done = (acc: DeviceAccount) => {
    addAccount(acc);
    onClose?.();
  };

  const shell = (
    <div className="w-full max-w-sm">
      <div className="flex flex-col items-center gap-2 mb-6">
        <span className="w-16 h-16 rounded-2xl bg-gradient-to-r from-[#8b5cf6] via-[#3b82f6] to-[#ec4899] text-white grid place-items-center shadow-lg shadow-violet-900/40">
          <Server size={32} />
        </span>
        <p className="text-2xl font-bold text-white mt-1">Node Assistant</p>
      </div>

      {bootstrap === null && (
        <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-6 flex items-center justify-center gap-2 text-sm text-gray-500">
          <Loader2 size={15} className="animate-spin" /> Проверяем установку…
        </div>
      )}

      {/* Владельца ещё нет → сохранённые на устройстве токены заведомо мертвы,
          поэтому список аккаунтов не показываем даже если он не пуст. */}
      {bootstrap === true && <BootstrapForm onDone={done} />}

      {bootstrap === false && view === "chooser" && (
        <Chooser
          accounts={accounts}
          onPick={id => { switchTo(id); onClose?.(); }}
          onForget={forget}
          onAdd={() => setView("login")}
          onClose={overlay ? onClose : undefined}
        />
      )}
      {bootstrap === false && view === "login" && (
        <LoginForm
          onDone={done}
          onBack={accounts.length ? () => setView("chooser") : undefined}
        />
      )}
    </div>
  );

  if (overlay) {
    // Portal to <body>: the topbar (where AccountMenu lives) has backdrop-filter,
    // which would otherwise make this fixed overlay's containing block the 52px
    // header instead of the viewport — pinning the form to the top and clipping
    // the scrim to the header strip. Rendering into <body> escapes that.
    return createPortal(
      // Solid full-screen backdrop; click anywhere outside the form closes it
      // (no explicit close button by design).
      <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
        style={{ background: "var(--bg0)" }}
        onMouseDown={e => { if (e.target === e.currentTarget) onClose?.(); }}>
        {shell}
      </div>,
      document.body,
    );
  }

  return <div className="fixed inset-0 flex items-center justify-center p-4" style={{ background: "var(--bg0)" }}>{shell}</div>;
}

// ── Account chooser ───────────────────────────────────────────
function Chooser({ accounts, onPick, onForget, onAdd, onClose }: {
  accounts: DeviceAccount[];
  onPick: (id: string) => void;
  onForget: (id: string) => void;
  onAdd: () => void;
  onClose?: () => void;
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 flex flex-col gap-2">
      <p className="text-xs text-gray-500 px-1 mb-1">Выберите пользователя</p>
      {accounts.map(a => (
        <div key={a.id} className="group flex items-center gap-3 rounded-lg border border-gray-800 hover:border-gray-600 bg-gray-950/40 px-3 py-2.5 transition-colors">
          <button onClick={() => onPick(a.id)} className="flex items-center gap-3 flex-1 min-w-0 text-left">
            <span className="w-8 h-8 rounded-full bg-violet-500/20 text-violet-300 grid place-items-center text-sm font-semibold flex-none">
              {a.login.slice(0, 1).toUpperCase()}
            </span>
            <span className="text-sm text-gray-100 truncate">{a.login}</span>
          </button>
          <button onClick={() => onForget(a.id)} title="Удалить с устройства"
            className="p-1.5 rounded text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition">
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button onClick={onAdd}
        className="flex items-center gap-2 rounded-lg border border-dashed border-gray-700 hover:border-violet-500/60 px-3 py-2.5 text-sm text-gray-400 hover:text-violet-300 transition-colors">
        <LogIn size={15} /> Войти другим пользователем
      </button>
      {onClose && (
        <button onClick={onClose} className="text-xs text-gray-600 hover:text-gray-400 mt-1">Отмена</button>
      )}
    </div>
  );
}

// ── Login ─────────────────────────────────────────────────────
function LoginForm({ onDone, onBack }: {
  onDone: (a: DeviceAccount) => void; onBack?: () => void;
}) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!login.trim() || !password) { setErr("Введите логин и пароль"); return; }
    setBusy(true); setErr("");
    try {
      onDone(await authRequest("login", { login: login.trim(), password }));
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-6 flex flex-col gap-4">
      <div className="flex items-center gap-2">
        {onBack && <button onClick={onBack} className="p-1 -ml-1 rounded text-gray-500 hover:text-gray-200"><ArrowLeft size={15} /></button>}
        <p className="text-sm font-semibold text-white">Вход в аккаунт</p>
      </div>
      <input className={inputCls} placeholder="Логин" autoFocus value={login}
        onChange={e => setLogin(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
      <input className={inputCls} placeholder="Пароль" type="password" value={password}
        onChange={e => setPassword(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
      {err && <p className="text-xs text-red-400">{err}</p>}
      <button className={btnPrimary} onClick={submit} disabled={busy}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />} Войти
      </button>
      {/* Ссылки на регистрацию нет: пользователей заводит владелец в
          «Настройки → Пользователи», самостоятельная регистрация удалена. */}
      <p className="text-[11px] text-gray-600 text-center leading-snug">
        Доступ выдаёт владелец установки.
      </p>
    </div>
  );
}

// ── Первичная настройка (создание владельца) ──────────────────
function BootstrapForm({ onDone }: { onDone: (a: DeviceAccount) => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [token, setToken] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const [generated, setGenerated] = useState(false);

  const gen = () => {
    const pw = generatePassword();
    setPassword(pw);
    // Подтверждение заполняем тоже: сгенерированный пароль не набирали руками,
    // опечатки в нём невозможны, а перепечатывать 20 символов — мучение.
    setConfirm(pw);
    setCopied(false);
    setGenerated(true);
  };
  const copy = async () => {
    if (!password) return;
    try { await navigator.clipboard.writeText(password); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  };

  const submit = async () => {
    if (!login.trim() || !password) { setErr("Введите логин и пароль"); return; }
    if (password.length < MIN_PASSWORD) { setErr(`Пароль короче ${MIN_PASSWORD} символов`); return; }
    if (password !== confirm) { setErr("Пароли не совпадают"); return; }
    setBusy(true); setErr("");
    try {
      onDone(await authRequest("bootstrap", {
        login: login.trim(), password, bootstrap_token: token.trim(),
      }));
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-6 flex flex-col gap-4">
      <p className="text-sm font-semibold text-white">Первичная настройка</p>
      <p className="text-[11px] text-gray-500 -mt-2 leading-snug">
        Пользователей в установке ещё нет. Создайте владельца — он получает полный
        доступ и заводит остальных в «Настройки → Пользователи».
      </p>
      <input className={inputCls} placeholder="Логин" autoFocus value={login}
        onChange={e => setLogin(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
      <div className="flex flex-col gap-1.5">
        <div className="flex gap-2">
          <input className={inputCls} placeholder="Пароль" type="text" value={password}
            onChange={e => { setPassword(e.target.value); setGenerated(false); }}
            onKeyDown={e => e.key === "Enter" && submit()} spellCheck={false} />
          <button onClick={copy} disabled={!password} title="Скопировать"
            className="flex-none px-2.5 rounded-md border border-gray-700 text-gray-400 hover:text-white disabled:opacity-40">
            {copied ? <Check size={15} className="text-green-400" /> : <Copy size={15} />}
          </button>
        </div>
        <button onClick={gen} className="self-start flex items-center gap-1.5 text-xs text-gray-500 hover:text-violet-300">
          <KeyRound size={12} /> Сгенерировать пароль
        </button>
        {generated && (
          <p className="text-[11px] text-amber-400/90 leading-snug">
            Пароль нигде не сохраняется — скопируйте его сейчас, иначе восстановить будет нельзя.
          </p>
        )}
      </div>
      <input className={inputCls} placeholder="Пароль ещё раз" type="password" value={confirm}
        onChange={e => setConfirm(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />

      {/* Токен свёрнут: `BOOTSTRAP_TOKEN` по умолчанию пуст, и у большинства
          установок поля быть не должно вовсе. */}
      {tokenOpen ? (
        <input className={inputCls} placeholder="Токен первичной настройки" type="password"
          value={token} onChange={e => setToken(e.target.value)}
          onKeyDown={e => e.key === "Enter" && submit()} />
      ) : (
        <button onClick={() => setTokenOpen(true)}
          className="self-start flex items-center gap-1.5 text-xs text-gray-500 hover:text-violet-300">
          <ShieldCheck size={12} /> Установка защищена токеном
        </button>
      )}

      {err && <p className="text-xs text-red-400">{err}</p>}
      <button className={btnPrimary} onClick={submit} disabled={busy}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />} Создать владельца
      </button>
    </div>
  );
}
