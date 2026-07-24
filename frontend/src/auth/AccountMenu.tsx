import { useState, useRef, useEffect } from "react";
import { LogOut, Plus, Trash2, ChevronDown, Check } from "lucide-react";
import { useAuth } from "./useAuth";
import { switchTo, logoutActive, forget } from "./store";
import { AuthScreen } from "./AuthScreen";

// Google-style account switcher for the topbar. Lists accounts already signed
// in on this device (instant switch, no password), plus add / logout / remove.
export function AccountMenu() {
  const { accounts, activeId } = useAuth();
  const active = accounts.find(a => a.id === activeId);
  const others = accounts.filter(a => a.id !== activeId);
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  if (!active) return null;

  const avatar = (login: string, size = 22) => (
    <span className="rounded-full bg-[var(--accent-dim)] text-[var(--accent-hi)] grid place-items-center font-semibold flex-none"
      style={{ width: size, height: size, fontSize: size * 0.45 }}>
      {login.slice(0, 1).toUpperCase()}
    </span>
  );

  return (
    <div style={{ position: "relative" }} ref={ref}>
      <button className="iconbtn" onClick={() => setOpen(v => !v)} title="Аккаунт"
        style={{ display: "flex", alignItems: "center", gap: 6, padding: "3px 8px", width: "auto" }}>
        {avatar(active.login)}
        <span className="trunc" style={{ maxWidth: 120, fontSize: 12.5, color: "var(--t-hi)" }}>{active.login}</span>
        <ChevronDown size={13} style={{ color: "var(--t-low)" }} />
      </button>

      {open && (
        <div className="panel" style={{
          position: "absolute", top: "calc(100% + 8px)", right: 0, zIndex: 60, width: 264,
          padding: 8, boxShadow: "var(--shadow-pop)", display: "flex", flexDirection: "column", gap: 2,
        }}>
          {/* Active account */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px" }}>
            {avatar(active.login, 32)}
            <div style={{ minWidth: 0 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: "var(--t-hi)" }} className="trunc">{active.login}</p>
              <p style={{ fontSize: 10.5, color: "var(--t-low)" }}>активный аккаунт</p>
            </div>
            <Check size={15} style={{ marginLeft: "auto", color: "var(--ok)" }} />
          </div>

          <div style={{ height: 1, background: "var(--line-soft)", margin: "4px 0" }} />

          {/* Switch to another added account */}
          {others.map(a => (
            <div key={a.id} className="navitem" style={{ display: "flex", alignItems: "center", gap: 10, cursor: "default" }}>
              <button onClick={() => { switchTo(a.id); setOpen(false); }}
                style={{ display: "flex", alignItems: "center", gap: 10, flex: 1, minWidth: 0, background: "none", border: 0, cursor: "pointer", padding: 0 }}>
                {avatar(a.login, 26)}
                <span className="trunc" style={{ fontSize: 12.5, color: "var(--t-hi)" }}>{a.login}</span>
              </button>
              <button onClick={() => forget(a.id)} title="Удалить с устройства"
                className="iconbtn" style={{ width: 26, height: 26, flex: "none" }}>
                <Trash2 size={13} />
              </button>
            </div>
          ))}

          <button className="navitem" onClick={() => { setAdding(true); setOpen(false); }}>
            <Plus size={15} style={{ flex: "none" }} /> <span>Добавить аккаунт</span>
          </button>
          <button className="navitem" onClick={() => { logoutActive(); setOpen(false); }}>
            <LogOut size={15} style={{ flex: "none" }} /> <span>Выйти из аккаунта</span>
          </button>
        </div>
      )}

      {adding && <AuthScreen overlay onClose={() => setAdding(false)} />}
    </div>
  );
}
