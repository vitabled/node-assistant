import { describe, it, expect } from "vitest";
import { METRIC_DEFS, metricColor, avgScore, scoreOf, metricsOf, fmtScore } from "./metrics";
import type { Hosting } from "./api";

const parts = (v: number | null | undefined) => {
  const css = metricColor(v);
  const m = /^hsl\(([\d.]+),\s*([\d.]+)%,\s*([\d.]+)%\)$/.exec(css);
  if (!m) throw new Error(`не hsl-цвет: ${css}`);
  return { hue: parseFloat(m[1]), sat: parseFloat(m[2]), light: parseFloat(m[3]) };
};
const hue = (v: number) => parts(v).hue;

describe("metricColor", () => {
  it("идёт от красного к зелёному строго монотонно", () => {
    const hues = [1, 5, 20, 40, 50, 70, 90, 99, 100].map(hue);
    for (let i = 1; i < hues.length; i++) expect(hues[i]).toBeGreaterThan(hues[i - 1]);
  });

  it("держит границы диапазона: 1 — красный, 100 — зелёный", () => {
    expect(hue(1)).toBe(0);
    expect(hue(100)).toBe(120);
  });

  it("зажимает значения вне 1..100 вместо выхода за круг оттенков", () => {
    expect(hue(0)).toBe(0);
    expect(hue(-50)).toBe(0);
    expect(hue(1000)).toBe(120);
  });

  // Смысл кривой светлоты: цифра должна читаться и на светлой, и на тёмной
  // подложке, поэтому крайности (почти белый / почти чёрный) недопустимы.
  it("держит светлоту в читаемом коридоре на всём диапазоне", () => {
    for (let v = 1; v <= 100; v++) {
      const { light, sat } = parts(v);
      expect(light).toBeGreaterThanOrEqual(25);
      expect(light).toBeLessThanOrEqual(60);
      expect(sat).toBeGreaterThan(0);
    }
  });

  it("на «не оценено» отдаёт нейтральный токен, а не цвет", () => {
    expect(metricColor(null)).toBe("var(--t-low)");
    expect(metricColor(undefined)).toBe("var(--t-low)");
    expect(metricColor(NaN)).toBe("var(--t-low)");
  });
});

describe("fmtScore", () => {
  it("всегда показывает один знак после запятой", () => {
    expect(fmtScore(7)).toBe("7.0");
    expect(fmtScore(83.5)).toBe("83.5");
  });
});

describe("scoreOf", () => {
  it("не отдаёт скрытый fair use", () => {
    expect(scoreOf({ fairuse: 42 }, "fairuse")).toBe(42);
    expect(scoreOf({ fairuse: 42, fairuse_hidden: true }, "fairuse")).toBeNull();
  });

  it("отличает «не оценено» от нуля и мусора", () => {
    expect(scoreOf({}, "price")).toBeNull();
    expect(scoreOf({ price: null }, "price")).toBeNull();
    expect(scoreOf({ price: NaN }, "price")).toBeNull();
    expect(scoreOf(undefined, "price")).toBeNull();
  });
});

describe("avgScore", () => {
  it("усредняет только заполненные метрики", () => {
    expect(avgScore({ price: 80, quality: 60 })).toBe(70);
  });

  it("игнорирует скрытый fair use", () => {
    expect(avgScore({ price: 80, fairuse: 20 })).toBe(50);
    expect(avgScore({ price: 80, fairuse: 20, fairuse_hidden: true })).toBe(80);
  });

  it("пустые метрики → null", () => {
    expect(avgScore({})).toBeNull();
    expect(avgScore(undefined)).toBeNull();
    expect(avgScore({ fairuse: 50, fairuse_hidden: true })).toBeNull();
  });
});

describe("metricsOf", () => {
  // Записи, сохранённые до появления метрик, приходят без ключа.
  it("переживает хостинг без ключа metrics", () => {
    expect(metricsOf({ name: "Hetzner" } as Hosting)).toEqual({});
    expect(avgScore(metricsOf({ name: "Hetzner" } as Hosting))).toBeNull();
  });
});

describe("METRIC_DEFS", () => {
  it("описывает ровно шесть метрик бэкенд-контракта", () => {
    expect(METRIC_DEFS.map(d => d.key)).toEqual(
      ["price", "quality", "loyalty", "fairuse", "panel", "ru_access"]);
  });
});
