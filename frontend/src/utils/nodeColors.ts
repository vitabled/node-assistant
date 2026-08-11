// Цветовая маркировка нод (Wave-4 PR-9): один цвет у карточки деплоя и у
// строки ноды на дашборде доступности. Хранение — в записи deploy_jobs
// (localStorage, как всё у карточек): поле `color` = ключ пресета.
import { deployJobsKey } from "../auth/store";

export interface NodeColorPreset { key: string; hex: string; label: string }

export const NODE_COLOR_PRESETS: NodeColorPreset[] = [
  { key: "red",     hex: "#F0716E", label: "Красный" },
  { key: "orange",  hex: "#F0B054", label: "Оранжевый" },
  { key: "yellow",  hex: "#E3C341", label: "Жёлтый" },
  { key: "green",   hex: "#3ECF8E", label: "Зелёный" },
  { key: "cyan",    hex: "#38C3D2", label: "Бирюзовый" },
  { key: "blue",    hex: "#4C8DFF", label: "Синий" },
  { key: "violet",  hex: "#9D7BFF", label: "Фиолетовый" },
  { key: "magenta", hex: "#FF4D9D", label: "Маджента" },
];

const HEX = new Map(NODE_COLOR_PRESETS.map(p => [p.key, p.hex]));

export const colorHex = (key?: string | null): string | undefined =>
  key ? HEX.get(key) : undefined;

/** Тинт фона карточки: примесь цвета к поверхности bg2 (color-mix — все скины). */
export function cardTint(hex: string, pct = 7): string {
  return `color-mix(in srgb, ${hex} ${pct}%, var(--bg2))`;
}

export interface ColoredJob {
  taskId: string;
  domain?: string;
  ip?: string;
  color?: string;
  savedForm?: { domain?: string };
}

export function readJobs(): ColoredJob[] {
  try {
    const arr = JSON.parse(localStorage.getItem(deployJobsKey()) || "[]");
    return Array.isArray(arr) ? arr : [];
  } catch { return []; }
}

/** Записать/сбросить цвет карточки деплоя (null — убрать). */
export function setJobColor(taskId: string, colorKey: string | null): void {
  try {
    const key = deployJobsKey();
    const jobs = readJobs();
    const next = jobs.map(j => {
      if (j.taskId !== taskId) return j;
      const c = { ...j };
      if (colorKey) c.color = colorKey;
      else delete c.color;
      return c;
    });
    localStorage.setItem(key, JSON.stringify(next));
  } catch { /* ignore */ }
}

/** Карта «домен/IP ноды → hex цвета» для дашборда доступности. */
export function nodeColorLookup(): (host?: string, ip?: string) => string | undefined {
  const byDomain = new Map<string, string>();
  const byIp = new Map<string, string>();
  for (const j of readJobs()) {
    const hex = colorHex(j.color);
    if (!hex) continue;
    const d = (j.savedForm?.domain || j.domain || "").trim().toLowerCase();
    if (d) byDomain.set(d, hex);
    const ip = (j.ip || "").trim();
    if (ip) byIp.set(ip, hex);
  }
  return (host, ip) => {
    const h = (host || "").trim().toLowerCase();
    if (h && byDomain.has(h)) return byDomain.get(h);
    // имя ноды часто содержит домен («DE node1.example.com»)
    for (const [d, hex] of byDomain) if (h && h.includes(d)) return hex;
    const i = (ip || "").trim();
    if (i && byIp.has(i)) return byIp.get(i);
    return undefined;
  };
}
