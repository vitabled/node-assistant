// Render smoke for the 2026-07-07 dashboard/monitoring/stats/hosts/SSL phases.
//
// Boots the SPA (stubbed /api) and loads each key screen, asserting: the app
// mounts (.ni-sidebar present), a screen-specific Russian marker is in the DOM,
// and NO uncaught exception (pageerror) fired on mount. Screenshots each screen.
// This proves the new components RENDER at runtime (tsc + `vite build` only prove
// they type-check + bundle). Committed harness — reused by later frontend work.
//
// Usage:  node tests/e2e/phase-render-smoke.mjs [baseURL]   (run vite on 127.0.0.1 first)
// Requires: @playwright/test (chromium). Exit 0 = all clean, 1 = a screen failed.

import { chromium } from "@playwright/test";
import { apiStub } from "./theme-shots.mjs";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://127.0.0.1:5173";
const outDir = join(__dirname, "shots", "phase-smoke");
mkdirSync(outDir, { recursive: true });

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };

// Extend the shared stub with the endpoints this plan added (Ф1 registry, Ф3 stats).
function stub(url) {
  if (url.includes("/checker/instances"))
    return { instances: [{ id: "local", name: "Локальный чекер", kind: "local", base_url: "", enabled: true }] };
  if (url.includes("/stats/users/node-load")) return { hours: 24, nodes: [] };
  if (url.includes("/stats/users/top-users")) return { hours: 24, users: [] };
  if (url.includes("/stats/users/migrations")) return { hours: 24, approximate: true, migrations: [] };
  return apiStub(url);
}

// tab → a marker regex that should appear once the screen renders.
const SCREENS = [
  { tab: "dashboard",   marker: /Remnawave|узл|Аптайм|Мониторинг|нод|подписк/i },   // Ф2 selector + SubscriptionSelector
  { tab: "stats-users", marker: /Пользовател|нагруз|загруз|стабильн|быстр|данных пока нет|оценка/i }, // Ф4 widgets
  { tab: "settings",    marker: /Настройки|Тема|Мониторинг|Деплой|Deploy/i },        // Ф2 Monitoring tab
  { tab: "certs",       marker: /SSL|домен|сертификат/i },                            // Ф8 DomainsPanel
  { tab: "deploy",      marker: /сервер|Деплой|Добавить/i },                          // Ф5/Ф6 forms
];

async function run() {
  const browser = await chromium.launch();
  let failures = 0;
  for (const { tab, marker } of SCREENS) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 1400 } });
    await ctx.addInitScript(([acct, t]) => {
      localStorage.setItem("ni_accounts", JSON.stringify([acct]));
      localStorage.setItem("ni_active_account", acct.id);
      localStorage.setItem("ni_skin_" + acct.id, "apple");
      localStorage.setItem("ni_thememode_" + acct.id, "dark");
      localStorage.setItem("ni_tab_" + acct.id, t);
    }, [ACCT, tab]);
    await ctx.route("**/api/**", r =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stub(r.request().url())) }));
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on("pageerror", e => pageErrors.push(String(e).slice(0, 240)));
    await page.goto(baseURL, { waitUntil: "commit" });
    const booted = await page.waitForSelector(".ni-sidebar", { timeout: 15000 }).then(() => true).catch(() => false);
    await page.waitForTimeout(800);
    const bodyText = (await page.textContent("body").catch(() => "")) || "";
    const hasMarker = marker.test(bodyText);
    await page.screenshot({ path: join(outDir, tab + ".png") });
    const ok = booted && hasMarker && pageErrors.length === 0;
    console.log(`${ok ? "PASS" : "FAIL"} ${tab}  booted=${booted} marker=${hasMarker} pageerrors=${pageErrors.length}`);
    if (!ok) {
      failures++;
      pageErrors.slice(0, 3).forEach(e => console.log("   pageerror:", e));
      if (!hasMarker) console.log("   (marker not found in DOM)");
    }
    await ctx.close();
  }
  await browser.close();
  console.log(failures ? `\n${failures} screen(s) FAILED` : "\nALL screens rendered clean ✓");
  process.exit(failures ? 1 : 0);
}
run().catch(e => { console.error(e); process.exit(1); });
