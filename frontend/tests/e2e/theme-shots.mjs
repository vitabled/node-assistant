// Reusable frontend screenshot harness (no backend needed).
//
// Seeds a device account into localStorage so AuthGate mounts the SPA, stubs
// every /api/** call with benign JSON so components render without a live
// backend, then screenshots key screens in BOTH themes (dark + light).
//
// Usage:  node tests/e2e/theme-shots.mjs [baseURL] [outDir]
//   baseURL defaults to http://localhost:5173 (run `npx vite` first)
//   outDir  defaults to tests/e2e/shots
//
// Requires: @playwright/test (chromium). This is a visual harness — it proves
// the theme applies and the layout reorg landed; it is NOT a console-clean e2e
// (the backend is stubbed).

import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://localhost:5173";
const outDir = process.argv[3] || join(__dirname, "shots");
mkdirSync(outDir, { recursive: true });

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };

// Benign JSON per endpoint so components don't crash on unexpected shapes.
export function apiStub(url) {
  if (url.includes("/checker/statuspage")) return { container: "stopped", global: {}, nodes: [] };
  if (url.includes("/checker/incidents")) return [];
  if (url.includes("/checker/status")) return { container: "stopped", global: {}, proxies: [] };
  if (url.includes("/node-plugins")) return [];
  if (url.includes("/squads")) return [];
  if (url.includes("/remnawave/nodes")) return [];
  if (url.includes("/templates")) return [];
  if (url.includes("/traffic-rules")) return [];
  if (url.includes("/subscriptions/status")) return [];
  if (url.includes("/subscriptions")) return [];
  if (url.includes("/hosts")) return [];
  if (url.includes("/domains")) return [];
  // Infra-billing: list endpoints → [], summary/settings → benign objects.
  if (url.includes("/infra-billing/dashboard/summary"))
    return { total_balance: 0, base_currency: "RUB", burn: { hourly: 0, daily: 0, monthly: 0, daysLeft: null, critical: false },
             spend_by_provider: [], monthly: [] };
  if (url.includes("/infra-billing/settings"))
    return { base_currency: "RUB", fx_rates: {}, low_balance_threshold: 0, refresh_interval: 60 };
  if (url.includes("/infra-billing/")) return [];
  if (url.includes("/settings")) {
    return {
      remnawave: { panel_url: "", api_token: "", default_internal_squad_ids: [], default_external_squad_ids: [] },
      deploy_defaults: {
        ssh_user: "root", email: "", cloudflare_api_key: "", current_ssh_port: 22, new_ssh_port: 2222,
        open_ports: "", change_ssh_port: true, remnanode_port: 2222, xhttp_path: "",
        haproxy_source_port: 443, haproxy_dest_port: 443, haproxy_maxconn: 200000,
        haproxy_log: "global", haproxy_mode: "tcp",
        haproxy_timeout_connect: "5s", haproxy_timeout_client: "50s", haproxy_timeout_server: "50s", haproxy_timeout_tunnel: "1h",
      },
      optimization: {},
      xray_checker: { enabled: false, subscription_url: "", check_interval: 300, check_method: "ip", metrics_port: 2112, image: "", poll_interval: 60 },
    };
  }
  return {};
}

// Seed device account + skin + mode + a starting tab, all before first paint.
async function seed(context, { skin, mode, tab }) {
  await context.addInitScript(
    ([acct, s, m, t]) => {
      localStorage.setItem("ni_accounts", JSON.stringify([acct]));
      localStorage.setItem("ni_active_account", acct.id);
      localStorage.setItem("ni_skin_" + acct.id, s);
      localStorage.setItem("ni_thememode_" + acct.id, m);
      if (t) localStorage.setItem("ni_tab_" + acct.id, t);
    },
    [ACCT, skin, mode, tab || ""],
  );
}

async function shoot(page, name) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(outDir, name + ".png") });
  console.log("  shot:", name);
}

async function openPage(browser, { skin, mode, viewport, tab }) {
  const context = await browser.newContext({ viewport });
  await seed(context, { skin, mode, tab });
  await context.route("**/api/**", route => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) });
  });
  const page = await context.newPage();
  page.on("console", m => { if (m.type() === "error") console.log("  [console.error]", m.text().slice(0, 160)); });
  // vite-dev ESM never fires DOMContentLoaded under headless chromium here;
  // commit + wait for a React-rendered marker (the always-present sidebar).
  await page.goto(baseURL, { waitUntil: "commit" });
  await page.waitForSelector(".ni-sidebar", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);
  return { context, page };
}

const DESKTOP = { width: 1280, height: 1400 };
const MOBILE = { width: 390, height: 844 };

async function run() {
  const browser = await chromium.launch();

  // ── Desktop matrix: skin × mode, key screens ──
  for (const skin of ["apple", "console"]) {
    for (const mode of ["light", "dark"]) {
      const tag = `${skin}-${mode}`;
      console.log("desktop:", tag);
      // Dashboard
      let { context, page } = await openPage(browser, { skin, mode, viewport: DESKTOP, tab: "dashboard" });
      await shoot(page, `matrix/dashboard-${tag}`);
      // Settings → Тема (skin+mode selectors)
      await page.getByText("Настройки", { exact: true }).click().catch(() => {});
      await page.waitForTimeout(250);
      await page.getByRole("button", { name: "Тема" }).click().catch(() => {});
      await shoot(page, `matrix/settings-theme-${tag}`);
      await context.close();
      // Infra providers (a converted table page)
      ({ context, page } = await openPage(browser, { skin, mode, viewport: DESKTOP, tab: "infra-providers" }));
      await shoot(page, `matrix/infra-providers-${tag}`);
      await context.close();
    }
  }

  // ── Mobile pass: apple light+dark — dashboard reflow + bottom-sheet modal ──
  for (const mode of ["dark", "light"]) {
    const tag = `apple-${mode}`;
    console.log("mobile:", tag);
    const { context, page } = await openPage(browser, { skin: "apple", mode, viewport: MOBILE, tab: "dashboard" });
    await shoot(page, `matrix/mobile-dashboard-${tag}`);
    // Traffic → open the create modal to verify the bottom-sheet on ≤600px
    await page.getByText("Ещё", { exact: true }).click().catch(() => {});
    await page.waitForTimeout(250);
    await page.locator(".ni-drawer").getByText("Трафик", { exact: true }).click().catch(() => {});
    await page.waitForTimeout(250);
    await page.getByRole("button", { name: /Создать ограничение|Создать первое правило/ }).first().click().catch(() => {});
    await page.waitForTimeout(300);
    await shoot(page, `matrix/mobile-sheet-${tag}`);
    await context.close();
  }

  await browser.close();
  console.log("done →", join(outDir, "matrix"));
}

// Only drive the browser when executed directly (`node theme-shots.mjs`), not
// when imported by the unit test that exercises apiStub().
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch(e => { console.error(e); process.exit(1); });
}
