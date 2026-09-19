/**
 * YouTube Region — единое представление бейджа во всех карточках NA
 * (развёрнутая карточка ноды, свёрнутая карточка, карточка панели).
 *
 * Значения, которые приходят с бэкенда (`/api/stats/node` → securityStats.ytRegion):
 *   - код региона ("NL", "DE", …) — нода видит YouTube в этом регионе;
 *   - "ads"     — показывается реклама (регион не обходится);
 *   - "unknown" — определить не удалось (нет ни одного инструмента / нет доступа);
 *   - undefined — карточка отрисована без данных (нода недоступна).
 *
 * `unknown` и `undefined` показываем как «—»: бейдж не скрываем никогда.
 */
export type YtTone = "warn" | "ok" | "muted";

export function ytRegionText(yt?: string | null): string {
  if (yt === "ads") return "Реклама";
  if (yt && yt !== "unknown") return yt;
  return "—";
}

export function ytRegionTone(yt?: string | null): YtTone {
  if (yt === "ads") return "warn";
  if (yt && yt !== "unknown") return "ok";
  return "muted";
}

/** Классы бейджа (тёмная тема NA, CSS-переменные). */
export const YT_TONE_CLASS: Record<YtTone, string> = {
  warn: "text-[var(--warn)] bg-[var(--warn-dim)] border-[var(--warn-line)]",
  ok: "text-[var(--ok)] bg-[var(--ok-dim)] border-[var(--ok-line)]",
  muted: "text-[var(--t-mid)] bg-[var(--bg3)] border-[var(--line)]",
};

/** Цвет иконки для инлайн-чипов. */
export const YT_TONE_COLOR: Record<YtTone, string> = {
  warn: "var(--warn)",
  ok: "var(--ok)",
  muted: "var(--t-mid)",
};
