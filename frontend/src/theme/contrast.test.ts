/**
 * Контраст палитр — измеряемый гейт (Волна 6, План F Ф1).
 *
 * Читает index.css как ТЕКСТ (а не через рендер): цветовые токены объявлены в
 * пяти статических блоках, поэтому браузер для их проверки не нужен, и тест
 * остаётся частью обычного `npm test`.
 *
 * Тест ЗЕЛЁНЫЙ сегодня: все известные провалы перечислены в `KNOWN_FAILURES` с
 * их измеренными числами. Он ловит НОВЫЕ регрессии, а Ф4/Ф5 вычёркивают строки
 * из списка. Пустой `KNOWN_FAILURES` = контрастная часть Плана F готова.
 */
import { readFileSync, readdirSync } from "node:fs";
import { resolve, join, relative } from "node:path";
import { describe, it, expect } from "vitest";
import { ACCENTS } from "./tweaks";

// От корня проекта, а не от import.meta.url: под vitest модуль грузится по
// vite-URL, и file-схемы там нет.
const CSS = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

// ── WCAG 2.1 relative luminance + contrast ratio ──
function srgb(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

export function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * srgb((n >> 16) & 255) + 0.7152 * srgb((n >> 8) & 255) + 0.0722 * srgb(n & 255);
}

export function ratio(fg: string, bg: string): number {
  const [a, b] = [luminance(fg), luminance(bg)];
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/** Значения токенов внутри одного селекторного блока index.css. */
function block(selector: string): Record<string, string> {
  const i = CSS.indexOf(selector + "{");
  if (i < 0) throw new Error(`палитровый блок не найден: ${selector}`);
  const body = CSS.slice(i + selector.length + 1, CSS.indexOf("}", i));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})/g)) out[m[1]] = m[2];
  return out;
}

// Палитры наследуются: apple/neon переопределяют только часть токенов поверх
// соответствующей базы, ровно как каскад в браузере.
const consoleDark = block(":root");
const consoleLight = { ...consoleDark, ...block(':root[data-theme="light"]') };
const appleLight = { ...consoleLight, ...block(':root[data-skin="apple"][data-theme="light"]') };
const appleDark = { ...consoleDark, ...block(':root[data-skin="apple"]:not([data-theme="light"])') };
const neon = { ...consoleDark, ...block(':root[data-skin="neon"]') };

const PALETTES: Record<string, Record<string, string>> = {
  "console-dark": consoleDark,
  "console-light": consoleLight,
  "apple-light": appleLight,
  "apple-dark": appleDark,
  neon,
};

const INKS = ["--t-faint", "--t-low", "--t-mid", "--t-hi"];
const SURFACES = ["--bg1", "--bg2", "--bg3"];
const AA = 4.5; // обычный текст <18px

/**
 * Известные провалы на момент Ф1, с измеренными коэффициентами. Каждая строка —
 * долг, который снимают Ф4/Ф5. Формат ключа: "<палитра> <fg> on <bg>".
 * Заполняется реальными числами прогона (см. вывод describe ниже).
 */
const KNOWN_FAILURES = new Set<string>([
  // --t-faint задуман как плейсхолдер/disabled, но по факту используется и для
  // контента (Ф5 правит StepProgress), а сам токен перетюнивается в Ф4.
  // Измерено: 1.96–3.32 при пороге 4.5.
  "console-dark --t-faint on --bg1",   // 3.32
  "console-dark --t-faint on --bg2",   // 3.11
  "console-dark --t-faint on --bg3",   // 2.79
  "console-light --t-faint on --bg1",  // 2.26
  "console-light --t-faint on --bg2",  // 2.42
  "console-light --t-faint on --bg3",  // 1.96 — худшее значение во всей матрице
  "apple-light --t-faint on --bg1",    // 2.26
  "apple-light --t-faint on --bg2",    // 2.42
  "apple-light --t-faint on --bg3",    // 1.96
  "apple-dark --t-faint on --bg1",     // 2.92
  "apple-dark --t-faint on --bg2",     // 2.59
  "apple-dark --t-faint on --bg3",     // 2.11
  "neon --t-faint on --bg1",           // 3.32 (neon не переопределяет --t-faint/--bg*)
  "neon --t-faint on --bg2",           // 3.11
  "neon --t-faint on --bg3",           // 2.79

  // НЕ предусмотрено планом — найдено этим измерением: --t-low (вторичный, но
  // «всё ещё читаемый» по замыслу) проваливается на самой светлой поверхности.
  // Ф4 обязана поднять и его, а не только --t-faint.
  "console-light --t-low on --bg3",    // 4.10
  "apple-light --t-low on --bg3",      // 4.10
  "apple-dark --t-low on --bg3",       // 3.96
]);

describe("contrast: ink on surfaces", () => {
  const failures: string[] = [];
  const table: string[] = [];

  for (const [pal, tok] of Object.entries(PALETTES)) {
    for (const ink of INKS) {
      for (const bg of SURFACES) {
        const fg = tok[ink];
        const back = tok[bg];
        if (!fg || !back) continue;
        const r = ratio(fg, back);
        const key = `${pal} ${ink} on ${bg}`;
        table.push(`${key.padEnd(38)} ${r.toFixed(2)}${r < AA ? "  ✗" : ""}`);
        if (r < AA) failures.push(`${key} = ${r.toFixed(2)}`);
      }
    }
  }

  it("prints the measured table", () => {
    console.log("\n" + table.join("\n"));
    expect(table.length).toBeGreaterThan(0);
  });

  it("has no contrast failure outside KNOWN_FAILURES", () => {
    const unexpected = failures.filter(f => !KNOWN_FAILURES.has(f.split(" = ")[0]));
    expect(unexpected).toEqual([]);
  });

  // Обратная сторона: список не должен гнить. Если провал починили, а строку не
  // убрали — тест скажет об этом, и «список пуст» останется честным критерием.
  it("has no stale entry in KNOWN_FAILURES", () => {
    const actual = new Set(failures.map(f => f.split(" = ")[0]));
    expect([...KNOWN_FAILURES].filter(k => !actual.has(k))).toEqual([]);
  });
});

describe("contrast: ink on accents", () => {
  // На акценте лежит либо #fff, либо тёмные чернила самого акцента. Сегодня
  // index.css местами хардкодит #fff.
  //
  // ⚠️ ИЗМЕРЕНО (Ф1) — корректирует посылку Плана F Ф4. План исходил из того,
  // что белое годится для blue/violet/magenta и переворачивать надо только для
  // светлых lime/amber/green/cyan. На самом деле белое проваливает AA на ВСЕХ
  // семи акцентах, а a.ink проходит на всех:
  //   blue    white 3.20 / ink  6.03      cyan    white 2.12 / ink  8.87
  //   green   white 2.00 / ink  9.47      magenta white 3.09 / ink  6.34
  //   violet  white 3.13 / ink  6.23      lime    white 1.21 / ink 15.51
  //   amber   white 1.90 / ink  9.75
  // ⇒ Ф4 должна не «считать порог по светимости», а просто всегда брать a.ink
  //   для текста на сплошном акценте. Порог остаётся полезен только если в
  //   ACCENTS появится тёмный акцент — тогда этот тест сразу это покажет.
  it("prints the measured table for both ink choices", () => {
    const rows = Object.entries(ACCENTS).map(([name, a]) => {
      const white = ratio("#ffffff", a.base);
      const ink = ratio(a.ink, a.base);
      const best = Math.max(white, ink);
      return `${name.padEnd(9)} base=${a.base}  white=${white.toFixed(2)}  ink=${ink.toFixed(2)}  ` +
        `best=${best >= AA ? "OK" : "FAIL"} (${best >= AA ? (white >= ink ? "white" : "ink") : "—"})`;
    });
    console.log("\n" + rows.join("\n"));
    expect(rows.length).toBe(Object.keys(ACCENTS).length);
  });

  // Каждый акцент ОБЯЗАН иметь хотя бы один читаемый вариант чернил — иначе
  // цвет непригоден и его нельзя чинить выбором ink.
  it("gives every accent at least one readable ink", () => {
    for (const [name, a] of Object.entries(ACCENTS)) {
      const best = Math.max(ratio("#ffffff", a.base), ratio(a.ink, a.base));
      expect(`${name}:${best >= AA}`).toBe(`${name}:true`);
    }
  });
});

// ── Хардкод цветов в компонентах (Волна 7, План A Ф3) ──────────
//
// Токены палитры можно измерить, а `text-white` — нельзя: он не участвует ни в
// одном блоке index.css и просто игнорирует тему. Ровно так заголовок «Деплой
// нод» оказался белым по белому в светлой теме. Поэтому — отдельный гейт на
// исходники.
describe("no hardcoded theme-blind colors in components", () => {
  // AuthScreen — намеренно тёмный полноэкранный гейт ДО выбора темы: там нет
  // ни аккаунта, ни его настроек оформления, поэтому токены неприменимы.
  const ALLOW = ["auth/AuthScreen.tsx"];

  // ⚠️ ИЗМЕРЕНО (Ф3) — правило ýже, чем казалось при планировании. Первая,
  // широкая версия регулярки дала 11 «нарушений», из которых настоящим было
  // ОДНО (`bg-blue-600/20 text-blue-300` на аватарке аккаунта). Остальные —
  // два корректных идиома, не зависящих от темы:
  //   • `bg-white` (9 мест) — БЕЛЫЙ КРУЖОК тумблера на цветной дорожке. Он белый
  //     и в светлой, и в тёмной теме, ровно как в iOS; токен тут был бы ошибкой.
  //   • `bg-black/75` — затемняющая подложка модалки (infra/ui.tsx:60).
  // Поэтому white/black пропускаем, а любой ИМЕНОВАННЫЙ ОТТЕНОК палитры
  // Tailwind ловим: он игнорирует и тему, и выбранный акцент.
  const BAD = new RegExp(
    "\\b(?:text|border|from|to|via)-(?:white|black)\\b" +           // текст/рамка белым-чёрным
    "|\\b(?:text|bg|border|from|to|via)-" +                          // любой оттенок палитры
      "(?:gray|slate|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|" +
      "cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\\d{2,3}\\b",
    "g",
  );

  it("finds none outside the allow-list", () => {
    const root = resolve(process.cwd(), "src");
    const hits: string[] = [];

    const walk = (dir: string) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = join(dir, e.name);
        if (e.isDirectory()) { walk(p); continue; }
        if (!e.name.endsWith(".tsx") || e.name.endsWith(".test.tsx")) continue;
        const rel = relative(root, p).replace(/\\/g, "/");
        if (ALLOW.some(a => rel.endsWith(a))) continue;
        const found = readFileSync(p, "utf8").match(BAD);
        if (found) hits.push(`${rel}: ${[...new Set(found)].join(", ")}`);
      }
    };
    walk(root);

    expect(hits).toEqual([]);
  });
});
