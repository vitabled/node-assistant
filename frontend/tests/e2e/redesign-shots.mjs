// S4 redesign screenshot harness — remnawave skin × light/dark, desktop 1440 +
// mobile 390. Captures the empty states + skeleton/animations added in S4.
//
// Reuses apiStub from theme-shots.mjs (no backend needed). Requires chromium:
//   npx playwright install chromium
// Usage:
//   node tests/e2e/redesign-shots.mjs [baseURL] [outDir]
//     baseURL defaults to http://localhost:5173 (run `npx vite --host` first)
//     outDir  defaults to tests/e2e/shots/redesign

import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { apiStub } from "./theme-shots.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://localhost:5173";
const outDir = process.argv[3] || join(__dirname, "shots", "redesign");
mkdirSync(outDir, { recursive: true });

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };
const SKIN = "remnawave";

async function seed(context, { mode, tab }) {
  await context.addInitScript(
    ([acct, s, m, t]) => {
      localStorage.setItem("ni_accounts", JSON.stringify([acct]));
      localStorage.setItem("ni_active_account", acct.id);
      localStorage.setItem("ni_skin_" + acct.id, s);
      localStorage.setItem("ni_thememode_" + acct.id, m);
      if (t) localStorage.setItem("ni_tab_" + acct.id, t);
    },
    [ACCT, SKIN, mode, tab || ""],
  );
}

async function openPage(browser, { mode, viewport, tab }) {
  const context = await browser.newContext({ viewport });
  await seed(context, { mode, tab });
  await context.route("**/api/**", route => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) });
  });
  const page = await context.newPage();
  page.on("console", m => { if (m.type() === "error") console.log("  [console.error]", m.text().slice(0, 160)); });
  await page.goto(baseURL, { waitUntil: "commit" });
  await page.waitForSelector(".ni-sidebar", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(900);
  return { context, page };
}

const DESKTOP = { width: 1440, height: 1400 };
const MOBILE = { width: 390, height: 844 };

// Each target: App tab + optional click to reach the S4-relevant view.
const TARGETS = [
  // Dashboard — кликаем внутренний Seg «Server uptime», чтобы снять пустое
  // состояние таблицы серверов (нет серверов → EmptyState + «Добавить сервер»).
  { tab: "dashboard", name: "dashboard-server-empty", click: "Server uptime" },
  { tab: "deploy", name: "deploy-empty" },
  { tab: "f2b-list", name: "f2b-empty" },
  { tab: "stats-users", name: "stats-empty" },
];

async function shoot(browser, { mode, viewport, prefix, t }) {
  const { context, page } = await openPage(browser, { mode, viewport, tab: t.tab });
  if (t.click) await page.getByRole("button", { name: t.click }).click().catch(() => {});
  await page.waitForTimeout(400);
  const name = `${prefix}-${t.name}-${mode}`;
  await page.screenshot({ path: join(outDir, name + ".png") });
  console.log("  shot:", name);
  await context.close();
}

async function run() {
  const browser = await chromium.launch();
  for (const mode of ["dark", "light"]) {
    for (const t of TARGETS) {
      await shoot(browser, { mode, viewport: DESKTOP, prefix: "d", t });
    }
    for (const t of TARGETS) {
      await shoot(browser, { mode, viewport: MOBILE, prefix: "m", t });
    }
  }
  await browser.close();
  console.log("done →", outDir);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch(e => { console.error(e); process.exit(1); });
}
