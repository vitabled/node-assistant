import { useState } from "react";
import {
  LogIn, UserPlus, Loader2, KeyRound, Copy, Check, Server, X, Plus, ArrowLeft, Trash2,
} from "lucide-react";
import { useAuth } from "./useAuth";
import { addAccount, switchTo, forget, generatePassword, type DeviceAccount } from "./store";

const inputCls =
  "w-full bg-gray-900/80 border border-gray-700/80 rounded-md px-3 py-2 text-sm text-gray-100 " +
  "placeholder:text-gray-700 focus:outline-none focus:ring-1 focus:border-blue-500/70 focus:ring-blue-500/20";

const btnPrimary =
  "flex items-center justify-center gap-2 px-4 py-2 rounded-md text-sm font-medium " +
  "bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 transition-colors";

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

type View = "chooser" | "login" | "register";

export function AuthScreen({ overlay, onClose }: { overlay?: boolean; onClose?: () => void }) {
  const { accounts } = useAuth();
  const [view, setView] = useState<View>(overlay ? "login" : accounts.length ? "chooser" : "login");

  const done = (acc: DeviceAccount) => {
    addAccount(acc);
    onClose?.();
  };

  const shell = (
    <div className="w-full max-w-sm">
      <div className="flex flex-col items-center gap-2 mb-6">
        <span className="w-11 h-11 rounded-xl bg-blue-600 text-white grid place-items-center shadow-lg shadow-blue-900/40">
          <Server size={22} />
        </span>
        <p className="text-[15px] font-semibold text-white">Node Installer</p>
        <p className="text-[11px] text-gray-500 tracking-wide">remnawave ops</p>
      </div>

      {view === "chooser" && (
        <Chooser
          accounts={accounts}
          onPick={id => { switchTo(id); onClose?.(); }}
          onForget={forget}
          onAdd={() => setView("login")}
          onClose={overlay ? onClose : undefined}
        />
      )}
      {view === "login" && (
        <LoginForm
          onDone={done}
          onRegister={() => setView("register")}
          onBack={accounts.length ? () => setView("chooser") : undefined}
        />
      )}
      {view === "register" && (
        <RegisterForm onDone={done} onLogin={() => setView("login")} />
      )}
    </div>
  );

  if (overlay) {
    return (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4"
        onMouseDown={e => { if (e.target === e.currentTarget) onClose?.(); }}>
        <div className="relative">
          {onClose && (
            <button onClick={onClose}
              className="absolute -top-2 -right-2 p-1.5 rounded-full bg-gray-800 border border-gray-700 text-gray-400 hover:text-white z-10">
              <X size={14} />
            </button>
          )}
          {shell}
        </div>
      </div>
    );
  }

  return <div className="fixed inset-0 flex items-center justify-center p-4" style={{ background: "var(--bg0, #0b0e14)" }}>{shell}</div>;
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
      <p className="text-xs text-gray-500 px-1 mb-1">Выберите аккаунт</p>
      {accounts.map(a => (
        <div key={a.id} className="group flex items-center gap-3 rounded-lg border border-gray-800 hover:border-gray-600 bg-gray-950/40 px-3 py-2.5 transition-colors">
          <button onClick={() => onPick(a.id)} className="flex items-center gap-3 flex-1 min-w-0 text-left">
            <span className="w-8 h-8 rounded-full bg-blue-600/20 text-blue-300 grid place-items-center text-sm font-semibold flex-none">
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
        className="flex items-center gap-2 rounded-lg border border-dashed border-gray-700 hover:border-blue-500/60 px-3 py-2.5 text-sm text-gray-400 hover:text-blue-300 transition-colors">
        <Plus size={15} /> Добавить аккаунт
      </button>
      {onClose && (
        <button onClick={onClose} className="text-xs text-gray-600 hover:text-gray-400 mt-1">Отмена</button>
      )}
    </div>
  );
}

// ── Login ─────────────────────────────────────────────────────
function LoginForm({ onDone, onRegister, onBack }: {
  onDone: (a: DeviceAccount) => void; onRegister: () => void; onBack?: () => void;
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
      <button onClick={onRegister} className="text-xs text-gray-500 hover:text-blue-300 text-center">
        Нет аккаунта? Регистрация
      </button>
    </div>
  );
}

// ── Register ──────────────────────────────────────────────────
function RegisterForm({ onDone, onLogin }: {
  onDone: (a: DeviceAccount) => void; onLogin: () => void;
}) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);

  const gen = () => {
    const pw = generatePassword();
    setPassword(pw);
    setCopied(false);
  };
  const copy = async () => {
    if (!password) return;
    try { await navigator.clipboard.writeText(password); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  };
  const submit = async () => {
    if (!login.trim() || !password) { setErr("Введите логин и пароль"); return; }
    setBusy(true); setErr("");
    try {
      onDone(await authRequest("register", { login: login.trim(), password }));
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-6 flex flex-col gap-4">
      <p className="text-sm font-semibold text-white">Регистрация</p>
      <p className="text-[11px] text-gray-500 -mt-2">Создаётся новый пустой аккаунт с чистыми настройками.</p>
      <input className={inputCls} placeholder="Логин" autoFocus value={login}
        onChange={e => setLogin(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} />
      <div className="flex flex-col gap-1.5">
        <div className="flex gap-2">
          <input className={inputCls} placeholder="Пароль" type="text" value={password}
            onChange={e => setPassword(e.target.value)} onKeyDown={e => e.key === "Enter" && submit()} spellCheck={false} />
          <button onClick={copy} disabled={!password} title="Скопировать"
            className="flex-none px-2.5 rounded-md border border-gray-700 text-gray-400 hover:text-white disabled:opacity-40">
            {copied ? <Check size={15} className="text-green-400" /> : <Copy size={15} />}
          </button>
        </div>
        <button onClick={gen} className="self-start flex items-center gap-1.5 text-xs text-gray-500 hover:text-blue-300">
          <KeyRound size={12} /> Сгенерировать пароль
        </button>
      </div>
      {err && <p className="text-xs text-red-400">{err}</p>}
      <button className={btnPrimary} onClick={submit} disabled={busy}>
        {busy ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />} Создать и войти
      </button>
      <button onClick={onLogin} className="text-xs text-gray-500 hover:text-blue-300 text-center">
        Уже есть аккаунт? Войти
      </button>
    </div>
  );
}
