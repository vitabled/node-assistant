// Субъективные оценки хостинга (1.0..100.0): словарь метрик, цвет цифры и
// средний балл. Чистый модуль по тем же соображениям, что и `search.ts`:
// сортировку и раскраску каталога можно проверить без рендера.

import type { Hosting, HostingMetrics, MetricKey } from "./api";

/** `short` — для строки метрик на карточке: «Удобство панели» туда не влезает. */
export interface MetricDef { key: MetricKey; label: string; short: string }

export const METRIC_DEFS: MetricDef[] = [
  { key: "price", label: "Цена", short: "Цена" },
  { key: "quality", label: "Качество", short: "Качество" },
  { key: "loyalty", label: "Лояльность", short: "Лояльность" },
  { key: "fairuse", label: "Fair use", short: "Fair use" },
  { key: "panel", label: "Удобство панели", short: "Панель" },
  { key: "ru_access", label: "Доступность в РФ", short: "РФ" },
];

/** Цвет «не оценено» — тот же токен, что у прочего второстепенного текста. */
const NEUTRAL_INK = "var(--t-low)";

/**
 * Цвет цифры по величине оценки: 1 — красный, 100 — зелёный.
 *
 * Светлота НЕ постоянная, и это главное здесь. Жёлто-зелёная середина спектра
 * сама по себе намного ярче красного: при одинаковой светлоте она сливается со
 * светлой темой, а тёмно-зелёный — с тёмной. Кривая подобрана так, чтобы
 * относительная яркость всех оттенков держалась около 0.2 — тогда одна и та же
 * цифра читается на обеих подложках, и отдельный цвет под тему не нужен.
 */
export function metricColor(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return NEUTRAL_INK;
  const u = Math.min(1, Math.max(0, (v - 1) / 99));
  const hue = u * 120;
  const light = 64 * u * u - 88 * u + 57; // 57% у красного → ~27% у жёлтого → 33% у зелёного
  return `hsl(${hue.toFixed(1)}, 72%, ${light.toFixed(1)}%)`;
}

/** Оценки хранятся с шагом 0.1 — показываем ровно один знак. */
export const fmtScore = (v: number) => v.toFixed(1);

/** Записи, созданные до появления метрик, приходят без ключа. */
export function metricsOf(h: Hosting): HostingMetrics {
  return h.metrics || {};
}

/**
 * Учитываемая оценка метрики: `null` — «не оценено». Скрытый fair use не
 * учитывается нигде (ни в среднем, ни в фильтре, ни на карточке) — иначе
 * провайдер без такой политики выглядел бы просто неоценённым по ней.
 */
export function scoreOf(m: HostingMetrics | null | undefined, key: MetricKey): number | null {
  if (!m) return null;
  if (key === "fairuse" && m.fairuse_hidden) return null;
  const v = m[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Средний балл по заполненным метрикам; `null`, если не оценено ничего. */
export function avgScore(m: HostingMetrics | null | undefined): number | null {
  const vals = METRIC_DEFS
    .map(d => scoreOf(m, d.key))
    .filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}
