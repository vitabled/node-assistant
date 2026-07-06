// Mobile shell smoke — proves the ≤820px adaptation: bottom tab bar visible,
// desktop sidebar hidden, «Ещё» opens the full-nav drawer, tapping a drawer item
// navigates + closes the drawer. Screenshots for visual confirmation.
//
// Usage: node tests/e2e/mobile-smoke.mjs [baseURL] [outDir]

import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { apiStub } from "./theme-shots.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://127.0.0.1:5178";
const outDir = process.argv[3] || join(__dirname, "shots");
mkdirSync(outDir, { recursive: true });

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };
const fails = [];
function check(cond, msg) { console.log((cond ? "  ✓ " : "  ✗ ") + msg); if (!cond) fails.push(msg); }

async function run() {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await context.addInitScript(acct => {
    localStorage.setItem("ni_accounts", JSON.stringify([acct]));
    localStorage.setItem("ni_active_account", acct.id);
    localStorage.setItem("ni_skin_" + acct.id, "apple");
    localStorage.setItem("ni_thememode_" + acct.id, "dark");
  }, ACCT);
  await context.route("**/api/**", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) }));
  const page = await context.newPage();
  page.on("console", m => { if (m.type() === "error") console.log("  [console.error]", m.text().slice(0, 160)); });

  await page.goto(baseURL, { waitUntil: "commit" });
  await page.waitForSelector(".ni-tabbar", { timeout: 15000 });
  await page.waitForTimeout(500);

  const tabbar = page.locator(".ni-tabbar");
  const sidebar = page.locator(".ni-sidebar");
  check(await tabbar.isVisible(), "bottom tab bar visible on mobile");
  check(!(await sidebar.isVisible()), "desktop sidebar hidden on mobile");
  await page.screenshot({ path: join(outDir, "mobile-dashboard.png") });

  // «Ещё» → drawer
  await page.getByText("Ещё", { exact: true }).click();
  await page.waitForTimeout(300);
  const drawer = page.locator(".ni-drawer");
  check(await drawer.isVisible(), "drawer opens on «Ещё»");
  check(await drawer.getByText("Хосты", { exact: true }).isVisible(), "drawer exposes full nav (Хосты)");
  await page.screenshot({ path: join(outDir, "mobile-drawer.png") });

  // tap a drawer item → navigates + closes drawer
  await drawer.getByText("Хосты", { exact: true }).click();
  await page.waitForTimeout(300);
  check(!(await drawer.isVisible()), "drawer closes after tapping a nav item");

  // desktop: tabbar hidden, sidebar visible
  const dctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  await dctx.addInitScript(acct => {
    localStorage.setItem("ni_accounts", JSON.stringify([acct]));
    localStorage.setItem("ni_active_account", acct.id);
  }, ACCT);
  await dctx.route("**/api/**", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) }));
  const dpage = await dctx.newPage();
  await dpage.goto(baseURL, { waitUntil: "commit" });
  await dpage.waitForSelector(".ni-sidebar", { timeout: 15000 });
  await dpage.waitForTimeout(400);
  check(await dpage.locator(".ni-sidebar").isVisible(), "sidebar visible on desktop");
  check(!(await dpage.locator(".ni-tabbar").isVisible()), "tab bar hidden on desktop");

  await context.close();
  await dctx.close();
  await browser.close();
  console.log(fails.length ? `\nFAIL (${fails.length}): ${fails.join("; ")}` : "\nOK — mobile shell smoke passed");
  process.exit(fails.length ? 1 : 0);
}
run().catch(e => { console.error(e); process.exit(1); });
