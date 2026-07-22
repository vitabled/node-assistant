// Измерительный харнесс производительности (Волна 6, План F Ф1).
//
// Задача — числа, а не ощущения: каждая последующая фаза Плана F обязана
// показать «было/стало» на этих же сценариях.
//
// Usage:
//   node tests/e2e/perf-probe.mjs [baseURL] [--label NAME]   — измерить и записать
//   node tests/e2e/perf-probe.mjs --bundle [distDir]         — только вес бандла
//   node tests/e2e/perf-probe.mjs --compare a.json b.json    — две колонки + дельта
//
// baseURL по умолчанию http://localhost:5173 (сначала подними `npx vite`).
// Результат: таблица в stdout + JSON в tests/e2e/shots/perf/<label>.json.
//
// Требует @playwright/test (chromium). На машине без Node это гоняется в
// контейнере — см. CLAUDE.md, раздел про проверку фронтенда.
//
// ⚠️ КАК ЧИТАТЬ ЧИСЛА
// * Меряется vite-dev, а не прод-сборка: React в StrictMode монтирует эффекты
//   ДВАЖДЫ, поэтому wsInstances/framesDelivered в сценарии A ровно вдвое больше
//   «настоящих» (3 карточки → 6 сокетов, 3x2001 → 12006 рамок). Для сравнения
//   «было/стало» это неважно — множитель одинаков в обоих прогонах, — но
//   абсолютные числа не выдавать за продовые.
// * Абсолютные миллисекунды зависят от машины. Значим только относительный
//   сдвиг между двумя запусками на ОДНОЙ машине (режим --compare).

import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, "shots", "perf");

const ACCT = { id: "perf-acc", login: "perf", token: "perf-token" };

// ── Сценарий B: крупный, но правдоподобный статус-пейдж ──
const NODES = 40;
const BARS = 90;

// Данные ОБЯЗАНЫ меняться от тика к тику. С идентичным ответом React ничего не
// перерисовывает, счётчик мутаций даёт ноль, и сценарий «доказывает», что
// дэшборд бесплатен. Живой поллинг приносит новый ts и меняющиеся статусы —
// воспроизводим это.
let tick = 0;

function statuspage() {
  tick++;
  const nodes = Array.from({ length: NODES }, (_, i) => ({
    stableId: `n${i}`,
    name: `node-${i}`,
    groupName: ["DE", "NL", "FI", "US"][i % 4],
    online: (i + tick) % 7 !== 0,
    latencyMs: 40 + ((i + tick * 3) % 30),
    protocol: "vless",
    uptime30d: 99 - (i % 5),
    // Окно баров сдвигается на каждый тик — как в жизни, где приходит новая
    // точка, а самая старая уходит.
    bars: Array.from({ length: BARS }, (_, j) => ({
      ts: 1700000000 + (j + tick) * 60,
      status: (j + tick) % 23 === 0 ? "down" : (j + tick) % 11 === 0 ? "slow" : "up",
    })),
  }));
  return {
    container: "running",
    global: { state: "ok", total: NODES, online: nodes.filter(n => n.online).length,
              uptime30d: 99.1, protocols: ["vless"] },
    nodes,
  };
}

function stub(url) {
  if (url.includes("/checker/statuspage")) return statuspage();
  if (url.includes("/checker/incidents")) return [];
  if (url.includes("/settings")) return { remnawave: {}, deploy_defaults: {}, optimization: {}, appearance: {} };
  if (url.includes("/subscriptions/status")) return [];
  return [];
}

// ── Инструментовка страницы ──
// Ставится ДО загрузки приложения: считает long-task'и, кадры и — главное —
// подменяет WebSocket, чтобы реплей лога был детерминированным и не зависел
// от живого бэкенда.
function instrument(logFrames) {
  window.__lt = { total: 0, max: 0, first: 0, last: 0 };
  new PerformanceObserver(list => {
    for (const e of list.getEntries()) {
      window.__lt.total += e.duration;
      if (e.duration > window.__lt.max) window.__lt.max = e.duration;
      if (!window.__lt.first) window.__lt.first = performance.now();
      window.__lt.last = performance.now() + e.duration;
    }
  }).observe({ entryTypes: ["longtask"] });

  window.__ws = { instances: 0, frames: 0, firstFrameAt: 0 };

  // Точная копия ТЕКУЩЕГО поведения сервера: одна WS-рамка на строку лога,
  // каждая — отдельной задачей. Именно это Ф2 будет схлопывать в одну рамку,
  // поэтому фейк обязан воспроизводить «до», а не желаемое «после».
  const Real = window.WebSocket;
  class FakeWS {
    constructor(url, protocols) {
      // ТОЛЬКО сокет журнала задач. Глобальная подмена ломает HMR-сокет vite,
      // из-за чего модульный граф не догружается и приложение вообще не
      // монтируется (проверено на себе).
      if (!String(url).includes("/ws/logs/")) return new Real(url, protocols);
      this.url = url;
      this.readyState = 1;
      window.__ws.instances++;
      setTimeout(() => {
        this.onopen && this.onopen({});
        const send = (obj) => {
          if (!window.__ws.firstFrameAt) window.__ws.firstFrameAt = performance.now();
          window.__ws.frames++;
          this.onmessage && this.onmessage({ data: JSON.stringify(obj) });
        };
        send({ type: "status", step: 3, total: 14, status: "running" });
        for (let i = 0; i < logFrames; i++) {
          setTimeout(() => send({ type: "log", line: `[${i}] строка журнала развёртывания, довольно длинная как в жизни` }), 0);
        }
      }, 0);
    }
    close() { this.readyState = 3; }
    send() {}
    addEventListener() {}
    removeEventListener() {}
  }
  FakeWS.OPEN = Real.OPEN; FakeWS.CLOSED = Real.CLOSED;
  window.WebSocket = FakeWS;

  // Счётчик атрибутных мутаций — прокси стоимости перерисовки дэшборда.
  window.__mut = 0;
  window.__startMut = () => {
    new MutationObserver(rs => { window.__mut += rs.length; })
      .observe(document.body, { attributes: true, subtree: true, childList: true });
  };

  // Сэмплер кадров: доля кадров дольше 32 мс во время прокрутки.
  window.__frames = [];
  window.__startFrames = () => {
    let prev = performance.now();
    const tick = (t) => { window.__frames.push(t - prev); prev = t; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  };
}

async function newCtx(browser, { tab, jobs = [], logFrames = 0 }) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(([acct, t, js]) => {
    localStorage.setItem("ni_accounts", JSON.stringify([acct]));
    localStorage.setItem("ni_active_account", acct.id);
    localStorage.setItem("ni_tab_" + acct.id, t);
    localStorage.setItem("deploy_jobs_" + acct.id, JSON.stringify(js));
  }, [ACCT, tab, jobs]);
  await ctx.addInitScript(instrument, logFrames);
  await ctx.route("**/api/**", r =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stub(r.request().url())) }));
  const page = await ctx.newPage();
  await page.goto(baseURL, { waitUntil: "commit" });
  // НЕ глотать сбой загрузки. Харнесс, который отчитывается нулями по
  // незагрузившейся странице, «докажет» любую оптимизацию — это хуже, чем его
  // отсутствие. (Так и случилось на первом прогоне: vite отдавал 403
  // «host is not allowed», а метрики бодро печатали нули.)
  //
  // Ждём с повтором: vite-dev может сделать полный reload (optimize-deps), и
  // тогда одиночный waitForSelector падает не по таймауту, а с уничтожением
  // контекста исполнения.
  let mounted = false;
  for (let i = 0; i < 3 && !mounted; i++) {
    mounted = await page.waitForSelector(".ni-sidebar", { timeout: 20000 })
      .then(() => true).catch(() => false);
  }
  if (!mounted) {
    const d = await page.evaluate(() => ({
      present: !!document.querySelector(".ni-sidebar"),
      text: document.body.innerText.slice(0, 200),
    })).catch(() => ({ text: "<evaluate failed>" }));
    throw new Error(`Приложение не смонтировалось на ${baseURL}: ${JSON.stringify(d).slice(0, 400)}`);
  }
  return { ctx, page };
}

// ── A: реплей лога развёртывания ──
async function scenarioA(browser) {
  const jobs = Array.from({ length: 3 }, (_, i) => ({
    taskId: `task-${i}`, domain: `n${i}.example.com`, ip: `10.0.0.${i + 1}`,
    newSshPort: 2222, startedAt: Date.now(),
    savedForm: { mode: "remnanode", ip: `10.0.0.${i + 1}`, domain: `n${i}.example.com` },
    // finalStatus НЕ задан: карточка считается активной и открывает поток —
    // именно этот случай Ф2 будет сужать.
  }));
  const { ctx, page } = await newCtx(browser, { tab: "deploy", jobs, logFrames: 2000 });
  await page.waitForTimeout(8000); // дать реплею отработать
  // Сценарий бессмыслен, если карточки не открыли поток: измерять было бы
  // нечего, а ноль выглядел бы как «уже быстро».
  const opened = await page.evaluate(() => window.__ws.instances);
  if (!opened) throw new Error("A: ни одна карточка не открыла WebSocket — сценарий ничего не мерит");
  const m = await page.evaluate(() => ({
    wsInstances: window.__ws.instances,
    framesDelivered: window.__ws.frames,
    longTaskTotalMs: Math.round(window.__lt.total),
    longTaskMaxMs: Math.round(window.__lt.max),
    msToSettled: window.__lt.last && window.__ws.firstFrameAt
      ? Math.round(window.__lt.last - window.__ws.firstFrameAt) : 0,
  }));
  await ctx.close();
  return m;
}

// ── B: перерисовка и прокрутка дэшборда ──
async function scenarioB(browser) {
  const { ctx, page } = await newCtx(browser, { tab: "dashboard" });
  await page.waitForTimeout(1500);
  await page.evaluate(() => { window.__mut = 0; window.__startMut(); });
  await page.waitForTimeout(25000); // >= 2 тика поллинга (10 c)
  const attrWrites = await page.evaluate(() => window.__mut);
  if (!attrWrites) throw new Error("B: дэшборд не перерисовался за 25 c — сценарий ничего не мерит");

  await page.evaluate(() => { window.__frames = []; window.__startFrames(); });
  for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, 400); await page.waitForTimeout(120); }
  const frames = await page.evaluate(() => window.__frames);
  const jank = frames.filter(f => f > 32).length;
  const lt = await page.evaluate(() => Math.round(window.__lt.total));
  await ctx.close();
  return {
    attrWrites,
    framesSampled: frames.length,
    jankFrames: jank,
    jankPct: frames.length ? Math.round((jank / frames.length) * 100) : 0,
    longTaskTotalMs: lt,
  };
}

// ── C: вес бандла ──
function scenarioC(distDir) {
  const assets = join(distDir, "assets");
  if (!existsSync(assets)) return { error: `нет ${assets} — сначала соберите фронтенд` };
  const files = readdirSync(assets);
  const sum = (ext) => files.filter(f => f.endsWith(ext))
    .reduce((a, f) => a + statSync(join(assets, f)).size, 0);
  return { assetCount: files.length, jsBytes: sum(".js"), cssBytes: sum(".css") };
}

function printTable(title, obj) {
  console.log(`\n── ${title}`);
  for (const [k, v] of Object.entries(obj)) console.log(`   ${k.padEnd(20)} ${v}`);
}

function compare(aPath, bPath) {
  const a = JSON.parse(readFileSync(aPath, "utf8"));
  const b = JSON.parse(readFileSync(bPath, "utf8"));
  for (const sc of ["A", "B", "C"]) {
    if (!a[sc] || !b[sc]) continue;
    console.log(`\n── ${sc}   ${aPath} → ${bPath}`);
    for (const k of Object.keys(a[sc])) {
      const x = a[sc][k], y = b[sc][k];
      if (typeof x !== "number" || typeof y !== "number") continue;
      const d = x ? Math.round(((y - x) / x) * 100) : 0;
      console.log(`   ${k.padEnd(20)} ${String(x).padStart(9)} → ${String(y).padStart(9)}   ${d > 0 ? "+" : ""}${d}%`);
    }
  }
}

const argv = process.argv.slice(2);
const baseURL = argv.find(a => a.startsWith("http")) || "http://localhost:5173";

if (argv[0] === "--compare") {
  compare(argv[1], argv[2]);
} else if (argv[0] === "--bundle") {
  const r = scenarioC(argv[1] || join(__dirname, "..", "..", "dist"));
  printTable("C — вес бандла", r);
} else {
  const li = argv.indexOf("--label");
  const label = li >= 0 ? argv[li + 1] : "baseline";
  const browser = await chromium.launch();
  // Прогрев: первый заход в vite-dev запускает optimize-deps и полный reload.
  // Без него первый сценарий мерил бы стоимость сборки зависимостей, а не UI.
  {
    const w = await browser.newContext();
    const p = await w.newPage();
    await p.goto(baseURL, { waitUntil: "commit" }).catch(() => {});
    await p.waitForTimeout(6000);
    await w.close();
  }
  const A = await scenarioA(browser);
  const B = await scenarioB(browser);
  await browser.close();
  const C = scenarioC(join(__dirname, "..", "..", "dist"));
  printTable("A — реплей лога развёртывания (3 карточки × 2000 строк)", A);
  printTable("B — дэшборд (40 узлов × 90 бар)", B);
  printTable("C — вес бандла", C);
  mkdirSync(OUT_DIR, { recursive: true });
  const out = join(OUT_DIR, `${label}.json`);
  writeFileSync(out, JSON.stringify({ A, B, C, at: new Date().toISOString() }, null, 2));
  console.log(`\nЗаписано: ${out}`);
}
