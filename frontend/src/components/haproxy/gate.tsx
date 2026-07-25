import { useCallback, useEffect, useState } from "react";
import { Loader2, ServerCog } from "lucide-react";
import { haproxyApi } from "./api";
import type { HaproxyConnState } from "./contracts";

// ── shared readiness hook + gate ────────────────────────────────
// Every HAPROXY page mounts this to decide whether to show its content or a
// «connect first» prompt. `ready` = configured (local deployed / remote registered)
// AND enabled. One GET /api/haproxy/config, cheap.
//
// The connect/config UI itself now lives in Settings → «HAProxy»
// (components/haproxy/HaproxyConnect.tsx, rendered by Settings.tsx).
export function useHaproxyReady() {
  const [state, setState] = useState<HaproxyConnState | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(async () => {
    setLoading(true);
    try { setState(await haproxyApi.getConfig()); }
    catch { setState(null); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);
  return { state, ready: !!state?.configured && !!state?.enabled, loading, reload };
}

export function NotConnected({ loading }: { loading?: boolean }) {
  return (
    <div className="card p-8 text-center max-w-md mx-auto mt-10">
      {loading ? (
        <Loader2 size={26} className="animate-spin mx-auto text-[var(--t-low)]" />
      ) : (
        <>
          <ServerCog size={30} className="mx-auto text-[var(--t-low)] mb-3" />
          <p className="text-sm font-medium text-[var(--t-hi)] mb-1">Панель NodeFlow не готова</p>
          <p className="text-xs text-[var(--t-low)]">
            Откройте <b>«Настройки» → вкладка «HAProxy»</b>: по умолчанию локальная панель
            разворачивается автоматически. Либо подключите существующую панель.
          </p>
        </>
      )}
    </div>
  );
}
