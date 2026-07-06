import { useState, useEffect } from "react";
import { CheckCircle2, XCircle, Info, X } from "lucide-react";

// Minimal dependency-free toast system. `toast(text, type)` can be called from
// anywhere; <Toaster/> (mounted once in App) renders the stack.
type ToastType = "success" | "error" | "info";
interface ToastItem { id: number; type: ToastType; text: string }

let items: ToastItem[] = [];
let seq = 0;
const subs = new Set<() => void>();
const emit = () => subs.forEach(fn => fn());

export function toast(text: string, type: ToastType = "info", ttl = 4500) {
  const id = ++seq;
  items = [...items, { id, type, text }];
  emit();
  setTimeout(() => { items = items.filter(i => i.id !== id); emit(); }, ttl);
}

function useToasts(): ToastItem[] {
  const [, force] = useState(0);
  useEffect(() => {
    const cb = () => force(x => x + 1);
    subs.add(cb);
    return () => { subs.delete(cb); };
  }, []);
  return items;
}

const STYLE: Record<ToastType, { cls: string; icon: React.ReactNode }> = {
  success: { cls: "border-[var(--ok-line)] bg-[var(--ok-dim)] text-[var(--ok)]",       icon: <CheckCircle2 size={15} /> },
  error:   { cls: "border-[var(--err-line)] bg-[var(--err-dim)] text-[var(--err)]",    icon: <XCircle size={15} /> },
  info:    { cls: "border-[var(--line)] bg-[var(--bg2)] text-[var(--t-hi)]",           icon: <Info size={15} /> },
};

export function Toaster() {
  const list = useToasts();
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {list.map(t => {
        const s = STYLE[t.type];
        return (
          <div key={t.id}
            className={`flex items-start gap-2.5 px-3.5 py-2.5 rounded-lg border shadow-xl
                        text-sm backdrop-blur ${s.cls} animate-[fadeIn_.15s_ease-out]`}>
            <span className="mt-0.5 shrink-0">{s.icon}</span>
            <span className="flex-1">{t.text}</span>
            <button onClick={() => { items = items.filter(i => i.id !== t.id); emit(); }}
              className="opacity-60 hover:opacity-100"><X size={13} /></button>
          </div>
        );
      })}
    </div>
  );
}
