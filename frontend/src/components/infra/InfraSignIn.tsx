import { useState } from "react";
import { LogIn, Loader2, ShieldCheck, LogOut } from "lucide-react";
import { infraApi, session } from "./api";
import { toast } from "./Toast";
import { Page, PageHeader, inputCls } from "./ui";

// Sign-in verifies the finance PIN and stores a session token (sessionStorage),
// which the api client sends as X-Billing-Session to unlock Payments / API tokens.
export function InfraSignIn() {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [unlocked, setUnlocked] = useState(!!session.get());

  const verify = async () => {
    setBusy(true);
    try {
      const r = await infraApi.verifySession(pin);
      session.set(r.token);
      setUnlocked(true); setPin("");
      toast("Доступ к финансовому контуру открыт", "success");
    } catch (e) { toast((e as Error).message, "error"); }
    setBusy(false);
  };

  const logout = () => { session.clear(); setUnlocked(false); toast("Сессия финансового контура закрыта", "info"); };

  return (
    <Page>
      <PageHeader icon={<LogIn size={16} className="text-blue-400" />} title="Sign-in — финансовый контур"
        subtitle="Защищённый вход в разделы Платежи и API токены" />

      <div className="max-w-sm">
        {unlocked ? (
          <div className="rounded-xl border border-green-800/50 bg-green-950/30 p-6 flex flex-col items-center gap-3 text-center">
            <ShieldCheck size={28} className="text-green-400" />
            <p className="text-sm text-green-300">Доступ открыт. Разделы Платежи и API токены разблокированы.</p>
            <button onClick={logout} className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700">
              <LogOut size={13} /> Закрыть сессию
            </button>
          </div>
        ) : (
          <div className="rounded-xl border border-gray-800 bg-gray-900/40 p-6 flex flex-col gap-4">
            <p className="text-xs text-gray-500">
              Введите PIN финансового администратора. Если PIN не задан (раздел Настройки), доступ открыт по умолчанию.
            </p>
            <input type="password" value={pin} onChange={e => setPin(e.target.value)}
              onKeyDown={e => e.key === "Enter" && verify()} placeholder="PIN" autoFocus className={inputCls} />
            <button onClick={verify} disabled={busy}
              className="flex items-center justify-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />} Войти
            </button>
          </div>
        )}
      </div>
    </Page>
  );
}
