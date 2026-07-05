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

async function seed(context, theme) {
  await context.addInitScript(
    ([acct, thm]) => {
      localStorage.setItem("ni_accounts", JSON.stringify([acct]));
      localStorage.setItem("ni_active_account", acct.id);
      localStorage.setItem("ni_thememode_" + acct.id, thm);
    },
    [ACCT, theme],
  );
}

async function shoot(page, name) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(outDir, name + ".png") });
  console.log("  shot:", name);
}

async function run() {
  const browser = await chromium.launch();
  for (const theme of ["dark", "light"]) {
    console.log("theme:", theme);
    const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    await seed(context, theme);
    await context.route("**/api/**", route => {
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) });
    });
    const page = await context.newPage();
    page.on("console", m => { if (m.type() === "error") console.log("  [console.error]", m.text().slice(0, 160)); });

    // vite-dev ESM never fires DOMContentLoaded under headless chromium here;
    // commit + wait for a React-rendered marker instead.
    await page.goto(baseURL, { waitUntil: "commit" });
    await page.waitForSelector("text=Дешборд", { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(700);
    await shoot(page, `dashboard-${theme}`);

    // Deploy tab → open the add-server modal (DeployForm)
    await page.getByText("Деплой ноды", { exact: true }).click().catch(() => {});
    await page.waitForTimeout(250);
    // click the "add server" empty-state button if present
    await page.getByRole("button", { name: /Добавить сервер|Новый сервер|Добавить/ }).first().click().catch(() => {});
    await page.waitForTimeout(300);
    await shoot(page, `deploy-modal-${theme}`);
    // The modal closes on a backdrop mousedown (no Esc handler) — click the far
    // left of the overlay, well outside the centered modal box.
    await page.mouse.click(20, 430);
    await page.waitForTimeout(300);

    // Certs tab (CertsForm)
    await page.getByText("Обновить SSL", { exact: true }).click().catch(() => {});
    await shoot(page, `certs-${theme}`);

    // Settings (footer) → Тема tab
    await page.getByText("Настройки", { exact: true }).click().catch(() => {});
    await page.waitForTimeout(200);
    await page.getByRole("button", { name: "Тема" }).click().catch(() => {});
    await shoot(page, `settings-theme-${theme}`);

    await context.close();
  }
  await browser.close();
  console.log("done →", outDir);
}

// Only drive the browser when executed directly (`node theme-shots.mjs`), not
// when imported by the unit test that exercises apiStub().
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch(e => { console.error(e); process.exit(1); });
}
