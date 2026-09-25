// Скриншоты NA в дизайн-системе Axottle — по осям панели: палитра × режим × стиль.
//
// Панель Axottle держит оформление тремя осями (data-axo-palette / data-axo-mode / data-style);
// NA повторяет их своими атрибутами (data-palette / data-theme / data-style) плюс скин
// data-skin="axottle". Скрипт засевает эти же ключи в localStorage ДО первого кадра и снимает
// экраны, поэтому на снимках ровно то, что увидит владелец после выбора палитры.
//
// Запуск:  npx vite &   затем   node tests/e2e/axottle-shots.mjs [baseURL] [outDir]
// Требует @playwright/test (chromium). Харнесс визуальный: бэкенд замокан, это не e2e.

import { chromium } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { apiStub } from "./theme-shots.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://localhost:5173";
const outDir = process.argv[3] || join(__dirname, "shots", "axottle");
mkdirSync(outDir, { recursive: true });

// ⚠️ @playwright/test в этом дереве ищет СВОЮ ревизию chromium (1228), а в кэше лежит
// другая (1243) — launch падает с «Executable doesn't exist», хотя браузер есть.
// Поэтому бинарь берём из кэша по факту: сначала headless-shell, затем полный chrome.
function findChromium() {
  const cache = "/root/.cache/ms-playwright";
  if (!existsSync(cache)) return undefined;
  const dirs = readdirSync(cache).sort().reverse();
  for (const want of ["chromium_headless_shell-", "chromium-"]) {
    for (const d of dirs.filter(x => x.startsWith(want))) {
      for (const rel of ["chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux64/chrome", "chrome-linux/chrome"]) {
        const p = join(cache, d, rel);
        if (existsSync(p)) return p;
      }
    }
  }
  return undefined;
}
const CHROMIUM = findChromium();

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };

async function seed(context, { skin, mode, palette, style, tab, accent }) {
  await context.addInitScript(
    ([acct, s, m, p, st, t, a]) => {
      localStorage.setItem("ni_accounts", JSON.stringify([acct]));
      localStorage.setItem("ni_active_account", acct.id);
      localStorage.setItem("ni_skin_" + acct.id, s);
      localStorage.setItem("ni_thememode_" + acct.id, m);
      localStorage.setItem("ni_palette_" + acct.id, p);
      localStorage.setItem("ni_style_" + acct.id, st);
      if (t) localStorage.setItem("ni_tab_" + acct.id, t);
      // nil — «акцент не выбран явно», значит побеждает акцент палитры (как в панели).
      if (a) localStorage.setItem("ni_accent", a);
    },
    [ACCT, skin, mode, palette, style, tab || "", accent || ""],
  );
}

async function shoot(page, name) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(outDir, name + ".png") });
  console.log("  shot:", name);
}

const DESKTOP = { width: 1440, height: 1200 };
const MOBILE = { width: 390, height: 844 };

async function openPage(browser, opts) {
  const context = await browser.newContext({ viewport: opts.viewport || DESKTOP });
  await seed(context, { ...opts, skin: opts.skin || "axottle" });
  await context.route("**/api/**", route => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) });
  });
  const page = await context.newPage();
  page.on("console", m => { if (m.type() === "error") console.log("  [console.error]", m.text().slice(0, 140)); });
  await page.goto(baseURL, { waitUntil: "commit" });
  await page.waitForSelector(".ni-sidebar", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(700);
  return { context, page };
}

async function run() {
  const browser = await chromium.launch(CHROMIUM ? { executablePath: CHROMIUM } : {});
  console.log("chromium:", CHROMIUM || "(default revision)");
  const MATRIX = [
    { mode: "dark",  palette: "blush", style: "cloud" },
    { mode: "light", palette: "blush", style: "cloud" },
    { mode: "dark",  palette: "mint",  style: "cloud" },
    { mode: "light", palette: "mint",  style: "cloud" },
    { mode: "dark",  palette: "blush", style: "sharp" },
  ];

  for (const m of MATRIX) {
    const tag = `${m.palette}-${m.mode}-${m.style}`;
    console.log("desktop:", tag);
    let { context, page } = await openPage(browser, { ...m, tab: "dashboard" });
    await shoot(page, `dashboard-${tag}`);
    // Проверяем, что оси реально доехали до <html> (иначе снимок — не доказательство).
    const attrs = await page.evaluate(() => JSON.stringify({
      skin: document.documentElement.dataset.skin,
      palette: document.documentElement.dataset.palette,
      style: document.documentElement.dataset.style,
      theme: document.documentElement.dataset.theme,
      accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
    }));
    console.log("  attrs:", attrs);
    await context.close();

    ({ context, page } = await openPage(browser, { ...m, tab: "infra-providers" }));
    await shoot(page, `table-${tag}`);
    await context.close();
  }

  // Settings → Тема: сами переключатели палитры и стиля.
  console.log("desktop: settings-theme");
  const { context, page } = await openPage(browser, { mode: "dark", palette: "blush", style: "cloud", tab: "settings" });
  await page.getByRole("button", { name: "Тема" }).click().catch(() => {});
  await page.waitForTimeout(400);
  await shoot(page, "settings-theme");
  await context.close();

  // Мобильный проход — та же палитра, узкий экран.
  console.log("mobile: blush-dark-cloud");
  const mob = await openPage(browser, { mode: "dark", palette: "blush", style: "cloud", tab: "dashboard", viewport: MOBILE });
  await shoot(mob.page, "mobile-dashboard-blush-dark-cloud");
  await mob.context.close();

  await browser.close();
  console.log("done →", outDir);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch(e => { console.error(e); process.exit(1); });
}
