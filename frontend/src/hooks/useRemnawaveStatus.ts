import { useEffect, useState } from "react";

export type RwStatus = "loading" | "online" | "offline" | "unconfigured";

/**
 * Живой статус панели Remnawave для топбара. Опрашивает
 * POST /api/settings/remnawave/check — read-only пробу (GET internal-squads)
 * против СОХРАНЁННОЙ конфигурации панели, поэтому чип отражает реальное
 * состояние, а не захардкоженный «онлайн».
 *
 * Коды: 200 → online; 400 → панель не настроена; прочие/сеть → offline.
 * Первый прогон — сразу при монтировании, дальше — каждые `intervalMs`.
 */
export function useRemnawaveStatus(intervalMs = 60_000): RwStatus {
  const [status, setStatus] = useState<RwStatus>("loading");
  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      try {
        const res = await fetch("/api/settings/remnawave/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        if (cancelled) return;
        if (res.ok) setStatus("online");
        else if (res.status === 400) setStatus("unconfigured");
        else setStatus("offline");
      } catch {
        if (!cancelled) setStatus("offline");
      }
    };
    probe();
    const id = setInterval(probe, intervalMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [intervalMs]);
  return status;
}
