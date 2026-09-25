// Axottle-скин: скриншоты ФУНДАМЕНТА (без бэкенда).
//
// Переиспользует стенд `theme-shots.mjs` (тот же apiStub и та же схема сидирования
// аккаунта) и снимает панель с дизайном Axottle в двух темах, плюс галерею кита
// (.axo-*: кнопки, поля, карточка, таблица, статусы, табы, диалог, тост, скелетон,
// пустое состояние, крошки, прогресс) и экран входа.
//
// Галерея кита вставляется в УЖЕ загруженную страницу (document.querySelector('#root')),
// а не отдельным файлом: стили — из настоящего собранного бандла, отдельная страница
// не нужна и ничего лишнего не попадает в прод-сборку.
//
// Usage:  npx vite preview --port 4173 & \
//         node tests/e2e/axottle-shots.mjs http://127.0.0.1:4173
// Отчёт:  tests/e2e/shots/axottle/*.png

import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { apiStub } from "./theme-shots.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const baseURL = process.argv[2] || "http://127.0.0.1:4173";
const outDir = process.argv[3] || join(__dirname, "shots", "axottle");
mkdirSync(outDir, { recursive: true });

const ACCT = { id: "e2e-acc", login: "e2e", token: "e2e-token" };
const DESKTOP = { width: 1280, height: 1200 };
const MOBILE = { width: 390, height: 844 };

/** Галерея кита: только классы .axo-* и токены — то, что реально есть в бандле. */
function galleryHTML(dark) {
  const icon = '<span style="width:16px;height:16px;border-radius:4px;background:currentColor"></span>';
  return `
  <div class="axo-shell" style="min-height:100vh">
    <aside class="axo-shell-sidebar">
      <div class="axo-shell-head"><strong class="h1">Axottle kit</strong></div>
      <nav class="platform-nav" style="display:flex;flex-direction:column;gap:3px">
        <div class="ni-nav-section">Главное</div>
        <button class="navitem active">${icon}<span>Дашборд</span></button>
        <button class="navitem">${icon}<span>Ноды</span></button>
        <button class="navitem">${icon}<span>Бэкапы</span></button>
      </nav>
    </aside>
    <main class="axo-shell-main">
      <header class="axo-topbar">
        <nav class="axo-breadcrumbs">
          <button>Инфраструктура</button><span class="sep">›</span><span class="cur">Кит компонентов</span>
        </nav>
        <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <div class="axo-search" style="width:220px"><input placeholder="Поиск по панели" /></div>
          <button class="axo-btn is-icon" aria-label="Светлая тема">☀</button>
        </div>
      </header>
      <div class="axo-shell-content" style="display:flex;flex-direction:column;gap:18px">
        <header>
          <span class="axo-eyebrow">Главное · компоненты</span>
          <h1 class="axo-page-title">Кит Axottle</h1>
          <p class="axo-page-sub">${dark ? "Тёмная тема" : "Светлая тема"} · кнопки, поля, карточки, таблица, статусы, табы, диалог, тост, скелетон</p>
        </header>

        <section class="axo-card">
          <header class="axo-card-head">
            <span class="axo-card-icon">◆</span>
            <div><h2>Кнопки и статусы</h2><small>.axo-btn · .axo-status</small></div>
          </header>
          <div class="axo-card-body" style="display:flex;flex-direction:column;gap:14px">
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <button class="axo-btn">Применить</button>
              <button class="axo-btn is-quiet">Отмена</button>
              <button class="axo-btn is-ghost">Ещё</button>
              <button class="axo-btn is-danger">Удалить</button>
              <button class="axo-btn is-lg">Задеплоить ноду</button>
              <button class="axo-btn is-sm">Мини</button>
              <button class="axo-btn is-icon" aria-label="Настройки">⚙</button>
              <button class="axo-btn" disabled>Недоступно</button>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <span class="axo-status tone-success">Активна</span>
              <span class="axo-status tone-warning">Деградация</span>
              <span class="axo-status tone-danger">Авария</span>
              <span class="axo-status tone-info">Инфо</span>
              <span class="axo-status">Неизвестно</span>
            </div>
          </div>
        </section>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
          <section class="axo-card">
            <header class="axo-card-head"><div><h2>Поля</h2><small>.axo-input · .axo-switch · .axo-check</small></div></header>
            <div class="axo-card-body" style="display:flex;flex-direction:column;gap:14px">
              <label class="axo-field"><span>Домен</span><input class="axo-input" value="de-fra-1.example.com" /></label>
              <label class="axo-field"><span>SSH-порт</span><input class="axo-input is-err" value="0" /><span class="axo-errmsg">Порт вне диапазона</span></label>
              <label class="axo-field"><span>Провайдер сертификата</span>
                <select class="axo-select-native"><option>cloudflare</option><option>letsencrypt</option></select>
              </label>
              <div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
                <span style="display:inline-flex;align-items:center;gap:9px"><button class="axo-switch" role="switch" aria-checked="true"></button><span style="color:var(--t-low);font-size:13px">Анимации</span></span>
                <span style="display:inline-flex;align-items:center;gap:9px"><button class="axo-switch" role="switch" aria-checked="false"></button><span style="color:var(--t-low);font-size:13px">Неон-свечение</span></span>
                <label class="axo-check"><input type="checkbox" checked /><span>За нодой CDN</span></label>
              </div>
            </div>
          </section>

          <section class="axo-card">
            <header class="axo-card-head"><div><h2>Прогресс и табы</h2><small>.axo-progress · .axo-tabs</small></div></header>
            <div class="axo-card-body" style="display:flex;flex-direction:column;gap:16px">
              <div class="axo-tabs"><button class="is-active">Все</button><button>Проблемные</button><button>Новые</button></div>
              <div class="axo-tabs is-accent"><button class="is-active">Тёмная</button><button>Светлая</button></div>
              <div class="axo-progress-track"><div class="axo-progress-fill" style="width:62%"></div></div>
              <div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--t-low)">Шаг 9 из 15</span><span class="axo-mono" style="color:var(--t-hi)">62%</span></div>
            </div>
          </section>
        </div>

        <section class="axo-card">
          <header class="axo-card-head"><div><h2>Таблица</h2><small>.axo-table — моно-шапка, hover строки</small></div></header>
          <div class="axo-card-body">
            <div class="axo-table-wrap">
              <table class="axo-table">
                <thead><tr><th>Нода</th><th>Страна</th><th>Статус</th><th style="text-align:right">Трафик</th></tr></thead>
                <tbody>
                  <tr><td><code>de-fra-1</code></td><td>DE</td><td><span class="axo-status tone-success">Ok</span></td><td class="num">1.24 ТБ</td></tr>
                  <tr><td><code>nl-ams-2</code></td><td>NL</td><td><span class="axo-status tone-warning">Бан</span></td><td class="num">318 ГБ</td></tr>
                  <tr><td><code>fi-hel-1</code></td><td>FI</td><td><span class="axo-status tone-danger">Оффлайн</span></td><td class="num">0 Б</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
          <section class="axo-card">
            <header class="axo-card-head"><div><h2>Загрузка</h2><small>.axo-skeleton</small></div></header>
            <div class="axo-card-body axo-skeleton-page">
              <div class="axo-skeleton is-title"></div>
              <div class="axo-skeleton is-copy"></div>
              <div class="axo-skeleton" style="height:56px"></div>
            </div>
          </section>
          <div style="display:flex;flex-direction:column;gap:14px">
            <div class="axo-empty" style="min-height:150px">
              <span class="axo-empty-icon">▤</span>
              <h2>Нод пока нет</h2>
              <p>Задеплойте первую ноду — она появится здесь вместе со статистикой.</p>
              <div class="axo-empty-actions"><button class="axo-btn">Задеплоить ноду</button></div>
            </div>
            <div class="axo-dialog" style="width:100%;position:relative">
              <button class="axo-dialog-close" aria-label="Закрыть">✕</button>
              <span class="axo-dialog-icon is-danger">!</span>
              <h2 style="margin-top:19px">Удалить ноду?</h2>
              <p>Нода будет снята с панели. Действие необратимо.</p>
              <div class="axo-dialog-foot"><button class="axo-btn is-quiet">Отмена</button><button class="axo-btn is-danger">Удалить</button></div>
            </div>
            <div class="axo-toast is-error"><div style="flex:1"><strong>Шаг 6 упал</strong><span>SSH: connection refused</span></div></div>
            <div class="axo-toast"><div style="flex:1"><strong>Сертификат обновлён</strong><span>de-fra-1, 89 дней</span></div></div>
          </div>
        </div>
      </div>
    </main>
  </div>`;
}

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

async function openPage(browser, { skin, mode, viewport, tab, seedAccount = true }) {
  const context = await browser.newContext({ viewport });
  if (seedAccount) await seed(context, { skin, mode, tab });
  else await context.addInitScript(() => { localStorage.clear(); });
  await context.route("**/api/**", route => {
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(apiStub(route.request().url())) });
  });
  const page = await context.newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
  await page.goto(baseURL, { waitUntil: "commit" });
  await page.waitForTimeout(1200);
  return { context, page, errors };
}

async function shoot(page, name) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(outDir, name + ".png") });
  console.log("  shot:", name);
}

// @playwright/test из node_modules ждёт свою ревизию chromium (1228), а в кэше стоит
// соседняя (1243) — сборщик браузера подтягивать сюда не будем, поэтому пробуем
// штатный запуск и, если бинарь не найден, падаем на первый chromium-* из кэша.
function launchChromium() {
  return chromium.launch().catch(async err => {
    const { readdirSync, existsSync } = await import("node:fs");
    const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(process.env.HOME || "/root", ".cache", "ms-playwright");
    const alt = (existsSync(cache) ? readdirSync(cache) : [])
      .filter(d => /^chromium-\d+$/.test(d))
      .sort()
      .map(d => join(cache, d, "chrome-linux64", "chrome"))
      .filter(existsSync)
      .pop();
    if (!alt) throw err;
    console.log("  (stock chromium revision missing — using", alt + ")");
    return chromium.launch({ executablePath: alt, args: ["--no-sandbox"] });
  });
}

async function run() {
  const browser = await launchChromium();
  const consoleErrors = [];

  for (const mode of ["dark", "light"]) {
    const tag = `axottle-${mode}`;

    // Панель: дашборд (реальные компоненты, заглушённое API)
    let { context, page, errors } = await openPage(browser, { skin: "axottle", mode, viewport: DESKTOP, tab: "dashboard" });
    const sidebar = await page.locator(".ni-sidebar").count();
    console.log(`desktop ${tag}: .ni-sidebar=${sidebar}`);
    await shoot(page, `panel-dashboard-${tag}`);
    consoleErrors.push(...errors);

    // Галерея кита — в ту же страницу, чтобы стили были из настоящего бандла.
    await page.evaluate(html => {
      const root = document.getElementById("root");
      if (root) root.innerHTML = html;
    }, galleryHTML(mode === "dark"));
    await shoot(page, `kit-gallery-${tag}`);
    await context.close();

    // Настройки → Тема (выбор скина Axottle)
    ({ context, page, errors } = await openPage(browser, { skin: "axottle", mode, viewport: DESKTOP, tab: "settings" }));
    await page.getByRole("button", { name: "Тема" }).click().catch(() => {});
    await shoot(page, `settings-theme-${tag}`);
    consoleErrors.push(...errors);
    await context.close();

    // Табличные экраны (моно-шапка, hover)
    ({ context, page, errors } = await openPage(browser, { skin: "axottle", mode, viewport: DESKTOP, tab: "infra-providers" }));
    await shoot(page, `panel-table-${tag}`);
    consoleErrors.push(...errors);
    await context.close();
  }

  // Мобила: drawer-сайдбар и нижний лист модалки
  let { context, page } = await openPage(browser, { skin: "axottle", mode: "dark", viewport: MOBILE, tab: "dashboard" });
  await shoot(page, "mobile-dashboard-axottle-dark");
  await context.close();

  // Экран входа (до аккаунта) — проверяем, что смена токенов его не сломала
  ({ context, page } = await openPage(browser, { skin: "axottle", mode: "dark", viewport: DESKTOP, seedAccount: false }));
  await shoot(page, "authscreen-axottle-dark");
  await context.close();

  await browser.close();
  console.log("\nconsole.error по прогону:", consoleErrors.length);
  for (const e of [...new Set(consoleErrors)].slice(0, 8)) console.log("  -", e);
  console.log("done →", outDir);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run().catch(e => { console.error(e); process.exit(1); });
}
