\# CLAUDE.md



Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.



\*\*Tradeoff:\*\* These guidelines bias toward caution over speed. For trivial tasks, use judgment.



\## 1. Think Before Coding



\*\*Don't assume. Don't hide confusion. Surface tradeoffs.\*\*



Before implementing:

\- State your assumptions explicitly. If uncertain, ask.

\- If multiple interpretations exist, present them - don't pick silently.

\- If a simpler approach exists, say so. Push back when warranted.

\- If something is unclear, stop. Name what's confusing. Ask.



\## 2. Simplicity First



\*\*Minimum code that solves the problem. Nothing speculative.\*\*



\- No features beyond what was asked.

\- No abstractions for single-use code.

\- No "flexibility" or "configurability" that wasn't requested.

\- No error handling for impossible scenarios.

\- If you write 200 lines and it could be 50, rewrite it.



Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.



\## 3. Surgical Changes



\*\*Touch only what you must. Clean up only your own mess.\*\*



When editing existing code:

\- Don't "improve" adjacent code, comments, or formatting.

\- Don't refactor things that aren't broken.

\- Match existing style, even if you'd do it differently.

\- If you notice unrelated dead code, mention it - don't delete it.



When your changes create orphans:

\- Remove imports/variables/functions that YOUR changes made unused.

\- Don't remove pre-existing dead code unless asked.



The test: Every changed line should trace directly to the user's request.



\## 4. Goal-Driven Execution



\*\*Define success criteria. Loop until verified.\*\*



Transform tasks into verifiable goals:

\- "Add validation" → "Write tests for invalid inputs, then make them pass"

\- "Fix the bug" → "Write a test that reproduces it, then make it pass"

\- "Refactor X" → "Ensure tests pass before and after"



For multi-step tasks, state a brief plan:

```

1\. \[Step] → verify: \[check]

2\. \[Step] → verify: \[check]

3\. \[Step] → verify: \[check]

```



Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.



\---



\*\*These guidelines are working if:\*\* fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# PROJECT ARCHITECTURE REFERENCE (node-installer)

> Reference architecture of the fullstack node auto-deploy service. This section is the source of truth — keep it in sync with the code. Statements below reflect the **actual implementation**, which in a few places differs from earlier verbal specs (those deltas are called out explicitly).

## 0. Continuous Memory Protocol — CRITICAL
- Every codebase change (feature, refactor, bug fix, DTO change, new pipeline step) MUST be reflected here in the same session.
- When a task/bugfix is completed: update the relevant section below (naming conventions, data model, endpoints, pipeline steps).
- When you hit non-obvious third-party behavior (Remnawave API, acme.sh, Certbot, UFW, WARP/wgcf, Node Accelerator, Docker Compose) and find a workaround → record it immediately in **§6 Troubleshooting & Quirks** so we don't re-learn it.
- Record the *actual* code behavior, not the aspirational spec. If a verbal request conflicts with what shipped, document what shipped and note the conflict.

## 1. Tech Stack & Layout
- **Backend:** FastAPI (async, Python 3.11), Pydantic v2. `backend/app/`
  - `models/`: `deploy.py` (`DeployRequest`, `DeployCertRequest`), `settings.py` (`AppSettings`/`RemnavaveConfig`/`DeployDefaults`/`OptimizationSettings`/`Template`), `traffic_rules.py`, `subscriptions.py`.
  - `services/`: `pipeline.py` (the **14-step** deploy), `ssh_manager.py` (`SSHSession` — `run_script` streams, `get_output`/**`get_script_output`** silent), `remnawave_client.py`, `storage.py`, `task_store.py` (`Task`, `STEP_LABELS`), `cloudflare.py`, `backend_ip.py`. **Wave-1 services:** `test_tools.py`, `testserver_registry.py`, `speedtest_store.py`, `panel_pipeline.py`, `backup_service.py`, `subpage_store.py` (see §7).
  - `api/`: `settings.py`, `traffic_rules.py`, deploy/certs routers; wired in `main.py`.
- **Frontend:** React 18 + TS + Vite + Tailwind. `frontend/src/components/`: `DeployDashboard.tsx`, `DeployForm.tsx`, `DeployCard.tsx`, `Settings.tsx`, `CountrySelect.tsx`, `MultiSelect.tsx`, `TrafficRules.tsx`, `StepProgress.tsx`, `Sidebar.tsx`, `Dashboard.tsx`, `CertsForm.tsx`, `DomainsPanel.tsx`, `Hosts.tsx`.
- **Persistence:** JSON files under `DATA_DIR` (default `/app/data`): `settings.json`, `templates.json`, `traffic_rules.json`. **Deploy job cards live in browser `localStorage`** (key `deploy_jobs`) — there is NO server-side task-list DB.
- **Live logs:** `SSHSession.run_script` pipes scripts to `bash -s 2>&1` and streams stdout line-by-line into the task; the frontend `DeployCard` consumes a per-task stream via `useTaskStream`.
- **Verify before "done":** backend `python -m py_compile` on changed files; frontend `npx --no-install tsc --noEmit`. Then update this file (§5/§6).
- **Auth/accounts files:** backend `services/accounts.py`, `api/auth.py`; frontend `src/auth/` (`store.ts`, `useAuth.ts`, `apiClient.ts`, `AuthScreen.tsx`, `AuthGate.tsx`, `AccountMenu.tsx`). Backend tests in `backend/tests/` (`pytest`, uses `fastapi.testclient`). (Other pre-existing files not previously listed here: backend `api/stats.py`, `api/ws.py`, `config.py`; frontend `components/Templates.tsx`, `components/TerminalOutput.tsx`, `hooks/useTaskStream.ts`, `theme/*`, `utils/format.ts`.)

## 1b. Accounts, Auth & Per-Account Isolation
The whole SPA is gated behind account auth; there is no anonymous access.
- **Account registry (GLOBAL):** `DATA_DIR/accounts.json` = `{accounts:[{id, login, password_hash, created_at}]}`. Passwords hashed with **bcrypt** (salted); the plaintext input is pre-hashed sha256+base64 first (fixed 44-byte, NUL-free → sidesteps bcrypt's 72-byte truncation). Login uniqueness is **case-insensitive**, enforced under a `threading.Lock` (create_account read-modify-write) so concurrent registrations can't both win. `services/accounts.py`.
- **Sessions = stateless JWT** (HS256, signed with `settings.encryption_key`, claim `sub`=account_id, **no exp** → persistent like Google). `issue_token`/`account_id_from_token`. No server-side session store → survives backend restart with nothing session-related at rest; logout / "remove from device" are purely client-side (drop the token). **⚠️ Set a strong `ENCRYPTION_KEY` in prod** — the default `dev_key_...` lets anyone forge a JWT for any account (and it's also the Fernet key for the infra-billing vault).
- **Active-account resolution:** `require_account` dependency (`api/auth.py`) parses `Authorization: Bearer <jwt>` → account_id → publishes it on the `current_account` **ContextVar** (`accounts.current_account`). Missing/malformed/garbage token, or a token for a since-deleted account → **401**. The ContextVar is copied automatically into `asyncio.create_task` (deploy pipeline) and `asyncio.to_thread` (sqlite stores), so those resolve the right account.
- **API access tokens (Wave-5 План H):** `require_account` ALSO accepts a long-lived **API token** (`Bearer nai_<account_id>_<secret>`) alongside the session JWT — `services/api_tokens.py` (`resolve`), for external integrations that shouldn't carry a browser JWT. Per-account store `accounts/<id>/api_tokens.json` keeps only an **HMAC-SHA256** digest (key=`encryption_key`; fast MAC OK — token has 256-bit entropy), secret shown ONCE. account_id embedded in the token → O(1) resolve loading only that account's file (no global index → isolation kept). A **readonly** token → mutating methods (non GET/HEAD/OPTIONS) get **403** (enforced inline in `require_account`, publishes `api_tokens.token_readonly` ContextVar). `mint_managed(name, readonly)` rotates a named managed token; **MCP** (`mcp_server.start`) now injects a managed readonly API token as `NODE_ASSISTANT_TOKEN` (rotated each start) instead of a raw session JWT (JWT fallback on failure). Routes `/api/api-tokens` (GET/POST/DELETE, under `require_account`); frontend Settings→«Токены API» (`settings/ApiTokensTab.tsx`, show-once + amber warning). Tests `test_api_tokens.py`.
- **Wiring (`main.py`):** `auth.router` is public; all data routers (`deploy, certs, stats, settings, traffic_rules, xray_checker, infra_billing`) get `dependencies=[Depends(require_account)]`. `ws.router` and `/api/health` stay ungated — the WS log stream is capability-based (unguessable task_id; browsers can't set headers on the WS handshake).
- **Data isolation:** every isolated store lives under `DATA_DIR/accounts/<id>/` — `settings.json`, `templates.json`, `traffic_rules.json`, `infra_billing.db`. `storage.py` and `infra_billing_store.py` resolve the dir from `current_account` (both accept an explicit `account_id` for background callers). The xray-checker **metrics DB stays global** (`xray_checker_metrics.db` at DATA_DIR root — not in the isolation set; the checker container + poller are shared infra). deploy job cards in `localStorage` are keyed **per account** (`deploy_jobs_<id>`, via `auth/store.ts:deployJobsKey`).
- **Legacy migration:** the FIRST account created inherits the pre-auth root-level files (settings/templates/traffic_rules/infra_billing.db) — copied into its dir, **originals kept as backup**. Subsequent accounts start empty. `accounts._migrate_legacy`.
- **Frontend auth layer (`src/auth/`):** `store.ts` = device account list `{id,login,token}` + activeId in `localStorage` (`ni_accounts`/`ni_active_account`), exposed via `useAuth` (useSyncExternalStore). `apiClient.ts` installs a **global `window.fetch` interceptor** (from `main.tsx`) that attaches `Authorization: Bearer <activeToken>` to every `/api` request and, on 401, calls `forget(active)` → app falls back to the login gate. This is why individual `fetch` call sites and infra `api.ts` need no per-call auth. `AuthGate` renders `<AuthScreen/>` when no account is active, else `<App key={activeId}/>` — **App is keyed by activeId so switching accounts fully remounts it** (clean per-account in-memory state: active tab, deploy cards, fetched settings). `AccountMenu` (topbar) = Google-style switcher: instant switch, Добавить аккаунт (overlay `AuthScreen`), Выйти из аккаунта (logout active), per-account Удалить с устройства. `AuthScreen` = login (login+password) / register (login+password + «Сгенерировать пароль» via `crypto.getRandomValues` + copy; a **generated password shows an amber «пароль нигде не сохраняется — скопируйте сейчас» warning**, cleared on manual edit) / account chooser. Register rejects a **whitespace-only password** (422, symmetric with the whitespace-login check) — `api/auth.py`.

## 2. Frontend Behavior
- **Deploy modes (tabs):** the add-server modal has horizontal tabs **Remnanode** (default) / **HAProxy** (`FormData.mode`). Validation is mode-gated (`validateForm`, **exported** for unit tests — `DeployForm.test.tsx`): domain/email/country only in remnanode mode; the **Cloudflare token is required only when `cert_provider === "cloudflare"`**; `haproxy_dest_ip` required in haproxy mode; `whitelist_ips` accepts anything (normalized server-side in Ф5).
- **Deploy form layout (Ф4 reorg):** section order in **Remnanode** mode: Режим-tabs → **Сервер** (ip/ssh + «Обновить систему перед стартом») → **Remnanode** (remnanode_port/xhttp/CountrySelect/«Установить WARP Native») → **Remnawave** *(collapsible)* (remnanode_token + the panel-registration block) → **Домен и SSL** *(collapsible)* (**cert_provider** selector `cloudflare|letsencrypt|zerossl`; CF-token field shown ONLY for cloudflare; domain+email) → **Сеть** *(collapsible)* (ssh ports + change_ssh_port + open_ports; the old «Полоса (Mbps)» `bandwidth_mbps` field is **removed everywhere**) → **Оптимизация ОС** *(collapsible)*. In **HAProxy** mode «Настройки HAProxy» (renamed from «HAProxy (реле)») sits **above** «Сеть». Collapsible open-state is per-section (`sec` state); a failed submit auto-opens any collapsed section hiding an errored field (`FIELD_SECTION`). Reusable `Collapsible`/`SectionLabel` components.
- **Оптимизация ОС section** (both modes) also carries: «Нода за CDN» (`behind_cdn`, remnanode-only), «Установить vnstat» (`install_vnstat`, default on), «Установить TrafficGuard» (`install_trafficguard`, default on), a **Whitelist IP/CIDR** `<textarea>` (`whitelist_ips`), and «Разрешить SSH для всех» (`allow_ssh_all`). vnstat is no longer unconditional — it's gated on `install_vnstat` (Ф5 wires behavior).
- **SSL management section (Ф10 — «Управление SSL», renamed from «Обновить SSL»):** the `certs` tab (Sidebar `ShieldCheck` icon, `CRUMB` "Управление SSL") now **deploys** a cert (was renew). `CertsForm` gained a **cert_provider `<select>`** (cloudflare|letsencrypt|zerossl) — CF-token field only for cloudflare, an **Email (ACME)** field + HTTP-01 notice for the others — and a **force** toggle; submit is «Задеплоить сертификат» (`App.tsx:deployCert` → `POST /api/certs/deploy`). The screen also renders a **`DomainsPanel`**: the account's domains auto-filled from `deploy_jobs_<id>` (successful remnanode nodes, cert expiry probed via `POST /api/stats/node` with the saved SSH creds — green ≥14d/amber<14d/red expired) + manual domains from `GET/POST/DELETE /api/domains` (deduped). Backend: `build_ssl_script`/`ssl_needs_cf_dns` were **extracted from `step_ssl`** so the pipeline deploy and the SSL-management endpoint share one provider-logic source; `RENEW_STEPS[1]` label updated to match `certs.py`'s 3 deploy steps. Tests: `certs.py` (`test_certs.py` — provider validation, cert-present skip/force, domains CRUD), `CertsForm.test.tsx` (validate: provider gating).
- **Country flags = `flag-icons` SVG** (`fi fi-<cc>`, CSS imported in `main.tsx`) in `CountrySelect` (also converted to CSS-var theme tokens), replacing the emoji `getFlagEmoji`; XX/unknown → Globe fallback. `getFlagEmoji` remains for `Dashboard`/`InfraProviders` (their own phases).
- **Settings → Deploy defaults** (`DeployDefaults`): `ssh_user`, `email`, `cloudflare_api_key`, `current_ssh_port` (22), `new_ssh_port` (2222), `open_ports`, `change_ssh_port` (true), `remnanode_port` (2222), `xhttp_path` (""), `whitelist_ips` ("") (prefills the deploy-form whitelist), plus **HAProxy defaults** `haproxy_source_port` (443), `haproxy_dest_port` (443), `haproxy_maxconn` (200000), `haproxy_log` (global), `haproxy_mode` (tcp), `haproxy_timeout_{connect=5s,client=50s,server=50s,tunnel=1h}`. These auto-prefill the deploy form.
- **New-deploy modal MUST pass NO `initial`** so `DeployForm`'s settings-overlay effect (`if (!initial)`) runs; passing `FORM_DEFAULT` suppressed it and left email/Cloudflare/XHTTP empty (fixed). Edit/retry modal passes `editJob.savedForm` (keeps saved values).
- **Form fields:** `change_ssh_port` toggle (disables/skips new-port input when off; `useRef intendedNewPort` restores 2222 on re-enable), required `remnanode_port`, optional `xhttp_path`, required `CountrySelect` (single-select w/ search → `country_code`), `behind_cdn` checkbox, and **single-select** `plugin_uuid` `<select>` ("Не использовать плагин" = `""` → sent as `null`). Plugins load from `GET /api/remnawave/node-plugins`; squad/plugin selectors are disabled unless Remnawave is configured.
- **Traffic Rules** tab: table of quotas (ALL = node-level monthly/no-reset; SQUAD = user bulk, all periods) in GB.
- **Per-node stats (deploy cards only, NOT the status page):** a SUCCESS `DeployCard` polls `POST /api/stats/node` **every 5 min** with its own SSH creds from `savedForm` (creds per-request, never persisted → client-driven poll, not a stored-cred background worker, which the no-secrets-at-rest rule forbids). SSH port = `change_ssh_port ? new_ssh_port : current_ssh_port`. One SSH session runs 3 parallel probes; response has **`securityStats`** + **`trafficStats`**:
  - `securityStats {fail2banActive, fail2banTotal, trafficGuardActive}` — `fail2ban-client status sshd` parsed via `Currently banned:\s*(\d+)` / `Total banned:\s*(\d+)`; `iptables -L -n | grep -c 'na-ctguard'`. Card «Безопасность сервера» block, amber highlight when active bans > 0.
  - `trafficStats {today, week, month}` each `{rx, tx, total}` in **bytes** — parsed from **`vnstat --json`** (v2.x native JSON, bytes): aggregate across all `interfaces[].traffic`; today=latest `day`, week=last 7 `day` entries, month=latest `month` (also accepts legacy `days`/`months` keys). Card «Сетевой трафик» block with period selector (За сегодня/неделю/месяц), rows RX (↓)/TX (↑)/Total, bytes→ГБ/МБ (2 dp). **5-min cache** because vnstat updates its DB discretely.
- **vnstat** is installed in pipeline Step 2 (`apt install vnstat` + `systemctl enable --now vnstat`, non-fatal) — required software on every deployed node for the traffic block.
- **Cert-expiry + component management (Ф7, SUCCESS cards only):** the same 5-min `POST /api/stats/node` poll now also sends `domain` and returns **`certInfo {daysLeft, notAfter}`** (probe `_cert_expiry` runs `openssl x509 -enddate` + `date` math **on the node**, so no server clock/parse; missing/self-signed/empty-domain → `null` → card shows «неизвестно», never 500). Card renders a **CertBlock** (green ≥14d / amber <14d / red expired) for remnanode nodes. A **ManageBlock** lists the node's manageable components (derived from `savedForm` via `manageableComponents`: Node Accelerator[if optimize]/TrafficGuard[if install_trafficguard]/Remnanode/Masking/WARP[if install_warp]/SSL/Hysteria2 in remnanode mode; Node Accelerator/TrafficGuard/HAProxy in haproxy mode) each with **переустановить (↻)** and **удалить (🗑, two-click confirm)**. Steps «Подключение»/«Обновление системы» and the SSH-port network steps are intentionally NOT manageable. Actions POST `/api/node/step` (payload = coerced `opPayload(savedForm)` + `{component, action}`) → a streamed op task shown in an `OpStreamModal` (second `useTaskStream`).
- **Dashboard instant render:** `DeployDashboard` keeps jobs in local state + localStorage. The "card not appearing until F5" bug was fixed with **functional `setState`** (`setJobs(prev => …)`) in `addJob`/`retryJob`/`removeJob` — a stale async closure had been writing a list computed from an outdated snapshot. NOTE: there is **no** WebSocket `TASK_CREATED` and no list-refetch — the mechanism is local state + per-card SSE stream.
- **Active-tab persistence (per account):** `App` restores the last-open `Tab` from `localStorage` key `ni_tab_<accountId>` on mount and writes it on every change (`auth/store.ts:tabKey`). Fixes the F5-drops-to-dashboard bug. Different accounts remember different last tabs; the value is validated against `CRUMB` keys (unknown → `dashboard`). The account switcher lives in the topbar (`AccountMenu`).

## 2a. Appearance & Theme (skin × mode) + mobile
- **Two independent axes on `document.documentElement`** (`theme/tweaks.ts`):
  - **SKIN** — `data-skin`, `AppSkin = "apple"|"console"|"neon"` (`SKINS`), **default apple**. `applySkin(s)` sets `data-skin`. Apple = System-Settings aesthetic (SF/system font, radii 7/10/14, iOS toggle `#34C759`, pill segmented control, filled-nav-pill, glass-blur 22px); Console = the JetBrains-Mono default. **Neon (Wave-5 Plan B)** = third additive skin, **dark-committed** (same palette under light+dark, like `--term-bg`) — deep-black surfaces + glow. `applyAccent` emits `--accent-glow` on ALL skins but only neon renders `--glow` (apple/console keep `--glow:none` → zero change); `ACCENTS` gained `magenta`/`lime`. `loadSkin` validates 3 values.
  - **Motion + appearance (Wave-5 Plan B):** reusable primitives in `theme/motion.tsx` — `useMotionEnabled()` (device toggle `ni_motion` AND OS `prefers-reduced-motion`), `tabFade` (screen-transition variant, wired via `App.tsx` `<Screen>` keyed `motion.div`), `<Stagger>`/`<StaggerItem>`, `<AnimatedNumber>` (rAF tween, snaps under reduced-motion), `<Skeleton>` (`.ni-skeleton` shimmer). `index.css` has a top-priority `@media (prefers-reduced-motion: reduce)` kill-switch + `.hover-glow`/neon glow utilities (inert off-neon) + `--viz-1..8` data-ink ramp (neon-boosted). Device-global toggles «Анимации»/«Неон-свечение» in `ThemeTab` (`applyNeonGlow` stamps `data-glow`). **Backend appearance mirror (idea 5):** `AppearanceConfig` on `AppSettings` (skin/mode/accent/density/animations/neon_glow, Literal-validated) + `POST /api/settings/appearance` (per-account `settings.json`, no secrets) — best-effort mirror so the look follows the account across devices; localStorage stays the fast local cache. Tests `test_settings_appearance.py`.
  - **MODE** — `data-theme`, `ThemeMode = "light"|"dark"|"system"` (`THEME_MODES`), **default system**. `applyThemeMode(mode)` sets `data-theme` to resolved `light`/`dark`; `system` resolves via `matchMedia("(prefers-color-scheme: light)")` + a live `change` listener (module-scoped `_sysListener`, replaced each call) → OS switch flips on the fly. `resolveThemeMode` is the pure resolver. (`system`→dark when no light preference — satisfies "по умолчанию тёмная".)
- **Palettes** (`index.css`): `:root` = console **dark** base; `:root[data-theme="light"]` = console light. Apple adds a parallel set gated by `data-skin`: `:root[data-skin="apple"]` (structural: font/radii/controls/glass, theme-independent), `:root[data-skin="apple"][data-theme="light"]` (Apple-light palette), `:root[data-skin="apple"]:not([data-theme="light"])` (Apple-dark palette). Console untouched. Accent applied inline by `applyAccent` (skin/theme-independent); terminal `--term-bg` stays dark everywhere.
- **Storage (per-account):** `ni_skin_<accountId>` (default apple, garbage→apple) + `ni_thememode_<accountId>` (default system). **Accent + density stay device-global** (`ni_accent`/`ni_density`). `App.tsx` applies skin/mode/accent/density **once on mount**; App is keyed by `activeId` (AuthGate) → account switch remounts and re-reads that account's skin+mode.
- **Controls — Settings → «Тема» tab** (`Settings.tsx` `ThemeTab`): **Стиль cards (Apple/Консоль)** + mode cards (Системная/Светлая/Тёмная) + accent swatches + density seg; apply/save imperatively.
- **Mobile version** (skin-independent CSS in `index.css`): `@media(max-width:820px)` hides `.ni-sidebar`, shows fixed bottom `.ni-tabbar` (`BottomTabBar` — Статус/Деплой/SSL/Трафик + «Ещё»→drawer), tightens `.ni-topbar`/hides `.ni-clock`, stacks `.ni-pagehead`, enlarges tap targets. `@media(max-width:600px)` reflows `.ni-health`/`.ni-noderow`, turns `.overlay`/`.modal` into **bottom-sheets** (`ni-sheetUp`), + `env(safe-area-inset-*)` via `--safe-b/l/r` (needs `viewport-fit=cover` in `index.html`). Shell hooks: `App.tsx` (`ni-topbar/ni-clock/ni-main`), `Sidebar` (`ni-sidebar`; `drawer` prop omits the class so it stays visible inside `.ni-drawer`), page bodies/headers via `ni-pagebody/ni-pagehead/ni-pagehead-actions` (all 7 infra pages get these from the shared `infra/ui.tsx` `Page`/`PageHeader`; `Dashboard` also `ni-health/ni-noderow/ni-node-name/ni-node-bars`). Fixed grids (`certs`, `DeployCard`) stack ≤lg/sm; wide `.tbl` wrapped in `overflow-x-auto`.
- **CSS-var conversion — COMPLETE:** all previously hardcoded-dark components were converted to `var(--…)` tokens / utility classes so light + apple-light render coherently: Settings sub-forms, `Templates.tsx`, `TrafficRules.tsx`, `MultiSelect.tsx` (no longer a "dark island"), `CountrySelect.tsx` (+ `max-w-full`), and all `components/infra/*` (+ `ui.tsx`/`Toast.tsx`). Chart data-ink palettes (donut/line) intentionally kept as fixed hues. (Earlier-converted: DeployForm/DeployCard/StepProgress/CertsForm/deploy-modal/Dashboard.)
- **Layout:** topbar shows the «Remnawave • онлайн» indicator **centered** in the header (Ф7 — absolutely positioned, `.ni-clock`, hidden ≤820px) + `AccountMenu` on the right; «Настройки» lives in the sidebar footer.
- **Verify harnesses** (Playwright, `@playwright/test`+chromium; seed device account in `localStorage`, stub `/api/**` via `apiStub` in `theme-shots.mjs`): `theme-shots.mjs` = **matrix** skin×mode×viewport (desktop apple/console × light/dark for dashboard/settings-theme/infra-providers + mobile apple light/dark for dashboard + bottom-sheet) → `shots/matrix/`; `mobile-smoke.mjs` = **committed** assertions (tabbar visible/sidebar hidden ≤820px, drawer opens, nav closes it, desktop inverse). Run vite on **`127.0.0.1`** then `node tests/e2e/theme-shots.mjs http://127.0.0.1:<port>`; use `waitUntil:"commit"` (vite-dev ESM no DOMContentLoaded headless) + wait for `.ni-sidebar`. Unit: `theme/tweaks.test.ts` (mode/accent/density/**skin**) + `components/Settings.test.tsx` + `theme-shots.test.mjs` (`apiStub` shapes incl. infra-billing). `apiStub` covers infra-billing/subscriptions/hosts/domains (list→`[]`, summary/settings→objects).

## 3. Deploy Pipeline (`run_pipeline`, **14 steps** — `STEP_LABELS` index = `_begin_step` N)
Any exception → `task.finish(FAILED)` and re-raise → node card shows FAILED + retry.

**Step list + StepProgress groups (Ф6 + Ф2-wave1):** `STEP_LABELS` (14) = 1 Подключение · 2 Обновление системы · **[Оптимизация ОС]** 3 Node Accelerator, 4 TrafficGuard, **5 Тест-инструменты** · **[Сеть]** 6 Добавление порта SSH, 7 Перезагрузка, 8 Проверка нового порта SSH, 9 Удаление старого порта SSH · 10 Cloudflare DNS + SSL · **[Установка remnanode]** 11 Remnanode, 12 Маскировочный сайт, 13 WARP Native, 14 Hysteria2. **Wave-1 added step 5 «Тест-инструменты»** (`step_test_tools`, gated on `install_test_tools` + `skip_components`, non-fatal, runs in BOTH modes before the mode-branch) INSIDE the «Оптимизация ОС» group → all indices ≥5 shifted +1 (system_optimize 6, dualport-verify 7/8/9, ssl 10, remnanode 11, masking 12, warp 13, hysteria2 14). `test_tools` is a manageable `Component` in node_ops (reinstall/uninstall/detect). The frontend `StepProgress.tsx` renders the bracketed `STEP_GROUPS` (**3-5 / 6-9 / 11-14**) as **collapsible groups**; `DEPLOY_STEPS` mirrors the labels (14); steps 1/2/10 render standalone. Grouping applies only to `DEPLOY_STEPS`, not `RENEW_STEPS`. The old single "SSH verify" is **split across `step_ssh_dualport_verify`**: 7 = poll-for-online, 8 = SSH-connect on new port (rollback/lockout decided here), 9 = cleanup/drop old port; the change-off path advances through 7/8/9 with skip logs. **Execution order: Remnanode → Маскировочный сайт (12) → WARP (13) → Hysteria2 (14)** — masking runs BEFORE WARP (masking mutates `/var/www/html`, must precede WARP's routing). "Hysteria2" is a **label-only** rename of the Certbot-standalone SSL step (`step_certbot_ssl` unchanged). Index consistency (all 1..14 begun, no `IndexError`) is smoke-checked; `StepProgress.test.tsx` asserts groups/order/labels (badges are hierarchical 1./2./3.1…6.4).

**Mode branch (after Step 9):** `if req.mode == "haproxy"` → `step_haproxy_deploy` (reuses step slot **10** via `_begin_step(task, 10, "Установка HAProxy-реле")`, **skips Steps 11–14**): `apt install haproxy`, backup cfg, write `/etc/haproxy/haproxy.cfg` from `_haproxy_cfg(req)` (TCP relay `bind *:$source` → `server $destip:$destport`), `haproxy -c -f` validate (`exit 1`→FAILED), `systemctl restart haproxy`, verify `is-active == active`. Else (remnanode) → normal Steps 10–14. `_effective_open_ports(req)` appends `haproxy_source_port` to the node-accelerator TCP/UDP ports in haproxy mode so the host firewall passes transit traffic.

1. **Подключение к серверу** — connect `SSHSession(ip, current_ssh_port)`; `get_backend_ip()` for whitelists.
2. **Обновление системы** — always `apt-get update`; `apt-get upgrade` only if `update_system`.
3. **Node Accelerator** (`step_node_accelerator`, gated on `optimize`) — `install.sh -s all`, then `git clone` + `protect.sh` with `SSH_PORT`(new or current per toggle)/`TCP_PORTS`/`UDP_PORTS`(=open_ports)/`NODE_PORT`(=remnanode_port)/`REMNAWAVE_URL`/`REMNAWAVE_TOKEN`/`REMNAWAVE_NONINTERACTIVE=1`. If `behind_cdn`: `na-ctguard` enable → `journalctl -t na-ctguard` → `NA_CTG_ENFORCE=1` → `systemctl stop na-fw-safety.timer`.
4. **TrafficGuard** (`step_traffic_guard`) — clone `DonMatteoVPN/TrafficGuard-auto`, run `install.sh` or iptables fallback; whitelist backend IP.
5. **Тест-инструменты** (`step_test_tools`, Ф2-wave1, gated on `install_test_tools` + `skip_components`, **non-fatal** try/except → warn) — installs iperf3 + Ookla speedtest + xray-core via the shared `test_tools.test_tools_install_script()`; runs in both remnanode/haproxy modes. See §7 for the toolkit.
6. **Оптимизация + dual-port SSH + reboot** (`step_system_optimize(ssh, task, backend_ip, req)`) — Reshala kernel hardening, **fail2ban** (backend IP + the normalized deploy **whitelist** in `ignoreip`; sshd-jail `maxretry` = 8 when `allow_ssh_all` else 4), then **`_firewall_extra_script`** (UFW `allow from <ip> to any` per whitelisted IP/CIDR; `allow_ssh_all` → `ufw allow <effective-ssh-port>/tcp` open to any source — never force-enables UFW), ZRAM 40% + 4 GB swap. Whitelist normalized by **`_parse_ip_list`** (split on comma/space/newline, keep valid IPv4/CIDR via `ipaddress`, dedup, drop garbage; `install_vnstat`/`install_trafficguard` gate their steps). THEN if `change_ssh_port` → **Dual-Port strategy**: `_ssh_dualport_config_script(old,new)` opens BOTH ports in UFW (`ufw --force enable`), makes sshd listen on **both** (`Port old` + `Port new`, strips `sshd_config.d` overrides), `sshd -t` (`exit 1` → abort BEFORE reboot), fail2ban `port = old,new`. Then `_reboot_script` (`systemctl reboot --no-block`, nohup fallback) **cold-reboots** the box and closes the session. Rationale: prove the config survives an OS restart rather than trusting a single live session through a port swap.
7–9. **Перезагрузка / Проверка нового порта SSH / Удаление старого порта SSH** (`step_ssh_dualport_verify(...) -> SSHSession`, emits `_begin_step` 7→8→9) — polls for the rebooted server (initial 20s wait, then up to **90s** via `_tcp_reachable` on either port), then branches:
   - **Scenario А** (new port SSH-connects) → finalize via `_ssh_cleanup_newport_script` (drop old `Port`, fail2ban→new, `ufw delete allow old/tcp`, restart sshd), whitelist, **return the new-port session**.
   - **Scenario Б** (new port dead, old port SSH-connects) → `_ssh_rollback_to_old_script` over the old-port session (drop new `Port`, fail2ban→old, `ufw delete allow new/tcp`, restart sshd), raise RuntimeError → FAILED («Смена порта не удалась после перезагрузки… откатана на порт N, доступ сохранён»).
   - **Scenario В** (neither port answers in 90s, or no SSH) → raise RuntimeError (critical network lockout) → FAILED.
   - `change_ssh_port` off → no reboot; whitelist current session, return it.
   - Helpers: `_tcp_reachable(host,port,timeout)` (asyncio.open_connection), `_try_ssh_connect(req,port,timeout)`.
10. **DNS + SSL** (`step_ssl(…, cert_provider)`) — **branches on `cert_provider`**, per-FQDN cert (NEVER a root wildcard), installed to `/etc/ssl/certs/{domain}_fullchain.pem` + `/etc/ssl/private/{domain}.key`; issuance gated on actual `_ecc/*.cer` files (`--force` only when missing):
   - **`cloudflare`** — `upsert_a_record` (CF API) then acme.sh **DNS-01** (`--dns dns_cf --server letsencrypt`, `CF_Token` env). The only provider that manages DNS for us.
   - **`letsencrypt`** — acme.sh **HTTP-01 standalone** (`--standalone --server letsencrypt`); frees port 80 (`fuser -k 80/tcp`). No CF token; DNS must ALREADY point to the node (operator's responsibility — logged).
   - **`zerossl`** — acme.sh **HTTP-01 standalone** against `--server zerossl`, preceded by `--register-account --server zerossl -m <email>` (email-EAB: acme.sh fetches the EAB kid/hmac automatically — no manual EAB entry).
11. **Remnanode** (`step_remnanode`) — write `/opt/remnanode/docker-compose.yml` (`remnawave-nginx:1.28` + `remnanode:latest`) and `nginx.conf` from templates; `docker compose up -d`; verify `remnanode` running.
   - `SECRET_KEY` = the long base64/JWT token (NOT the node UUID).
   - XHTTP `location $path { … grpc_pass unix:/dev/shm/xrxh.socket; }` block included only if `xhttp_path` set, else removed.
   - Cert bridge: symlink `/etc/ssl/...` certs into `/etc/letsencrypt/live/{domain}/`.
13. **WARP Native** (`step_warp`, gated on `install_warp`, **non-fatal** try/except; runs AFTER masking) — uses **wgcf (WireGuard)**, NOT `warp-cli`. Register + generate, then patch `warp.conf`: `Table = off` (prevents default-route hijack that would kill SSH), remove `DNS`, `AllowedIPs = 0.0.0.0/0`, `PersistentKeepalive = 25`, `wg-quick up warp`. (Spec said `warp-cli mode proxy`; distillium/warp-native actually uses wgcf — implemented accordingly.)
14. **Hysteria2** (`step_certbot_ssl` — SSL Certbot standalone, **label-only** rename to "Hysteria2"; functionality unchanged) — write `/opt/certbot/docker-compose.yml`; open + free port 80 (`fuser -k 80/tcp`); `docker run --rm … certbot/certbot certonly --standalone --non-interactive --agree-tos --email $email -d $domain` (check=True → cert failure aborts). Then **awk-REPLACE** the remnanode service's `/etc/letsencrypt` mount with `/opt/certbot/certs` (append would duplicate the mount target → error), `docker compose down && up -d`, cron renew `0 0 28 * *`.
12. **Уникализация маскировочного сайта** (`step_sni_masking`, runs BEFORE WARP) — `set -euo pipefail`, `curl -fL` the `distillium/sni-templates` zip into `/opt/`, random template via `$RANDOM`/`mapfile`, additive obfuscation (`openssl rand -hex 4` + `sed`: inject meta/comment into `<head>`, hidden marker before `</body>`, CSS comment) to change the page fingerprint without breaking markup, deploy to `/var/www/html`, clean up.

## 4. Remnawave API Integration (`remnawave_client.py`)
> **Audited against api-1.json = OpenAPI 3.0.0 "Remnawave API v2.8.0" (2026-07-01).** The integration layer was compared field-by-field (request required/optional fields, response envelopes, enums, pagination params) and is **fully in sync — no breaking changes** in any path we use. Rigorous checks confirmed: every request we send has all required fields and no unknown fields; node name 3–30, config-profile name 2–30 `^[A-Za-z0-9_\s-]+$`; every response still uses the `{ "response": … }` envelope. New v2.8.0 OPTIONAL node request fields exist but are intentionally unused: `proxyUrl`, `nodeConsumptionMultiplier`, `note`.
- `create_node(...)` → `POST /api/nodes` with `port=remnanode_port`, `countryCode`, `configProfile{activeConfigProfileUuid, activeInbounds}`, `activePluginUuid` (single, only when set). Response `uuid` is for **routing only — NOT the SECRET_KEY**.
- `get_node_secret_key()` → **`GET /api/keygen`** → `response.pubKey` = the node SECRET_KEY (the `eyJ…` token). The `POST /api/nodes` response has **no** token field — this was the "bricked node" bug.
- **Squad access fix:** after node creation, `add_inbounds_to_internal_squad(uuid, inbounds)` → `GET /api/internal-squads/{uuid}` then `PATCH /api/internal-squads` with the squad's current inbounds **unioned** with the node's `activeInbounds`. Without this, squad users have no access to the new node.
- Others: `list_node_plugins` (`GET /api/node-plugins` → `response.nodePlugins`), `list_internal_squads`/`list_external_squads`, `add_all_users_to_internal/external_squad`, `list_nodes`, `update_node_traffic`, `get_users_in_squad`, `bulk_update_users_traffic`, `create_config_profile`, **`get_nodes_metrics`** (`GET /api/system/nodes/metrics`, Ф3), **`get_node_users_usage`** (`GET /api/bandwidth-stats/nodes/{uuid}/users`, Ф3), **`create_host`** (`POST /api/hosts`, Ф6). All responses are unwrapped from the `{ "response": … }` envelope.
- **Host auto-create (Ф6, this plan):** at deploy (remnanode + `create_in_remnawave`), `pipeline.step_create_hosts` runs after `create_node` and, for each of the deploy `Template.host_template_ids` MINUS `DeployRequest.disabled_host_template_ids`, loads the local host-template (`hosts.json`) and `POST /api/hosts` with `address=req.domain`, `nodes=[node_uuid]`, `inbound={configProfileUuid: <the node's config profile>, configProfileInboundUuid: <template.inbound (free-text)>}`, `remark=<remark>·<sub>` (**truncated to 40** — Remnawave max), `port`, + `_map_host_optional(tpl)` (local snake_case → CreateHostRequestDto camelCase). **Enum quirks (local→Remnawave):** `security_layer` lowercase→UPPERCASE (`tls`→`TLS`, `none`→`NONE`; `default`/`reality` dropped — reality isn't a host-level enum), `exclude_sub_types` lowercase→UPPERCASE (`xray_json`→`XRAY_JSON`…), `visible=False`→`isDisabled=True` (inverse), `tag` capped 36 chars. Per-host failure → warn + continue (hosts are additive, never fail the deploy); empty `inbound` → skip. Frontend: `DeployForm.tsx` checkbox-list of the selected template's host-templates (unchecked → `disabled_host_template_ids`); `Templates.tsx` template editor has a host-template MultiSelect (persisted via `api/settings.py`). Tests: `backend/tests/test_host_autocreate.py`.

## 4b. Xray-Checker Analytics (Main Dashboard)
> Integration of `kutovoys/xray-checker` for the main dashboard (replaces the old SSH node-monitor). **Reality-check vs the original spec:** xray-checker is a pure Go 1.26 service (`CGO_ENABLED=0`) — **there is NO Fyne/GUI to disable**; it doesn't store history itself (exposes current state via a JSON API + Prometheus). **Chosen strategy (user decisions):** run it as the **official Docker image** (not compile-from-source), feed it a **Remnawave SUBSCRIPTION_URL**, store history in **SQLite**.

- **Docker-out-of-Docker (DooD):** the backend runs in a container, so it manages the sibling `xray-checker` container via the **host** daemon. Setup (all wired): backend `Dockerfile` installs `docker-ce-cli` (client only); `docker-compose.yml` mounts `/var/run/docker.sock` into the backend and gives the network an explicit `name: node-assistant-net`; `XRAY_CHECKER_NETWORK=node-assistant-net` env tells the orchestrator to run the checker with `--network node-assistant-net` and reach it by **container name** (`http://xray-checker:2112`) instead of `127.0.0.1:{port}` (the published host port would not be on the backend's own loopback). `_docker()` catches a missing binary → `_NO_DOCKER`/`container_state()="no-docker"`; the settings-save endpoint returns `{ok, warning}` (200, not 502) when the container can't start.
- **Orchestrator** `services/xray_checker.py` — manages container `xray-checker` via the `docker` CLI: `start` (=`docker run -d --restart unless-stopped -p {metrics_port}:2112 -e SUBSCRIPTION_URL/PROXY_CHECK_INTERVAL/PROXY_CHECK_METHOD/METRICS_PORT=2112 kutovoys/xray-checker:latest`), `stop`/`restart`/`get_logs`/`container_state`, and **`update`** (=`docker pull` → recreate; old container kept if pull fails). HTTP bridge to the checker's JSON API: `GET /api/v1/status` `{total,online,offline,avgLatencyMs}`, `GET /api/v1/proxies` `[{stableId,name,groupName,online,latencyMs,lastCheck}]`, `GET /api/v1/system/info`; responses unwrapped from `{success,data,error}`. **Deep check** = concurrently hit `/config/{stableId}` per proxy (live probe) then re-scrape.
- **Metrics store** `services/metrics_store.py` — stdlib `sqlite3` (no new pip dep) at `DATA_DIR/xray_checker_metrics.db`, table `proxy_samples(ts, stable_id, name, group_name, online, latency_ms, checker_id)` (idx on ts, (stable_id,ts), (checker_id,ts)); schema auto-created on import (the "migration") + an idempotent `ALTER TABLE ADD COLUMN checker_id DEFAULT 'local'` for pre-Ф1 DBs (**Ф1**), **35-day retention** (for 30-day uptime). **In-memory ring buffer** `_RING`/`_META` keyed by **`(checker_id, stable_id)`** (deque maxlen 90) holds the last ticks so the status-page uptime bars are served from RAM; warmed from SQLite on startup (`_warm_ring`, needs sqlite≥3.25 window fn — degrades gracefully). Tick status: `up` / `slow` (latency ≥ `SLOW_MS`=800) / `down`. Queries all take an optional `checker_id` (None=aggregate — **latent stable_id-collision caveat documented on `_cid_clause`, no route calls None**): `get_bars(n,cid)` (ring), `get_uptime_30d(cid)` (per-node + global), `get_incidents(days,cid)`, `get_history(hours,cid)`, `get_node_uptime(hours,cid)`. `record_samples(samples, checker_id='local')`. Async via `asyncio.to_thread`.
- **Checker instance registry (Ф1)** `services/checker_registry.py` — per-account store `accounts/<id>/checkers.json` (`storage.load/save_checkers`) of xray-checker instances `{id, name, kind:'local'|'remote', base_url, enabled, created_at}`. The shared local Docker checker is a **virtual built-in** `id='local'` (always present, not stored, `checker_id='local'` in the DB); **remote** instances are other-server checkers reached over HTTP and polled read-only, each with its own random 12-hex `checker_id` (account-unique → account-scoping via checker_id alone; their live proxies are untagged so `_filter_by_account` is a passthrough). CRUD + `test_connection` + `remote_deploy_script` (SSH docker-run). **SSRF guard `services/net_guard.py`** (`host_is_public`/`is_safe_url`, ported from subs-aggregator's `_host_is_public`) rejects non-public/loopback/link-local/IMDS/private/reserved hosts — applied at registration AND at fetch time (`xray_checker._get_json` when `base_url` set; local container-name URL is exempt), `follow_redirects=False`. `remote_deploy_script` `shlex.quote`s subscription_url + image. `xray_checker.{_get_json,fetch_status,fetch_proxies,fetch_system_info}` accept an optional `base_url` (remote target). Routes: `GET/POST /api/checker/instances`, `PATCH/DELETE /api/checker/instances/{id}` (local id locked → 400), `POST /api/checker/instances/{id}/test`, `POST /api/checker/instances/deploy` (SSH-deploy a checker → register by URL; SSH creds transient). `/status`,`/statuspage`,`/incidents` take `?checker_id=` (default `'local'`; unknown for the account → 404), resolved via `_resolve_instance` (account-scoped). Tests: `backend/tests/test_checker_registry.py`.
- **Poller** `api/xray_checker.poller_loop` — started in `main.py` **lifespan**; per tick samples the shared **local** checker `/api/v1/proxies` (checker_id=`'local'`) if any account has it enabled + running, PLUS each account's enabled **remote** instances (`_sample_remote` → stored under their `checker_id`); interval = smallest enabled `poll_interval` (≥15s). A dead remote is skipped, not retried (loop survives).
- **Multi-account subscription aggregation (Ф8):** a NEW **`subs-aggregator`** compose service (stdlib-only Python HTTP server, `subs-aggregator/app.py` + Dockerfile, on `node-assistant-net`) merges EVERY account's tracked subscriptions into ONE combined subscription the shared checker probes. Each account owns a per-account **subscriptions store** (`storage.load/save_subscriptions` → `accounts/<id>/subscriptions.json`; `Subscription{id,url,background,enabled,last_error}`) with session-gated CRUD (`api/subscriptions.py` → `/api/subscriptions`). The aggregator polls the backend's **ungated** `GET /internal/agg-subs` (only reachable on `node-assistant-net` — compose `expose` without a host port, nginx doesn't proxy `/internal`) for the cross-account active set (`background && enabled` subs; Ф9 layers transient selection). It fetches each upstream, **tags every proxy `account:sub`** in the remark (`#fragment` for vless/trojan/ss; the vmess JSON `ps` field), and serves the combined base64 at `/sub`. **Error policy:** a failed upstream keeps its last-good cached configs and records `last_error`; it is **NOT re-fetched until `POST /refresh`** (a dashboard button → backend `POST /api/subscriptions/{id}/refresh` → aggregator). The checker's `SUBSCRIPTION_URL` points at `http://subs-aggregator:8080/sub` when `SUBS_AGGREGATOR_URL` is set + DooD (`xray_checker._subscription_url`), else the single per-account `cfg.subscription_url`. CRUD does NOT notify the aggregator except on a URL change (the aggregator re-reads the source list every `/sub`; only the per-sub config cache can go stale) — notify is a detached daemon thread so a CRUD response never waits on the aggregator. **Hardening (review fixes):** (a) upstream subscription fetches go through **`_safe_fetch`** (SSRF guard — http/https only, resolves the host and rejects loopback/link-local/IMDS `169.254.169.254`/private/reserved, 4 MB read cap; the trusted internal source URL bypasses it); (b) `SubscriptionCreate/Update.url` reject non-http(s) schemes server-side; (c) the ungated `/internal/agg-subs` + the aggregator's `/refresh` are guarded by a shared **`AGG_TOKEN`** header (compose env, empty=disabled for dev) — defence-in-depth vs. other containers on the shared net (e.g. the pulled-`latest` checker image); (d) the aggregator's `_CACHE` stores each sub's URL and **auto-invalidates on a URL change** (self-healing, doesn't depend on the best-effort notify) and **evicts entries whose sub left the source set** (bounds memory), under a `threading.Lock`. Test escape hatch `ALLOW_PRIVATE_HOSTS=1` (smoke only — the mock upstream is on 127.0.0.1; NEVER set in prod). Verified: `subs-aggregator/test_app.py` (tagging/decoding), `subs-aggregator/smoke.py` (full server: tagged aggregate → break upstream → no-retry → refresh → recovery), `backend/tests/test_subscriptions.py` (store isolation + internal endpoint), `docker build` + `docker compose config`.
- **Settings model** `XrayCheckerConfig` on `AppSettings`: `enabled`, `subscription_url`, `check_interval` (300), `check_method` (ip), `metrics_port` (2112), `image`, `poll_interval` (60). Saved via `POST /api/settings/xray-checker` (also (re)starts the container when enabled).
- **Frontend** `Dashboard.tsx` is a **status-page UI** (original impl of the Uptime-Kuma/Stripe pattern): global health banner (ok green «Все узлы работают стабильно» / partial yellow / down red), 30-day uptime + protocol count + «Перепроверить все ноды» button, **country groups** (collapsible, grouped by `groupName`, flag via `flagFor()` reusing `COUNTRIES` from `CountrySelect`), compact **node rows** (flag+name, protocol badge, **uptime bar grid** of 30/60/90 thin bars green=up/amber=slow/red=down, live ping, 30d uptime%), and an **incident log** («История доступности за 7 дней»). Polls `/api/checker/statuspage?ticks=N` + `/api/checker/incidents` (10s). NOTE: we render our own status page in the SPA (node-assistant is the single aggregating backend) — we do NOT build/proxy the go-build statuspage binary; the checker is the official Docker image.
- **Ф9 — multi-subscription selector on the Dashboard; Ф2 (this plan) — checker config lives in Settings→«Мониторинг»:** `Dashboard.tsx` renders `SubscriptionSelector` (lists the account's tracked subs from `GET /api/subscriptions/status`, polled every 15s: a `background`-toggle checkbox → `PATCH /api/subscriptions/{id}`, a config-count chip, a per-sub `last_error` line + «Обновить» → `POST /api/subscriptions/{id}/refresh`, delete → `DELETE /api/subscriptions/{id}`; an add-row posts `{url, background:true}` to `POST /api/subscriptions`, surfacing the 422 "must start with http(s)" inline) **+ a global `checker_id` `<select>`** in the status-page header (shown only when >1 instance; threads `checker_id` into the `statuspage`/`incidents` fetches → switching refetches). `Settings.tsx`'s old `XrayCheckerSettings` (Deploy tab) was deleted long ago. **Ф2:** the checker config (`CheckerControls` — enable, `subscription_url`, `check_interval`/`check_method` (ip/status/download)/`metrics_port`, Сохранить→`POST /api/settings/xray-checker`, Обновить→`/api/checker/update`, Остановить→`/stop`) was **moved OUT of Dashboard into a new Settings→«Мониторинг» tab** (`Settings.tsx::MonitoringTab`), alongside **`monitoring/CheckerRegistry.tsx`** (the Ф1 instance registry UI: list local+remote, «Подключить по URL», «Развернуть по SSH» (`/instances/deploy`, creds transient), per-instance enable/disable/test/delete, local locked). `components/monitoring/{CheckerControls,CheckerRegistry}.tsx`.
- **Ф9 — per-account filtering of the SHARED checker output (server-side):** the checker container + metrics DB are global, but each account must see only ITS nodes. `api/xray_checker.py` filters `/statuspage`, `/status`, `/incidents` by the active account via **`_filter_by_account`** — it parses each proxy/incident `name` for the Ф8 tag `<account_id>:<sub_id>|<orig>` (**`_parse_tag`**), keeps only entries whose `account_id` matches `accounts.current_account.get()`, and **strips the tag** so the UI shows the clean node name. Global counts (`total/online/state/protocols`) are computed from the filtered set. **Fallback:** when NO proxy is tagged (single-subscription / bare-metal mode, no aggregator), it returns everything unchanged — the dashboard still works without the aggregator. **⚠️ Multi-account REQUIRES the aggregator** (tagged mode): the untagged fallback is all-to-all, so running >1 account with the aggregator disabled gives zero checker-metrics isolation (there's nothing to filter by). The mixed case fails **closed** — if ANY proxy is tagged, untagged ones are dropped (shown to no account). `/statuspage`'s `global.uptime30d` is computed from THIS account's filtered nodes (not the shared-DB aggregate); `/history` stays a global aggregate (carries no names/ids, and the dashboard doesn't render it). `metrics_store` SQL is untouched (filtering is on read, by the name tag). The tag rides only in the `name`/remark, NOT `groupName`. Backend `GET /api/subscriptions/status` merges the aggregator's live per-sub `error`/`count` (from its `/status`) into the account's stored subs (degrades to `error=None` when the aggregator is unreachable). Tests: `backend/tests/test_xray_checker.py` (`_parse_tag`/`_filter_by_account` + `/statuspage` per-account strip/isolation), `test_subscriptions.py` (status-degrade).

## 4c. Инфра-биллинг (Infra-billing) — full 8-tab subsystem
> **Reality:** Remnawave's `InfraBillingController` (v2.8.0) exposes ONLY `/providers`, `/nodes`, `/history` (minimal fields: provider `{name,faviconLink,loginUrl}`; node `{nodeUuid,name,providerUuid,nextBillingAt}`; history `{amount,billedAt,providerUuid}`; `PATCH` takes uuid in body). The other 6 tabs (dashboard/projects/services/payments/settings/api-tokens/sign-in) have **NO Remnawave endpoint** — they're a **local node-assistant subsystem** (user chose "full local subsystem").
- **⚠️ SECURITY OVERRIDE (user-approved, module-scoped):** the API-tokens vault **persists hosting secrets encrypted** (Fernet, key = SHA-256 of `settings.encryption_key`). This intentionally overrides the project rule "no third-party secrets at rest" — ONLY for this module. Secrets are never returned to the client (DTOs expose a masked hint only, e.g. `sel-api****`).
- **No separate PIN gate (removed):** the old finance-PIN sub-lock (`admin_pin_hash`, `POST /auth/verify-session`, `X-Billing-Session` header, `InfraSignIn` tab, in-memory `_SESSIONS`) is **fully deleted**. Since the whole panel now requires an account (§1b), infra-billing is just part of the account's isolated data — the billing DB is per-account (`accounts/<id>/infra_billing.db`). All infra routes are gated by `require_account` like every other data route.
- **Store** `services/infra_billing_store.py` — stdlib SQLite `DATA_DIR/infra_billing.db`, tables: `provider_meta(provider_uuid, balance, currency, low_balance_threshold, api_token_id, status)`, `node_meta`, `projects(id,name,description,node_uuids json,created_at)`, `services(id,name,kind,node_uuid,provider_uuid,project_id,billing_type[fixed|hourly],cost,next_billing_at,created_at)`, `payments(id,ts,provider_uuid,project_id,type[charge|topup|adjustment],amount,currency,status[success|pending|error],note)`, `api_tokens(id,name,provider_kind,secret_enc BLOB,created_at)`, `billing_settings(k,v)`. FX = RUB-anchored rates in settings; `_convert()` for base-currency aggregation.
- **Routes** `api/infra_billing.py` (`/api/infra-billing`, all under `require_account`): `GET /dashboard/summary` (total balance in base currency, burn-rate hourly/daily/monthly + daysLeft/critical<7, spend pie + monthly line); providers `GET/POST /providers`,`PATCH/DELETE /providers/{uuid}` (`?force=` cascade guard); projects/services CRUD; **payments** `GET/POST/DELETE` + **api-tokens** `GET/POST/DELETE` + `POST /api-tokens/{id}/verify`; `GET/PUT /settings`. No PIN/session sub-gate anymore (see above). Notify hook `services/infra_notify.py` fired from dashboard.
- **Frontend** `components/infra/`: shared `api.ts` (typed fetch; auth is added globally by the fetch interceptor — no per-call token), `ui.tsx` (Page/PageHeader/Field/SelectField/Modal/fmt/`loadDeployNodes` from localStorage `deploy_jobs_<accountId>`), `Toast.tsx` (`<Toaster/>` in `App.tsx`), and **7 pages** (Sign-in removed): `InfraDashboard` (inline-SVG donut+line, balance+burn widgets), `InfraProviders`, `InfraProjects` (card grid + node MultiSelect), `InfraServices` (table+modal), `InfraPayments` (ledger, filter), `InfraSettings` (base currency, FX rates, threshold, refresh interval — no PIN), `InfraApiTokens` (masked vault + «Проверить соединение»). Sidebar `Tab` union: `infra-dashboard|providers|projects|services|payments|settings|tokens` (no `signin`); rendered as a **flat «Инфра-биллинг» section** (Ф7 — the old `InfraGroup` accordion was removed).
- **Not implemented (stubs, documented):** live hosting-API balance verification & real FX feed & bot Anton — provider `status` and token `verify` are best-effort (secret decrypts; no per-hosting adapter); FX rates are manual; balances are entered manually.

## 4d. User statistics (Ф3) — per-account node-load history + best-effort migrations
> **Data reality (api-1.json):** there is NO "which user is on which node right now" endpoint. `GET /api/system/nodes/metrics` gives per-node **`usersOnline` (a count)** — the reliable signal; `GET /api/bandwidth-stats/nodes/{uuid}/users` gives **topUsers by cumulative usage** (approx membership). So node-load/busiest are exact; per-user sessions/migrations are **best-effort** (UI badge «оценка»).
- **Store** `services/user_stats_store.py` — per-account SQLite `accounts/<id>/user_stats.db` (explicit-`account_id` pattern like `storage.py`, NOT ContextVar-only — the collector has no request context; lazy per-path schema under a lock like `infra_billing_store`). Tables `node_load_samples(ts,node_uuid,node_name,users_online)`, `node_top_users(ts,node_uuid,username,total_bytes)` (+indices), 35-day retention on write. Async fns (`account_id: Optional`): `record_snapshot`, `node_load(hours)` (per-node usersOnline series + avg/peak/current, busiest-first), `top_users(hours)` (top by usage, LIMIT 20), `migrations(hours)` (best-effort dominant-node-change per username → `{from_node,to_node,count}`, `approximate:True`).
- **Collector** `api/user_stats.collector_loop` — lifespan background task (mirrors `poller_loop` resilience: per-account try/except, explicit `account_id`, skips accounts w/o `panel_url`+`api_token`, never dies), every **300s** snapshots each account's nodes; per-node top-users fetched **concurrently** (`asyncio.gather(return_exceptions=True)`, capped `_TOP_USERS_CAP=20`). `remnawave_client.get_nodes_metrics()` / `get_node_users_usage(uuid)`.
- **Routes** (`/api/stats/users`, under `require_account`): `GET /node-load|/top-users|/migrations?hours=` (clamped 1–720). Tests: `backend/tests/test_user_stats.py`.
- **Frontend (Ф4)** — Sidebar «Статистика» group → «Пользователи» (Tab `stats-users`, routed in `App.tsx`). `components/stats/UsersStats.tsx` = 6 inline-SVG widgets (no chart lib, CSP self-contained): a) node load over time, b) avg/busiest, c) top users by usage, d) migrations from→to (badge «оценка», `approximate`), e) most stable (xray uptime30d), f) fastest (xray latency). Each has a gear (`stats/WidgetSettings.tsx`): a–d time-window (24h/7d/30d), e–f `checker_id` selector (from `/api/checker/instances`) — per-widget refetch. Empty/loading/error states; stacks 1-col ≤820px.
- **Widget editor (Wave-5 Plan G):** `UsersStats.tsx` refactored to a **registry** (`WIDGETS: kind→component`) + a per-account **layout store** `stats/statWidgetsStore.ts` (zustand): `{instanceId, kind, w:1|2, order, settings}`, hydrate (server→localStorage→`DEFAULT_LAYOUT` of the 6 widgets), `add/remove/resize/move`, debounced persist (localStorage mirror + `PUT /api/stats/users/widgets`). Backend `GET/PUT /api/stats/users/widgets` — на **`user_stats.router`** (`api/user_stats.py`, подключён в `main.py`), НЕ на `stats.router`; `WidgetInstance` pydantic (closed-enum `kind`, `w` 1–2, ≤40), store `storage.load/save_stat_widgets` → `accounts/<id>/stat_widgets.json`. ⚠️ Бэкендный энум `_WIDGET_KINDS` содержит **8** типов, фронтовый `WIDGET_KINDS` — **6**; лишние молча отбрасываются в `normalize` при гидрации (не баг, но помнить при добавлении виджета: править надо ОБА списка). «Редактировать» toggle in the page head → per-widget move ↑/↓ (splice-reorder, no DnD lib), width 1↔2, two-click delete; «+ Виджет» palette (dups allowed). Empty layout migrates to the current 6-widget view. Tests `test_stat_widgets.py`. (Deferred: lifting per-widget window/checker settings into the store — they stay local for now.)
- **Скрытие серверов в статистике (Волна 6, План B):** соседний ключ **`hidden`** на том же документе виджетов —
  `{nodes: {node_uuid: name}, checker: {checker_id: {stableId: name}}}`. **ДВЕ оси не по прихоти:** node-load/
  avg-per-node/migrations ключуются на Remnawave `node_uuid`, а stable-nodes/fast-nodes — на `stableId`, который
  уникален только ВНУТРИ своего чекера (один и тот же `n1` у local и remote — разные узлы). Значение = последнее
  известное имя, чтобы пикер показывал пропавшую запись человеку, а не голый uuid. Набор **page-global на аккаунт**
  (не per-instance: палитра допускает дубликаты виджетов, наборы разъехались бы). Лимиты 200/20/64 продублированы
  на клиенте (`normalizeHidden`) — localStorage правится руками. Фильтрация **клиентская** (эндпоинты и так отдают
  всё) и применяется **ДО срезов top-N**, иначе скрытая нода занимала бы место видимой. `filterMigrations` режет
  строку по ЛЮБОМУ концу. **«Топ пользователей» НЕ фильтруется** (`_top_users` группирует по username и теряет
  node_uuid — нужен новый backend-параметр, отложено). `cid='server-monitor'` — passthrough на клиенте, там
  подавление делает бэкенд (§9b), иначе считалось бы дважды. ⚠️ `PUT` — **full-replace**: `persist()` обязан слать
  `{layout, hidden}` ЦЕЛИКОМ, иначе перестановка виджета сотрёт набор скрытых. localStorage мигрирует со старого
  формата (голый массив layout). UI: кнопка «Серверы» с бейджем в шапке (доступна ВНЕ режима редактирования) →
  `stats/HiddenServers.tsx`, группа «Не найдено в текущих данных» для записей, ушедших из выдачи (страховка от
  смены `stableId`). `State` отличает «Данных пока нет» от «Все серверы скрыты».

## 5. Backend Routes
- **Xray-Checker:** `GET /api/checker/status|history|statuspage?ticks=N|incidents?days=N|logs`, `POST /api/checker/check|update|start|stop`; `POST /api/settings/xray-checker`.
- Settings: `GET /api/settings`, `POST /api/settings/{remnawave,optimization,deploy-defaults,appearance}`, `POST /api/settings/remnawave/check`.
- **Панели Remnawave (Wave-5 План K):** `GET/POST /api/settings/remnawave/panels`, `PUT/DELETE /api/settings/remnawave/panels/{id}`, `POST /api/settings/remnawave/panels/{id}/activate`. Модель: `RemnawaveRegistry{panels:[PanelEntry], active_panel_id}` на `AppSettings`. **`AppSettings.remnawave` теперь вычисляемое представление АКТИВНОЙ панели** — `@model_validator(mode="after")` проецирует активную запись в `.remnawave` (legacy single-config автомигрируется в реестр с id `"primary"` при первом `AppSettings(**raw)`; битый `active_panel_id`→первая; пусто→пусто). ⇒ ~13 сайтов, читающих `.remnawave`, НЕ тронуты — работают на активной панели. `POST /settings/remnawave` (старая ручка) пишет в активную запись (обратная совместимость). Токен панели — plaintext в settings.json (статус-кво). Frontend `Settings.tsx::PanelSelector` (список/активация/удаление/добавление; форма редактирует активную). Тесты `test_settings_panels.py`. (Отложено: сайдбар-переключатель + «Из развёрнутых» из panel_jobs.)
- **API-токены (План H):** `GET/POST /api/api-tokens`, `DELETE /api/api-tokens/{id}` (per-account, HMAC-хеш, show-once; `require_account` принимает `nai_`-токен как bearer — см. §1b).
- **Библиотека (Wave-5 План C, scoped):** `GET /api/library`, `POST /api/library/upload` (multipart), `POST/PUT /api/library/notes(/{id})`, `GET /api/library/notes/{id}`, `GET /api/library/files/{id}` (download), `DELETE /api/library/{id}`. Per-account файлы + markdown-заметки (`services/library_store.py` → `accounts/<id>/library/`: `index.json` + `files/<id>_<name>`; 25 МБ/файл, ≤500, traversal-guard). Заменяет заглушку Плана A в nav-группе «Справка» (`components/Library.tsx`, `Tab 'library'`). **Отложено (heavy-deps):** извлечение текста (pdf/docx/xlsx), FTS5-поиск, богатые вьюеры — сейчас загрузка/скачивание/удаление + md-заметки (текстовый редактор). Тесты `test_library.py`.
- **Экспорт/импорт (Wave-5 План L, срез 1):** `GET /api/export/stores`, `POST /api/export` (→ `.tar.gz`: manifest + `data/<store>.json` по 15 per-account JSON-сторам, **секреты стрипаются** по умолчанию — settings-токены/`*_enc` зануляются, netbird.json исключается; `include_secrets=true`→400 пока), `POST /api/import` (multipart, `confirm=true` обязателен → replace-per-store; settings.json мержится, сохраняя учётные секции цели). `services/export_service.py`. Frontend Settings→«Экспорт/импорт» (`settings/DataTransfer.tsx`). Тесты `test_export_io.py`. **Отложено:** SQLite-дампы (server_monitor/speedtest/user_stats/infra_billing/rules_secrets), password-шифрование архива с секретами, срез 2 (снимок/восстановление Remnawave-панели через API + новый `create_user`).
- **Пользовательские конфиги (Wave-5 План D):** `GET/POST/PUT/DELETE /api/config-templates(/{id})` + `POST /reorder` + опц. `POST /{id}/export`·`GET /import/panel`·`POST /import/panel/{uuid}` (экспорт/импорт в Remnawave subscription-templates, гейт «панель не настроена»→400). Локальный per-account стор `config_templates_store.py` (`config_templates.json`, 6 типов клиента: xray-json/xray-base64/singbox — JSON-ядро `content_json`; mihomo/clash/stash — YAML-ядро `content_yaml`; base64 только на границе панели). `remnawave_client` получил `{list,get,create(2-шага),update,delete}_subscription_template`. Frontend `components/configs/*` (nav-таб «Конфиги» в **группе «Remnawave»**, CRUMB `["Remnawave","Пользовательские конфиги"]` — Волна 6, План A): группировка по типу, каркас `тип→редактор` — xray-json/JSON-ядра открываются в нашем `profiles/JsonEditor` (schema-валидация, stateless — НЕ трогает `xray_profile_<acc>`), YAML-ядра — textarea (mihomo: плашка «редактор в Плане E»). Тесты `test_config_templates.py`.
- Remnawave proxies: `GET /api/remnawave/squads/internal`, `…/squads/external`, `GET /api/remnawave/node-plugins`, `GET /api/remnawave/nodes`.
- Templates CRUD: `/api/templates`. Traffic rules: `/api/traffic-rules` (+ `/{id}/sync`).
- Deploy: `POST /api/deploy`, `POST /api/deploy/stop`.
- **SSL management (Ф10):** `POST /api/certs/deploy` (`DeployCertRequest` — ip/ssh/domain/**cert_provider**/email/cf_api_key/**force**; deploys a per-FQDN cert via the chosen provider, reusing `pipeline.build_ssl_script`; probes the installed cert with openssl and SKIPS unless `force`; streamed 3-step task); manual-domains store `GET/POST/DELETE /api/domains` (`domains.py`, per-account `domains.json`).
- **Cert download (Ф8, this plan):** `POST /api/certs/download` (`DownloadCertRequest` — ip/ssh creds/**domain** (FQDN-allowlist `_DOMAIN_RE.fullmatch`)/`files:[fullchain|key]`) → `_read_remote_file` base64-reads the selected files **silently** (`ssh.get_output`, no Task → the private key never hits a log; read capped `head -c 8 MiB`); single file → `application/x-pem-file`, multiple → in-memory `zipfile` `StreamingResponse` `{domain}-certs.zip`. Empty `files`→422, missing→404, SSH fail→502. Frontend: `DomainsPanel.tsx` per-row `DownloadCtl` (checkboxes fullchain/приватный ключ, amber HTTPS-warning on key, reuses deployed row's saved SSH creds; manual domains disabled). Creds per-request, never persisted. Tests: `test_certs.py` (injection/missing/empty/pem-vs-zip).
- Subscriptions: `GET/POST /api/subscriptions`, `PATCH/DELETE /api/subscriptions/{id}`, `POST /api/subscriptions/{id}/refresh`, `GET /api/subscriptions/status` (session-gated); `GET /internal/agg-subs` (**ungated**, internal-network only — see §4b Ф8).
- **Hosts (Ф11 — «Хосты» nav tab):** `GET/POST /api/hosts`, `PUT/DELETE /api/hosts/{id}` — per-account **local** Remnawave-host templates (`hosts.py` + `models/hosts.py::HostTemplateBody`, `accounts/<id>/hosts.json`; NO Remnawave API — templates are applied later at deploy time). Frontend `Hosts.tsx` = a list of saved templates + a modal editor with a **2-tab `.seg` (Основные / Расширенные)** form mirroring the Remnawave host form 1:1 (~25 fields: visible/remark*/inbound/address*+port*/tag/nodes(MultiSelect from deploy_jobs)/exclude_squads + SNI/sni_from_address/sni_empty/host+path/security_layer/alpn+fingerprint/vless_route_id/hide_host/exclude_sub_types(6 checkboxes)/xray_json_template/xhttp+mux+sockopt+final_mask(raw-JSON sub-editors)/server_description(≤30)/shuffle_host/allow_insecure/x25519mlkem768). Theme-aware (var-tokens); `MultiSelect` is now token-converted (no longer a dark island in light/apple-light). **Inbound + Xray-JSON-template are free-text inputs** (no Remnawave API to populate a selector — set manually). `vless_route_id` is `Field(0, ge=0, le=65535)` (0=off). The raw-JSON sub-editors (xhttp/mux/sockopt/final_mask) report parse errors up so a malformed one **blocks save** (no silent drop); `canSave` also checks the port upper bound (≤65535). Tests: `test_hosts.py` (CRUD + isolation + required-field/route-id bounds + shell-safety). **Wave-5 Plan F:** `host`/`sni`/`path` are now **shell-safety-validated** (`HostTemplateBody` field_validators via `services/http_headers.py::is_safe_host`/`is_safe_path` — hostname/path allowlist, reject metacharacters + CR/LF) — closes the old «Forward note» before any deploy-time apply-template path exists. **Reusable `HeadersEditor` (Wave-5 Plan F):** controlled key-value HTTP-headers editor `frontend/src/components/common/{HeadersEditor.tsx,headers.ts}` (RFC-7230 name validation, CR/LF-strip, presets, reorder) — wired into the Xray `profiles/ItemModal.tsx` for ws/httpupgrade/tcp transports (`streamSettings.*.headers`, keyed by network, raw-JSON block stays as escape-hatch). (Deferred: `Hosts.tsx` xhttp.headers UI + subscription `customResponseHeaders` proxy — optional Ф4/Ф5.)
- Node stats/ops: `POST /api/stats/node` (security + traffic + cert-expiry, creds-per-request); `POST /api/node/step` (`node_ops.py` — per-component reinstall/reconfigure/uninstall against a live node; `NodeOpRequest(DeployRequest)` + `component`/`action`; reinstall reuses `pipeline.step_*`, uninstall = `_UNINSTALL_SCRIPTS`; streamed via a Task over the generic `/ws/logs/{task_id}`).
- **Ready-node detect (Ф5, this plan):** `POST /api/node/detect` (`node_ops.py` — `NodeDetectRequest` creds-per-request, one SSH session runs `_DETECT_SCRIPTS` read-only probes per `Component`, `_parse_detect` → `{results:{component: present|absent|unknown}}`; per-probe failure → unknown, connect fail → 502). Frontend `DeployDashboard.tsx` «Существующий сервер» modal → detect → checklist (present pre-checked as skip) → deploy with **`DeployRequest.skip_components: list[str]`**. In `run_pipeline`, `_skip_component(task,idx,comp,label)` skips a manageable step (node_accelerator/trafficguard/**test_tools**/ssl/haproxy/remnanode/masking/warp/hysteria2) if its component is in `skip_components` (mirrors `install_vnstat=false` gating; SSH-port hardening + `step_system_optimize` stay UNconditional). `StepProgress.tsx` shows cosmetic hierarchical numbering (1/2/3.1…6.4) over the same **14-index** mapping. `_trafficguard_fallback` iptables rules now `iptables -C … || -A …` (idempotent re-run). Domain/email validators (`node_ops.py`, `models/deploy.py`) use `re.fullmatch` (reject trailing-newline). Tests: `backend/tests/test_node_detect.py`.

## 7. Wave 1 — тест-серверы, speed-тесты, Remnawave-модуль (панель/подписка/переменные/бэкап)
> План `docs/superpowers/plans/2026-07-07-remnawave-panel-wave1.md` (Ф1-Ф9). Всё per-account, creds-per-request, secrets-not-at-rest. Заглушки Волны 2: `rw-migration`/`rw-profiles`.

### 7a. Test tools + test servers (Ф1)
- **`services/test_tools.py`** — ЕДИНЫЙ инсталлер (потребляют деплой ноды/панели + тест-серверы): `test_tools_install_script()` (iperf3 + Ookla speedtest c python-fallback + xray-core, опц. сбои→`[warn]`), `speedtest_run_script()`, `iperf_server_script`/`iperf_client_script` (`shlex.quote`), **`parse_xray_link`** (vless/trojan/vmess/ss → xray-конфиг socks 127.0.0.1:10808; ошибки без фрагментов ссылки), `xray_link_speedtest_script` (`mktemp`-конфиг+trap). **⚠️ Share-ссылки/секреты → `SSHSession.get_script_output` (stdin, НЕ argv) — не в `/proc/cmdline`/логе.**
- **`services/testserver_registry.py`** — per-account `testservers.json` (паттерн `checker_registry`); `deploy_script`=инсталлер+iperf3-сервис+UFW-allowlist; дубликат ip+port→409. Routes `api/testservers.py` (CRUD+`/deploy` стрим-Task), Settings-вкладка «Сервера для тестирования».

### 7b. Node speed tests + matrix (Ф2/Ф2b)
- **`services/speedtest_store.py`** — per-account SQLite `node_speedtests.db` (explicit account_id, retention 90д, `kind`=node/pair/xray); `record_run`/`history`/`history_by_kind`/`latest`.
- **`POST /api/stats/node-speedtest`** (Ф2) — одна SSH-сессия: lazy-install→характеристики→speedtest→iperf3→xray-туннель; `_INFLIGHT`-lock `(account,ip)`→409; битая ссылка→422 без утечки. `DeployCard` блок «Характеристики и скорость». Тумблер `install_test_tools` (дефолт true).
- **`POST /api/speedtest/{pair,xray,history}`** (`api/speedtest.py`, Ф2b) — матрица «любой↔любой»: A=iperf3-клиент; **B=нода/панель→эфемерный `iperf3 -s`+UFW allow с IP A, cleanup в `finally`+`timeout 300` backstop**; B=testserver→прямое подключение. Парсеры из `api/stats`; A==B guard (нормализ. IP). `stats/SpeedTests.tsx` (Tab `stats-speedtests`).

### 7c. Remnawave install (Ф3-Ф7)
- **`models/panel_deploy.py::PanelDeployRequest`** (+`SubServer`): `target` panel/subpage/both, `reverse_proxy` **caddy|nginx** (отклонение: не 4 прокси — traefik/angie в Волну 2), `cert_provider`+`cf_api_key`, webhooks, `extra_env` (**protected secret-keys нельзя override**), `sub_server` (separate, только target=both), `subpage_html`. `validate_by_target`: cf_api_key/email обязательны только для nginx.
- **`services/panel_pipeline.py`** — 8 шагов `PANEL_STEP_LABELS` (локально, НЕ в task_store): connect→docker→test-tools→**`.env` (`secrets.token_hex`; `__ENV_EXISTS__`-guard — reinstall НЕ регенерит секреты поверх БД; `umask 077`)**→compose (backend:2+postgres:18.4 TZ=UTC+valkey; статичный YAML)→reverse-proxy (caddy авто-TLS/nginx через `build_ssl_script`)→up+verify→subscription-page (bundled/separate). **CF A-запись на IP настраиваемого бокса (`ssh.host`), не всегда панель.**
- Routes `api/panel_deploy.py`: `/api/panel/{deploy,detect,step,env/read,env/write}`. `step`: reinstall/uninstall (docker-uninstall запрещён; `compose down` без `-v` — том БД жив; `PanelOpRequest` ре-валидирует savedForm).
- **Frontend `rw/`:** `PanelDashboard` (`panel_jobs_<id>`, **стабильный `id` переживает retry**), `PanelWidget` (2 подрамки, `useTaskStream`; **клик-управление гейтится на `success`** — гонка с `run_panel_pipeline`), `PanelDeployForm` (`validatePanelForm` зеркалит сервер 1:1), `PanelManageModal` (Компоненты + Статистика).

### 7d. Subscription-page catalog / Variables / Backup (Ф5/Ф8/Ф9)
- **⚠️ Страница подписок: ПРОВЕРЕНО НА ОБРАЗЕ 7.2.6 (Волна 6, План E Ф1).** `remnawave/subscription-page`
  НИКОГДА не был «одним HTML»: `/opt/app/frontend/` — собранная Vite/React SPA (160 файлов, 6.9 МБ:
  `index.html` + `assets/`), а `index.html` — **EJS-шаблон** с `<%- panelData %>`, `<%= metaTitle %>`,
  `<%= metaDescription %>`. Наш прежний монтаж произвольного `index.html` поверх него ТИХО убивал единственный
  канал данных страницы. Теперь `subpage_html` без `panelData` отвергается моделью (и формой). Второе:
  **`REMNAWAVE_API_TOKEN` обязателен** — с пустым контейнер падает кодом 1 («Environment Configuration Errors»,
  воспроизведено запуском). Добавлено обязательное поле `PanelDeployRequest.subpage_api_token` для
  target ∈ {subpage, both} (не персистится у нас — уходит в `.env` на боксе тихим каналом) и поле в форме.
  Образ **пиннится** (`subpage_image`, дефолт `7.2.6`) вместо `:latest`: шаблон/overlay верны только для
  конкретной версии фронтенда.
- **Каталог Orion (Ф5):** `services/subpage_store.py` (per-account HTML, `threading.Lock` + лимит 512KiB/`MAX_PAGES=100`, membership-guard от traversal), `api/subpages.py` CRUD, `rw/SubPages.tsx` (iframe `sandbox=""` srcDoc). **`/raw` → CSP `sandbox`+`nosniff`** (latent-XSS при открытии в новой вкладке).
- **Переменные (Ф8):** `/api/panel/env/{read,write}` — маскировка `_is_secret_env_key` (**`PASS`**/SECRET/TOKEN/KEY/PWD/PRIVATE/CREDENTIAL/DATABASE_URL — ловит `METRICS_PASS`), merge через тихий канал (нетронутый секрет не затирается пустым), `restarted` по `docker ps` (не rc). `rw/PanelVariables.tsx`.
- **Бэкап (Ф9):** `services/backup_service.py` — distillium-обёртка (самодостаточный wrapper, НЕ клон): `config.env` секреты в **одинарно-кавыченном heredoc** `<<'RWCFG_EOF'`+`umask 077`+`_shell_safe`; restore за confirm-гейтом; дампы БД 0600. `api/backup.py`: `/api/backup/{setup,run,restore,status}` (restore без confirm→400; run/restore **rc→FAILED**; GD через `RCLONE_CONFIG_DRIVE_*`). `rw/Backup.tsx` (двойной confirm + «TLS не бэкапится»).
- **Секреты панели/бэкапа на ЦЕЛЕВОМ сервере** (`.env`/`config.env` 0600), не в нашей БД; SSH-креды панелей в `panel_jobs_<id>` localStorage.

## 6. Troubleshooting & Quirks (read before touching the pipeline)
- **Pipeline is 14 steps (Wave-1 added step 5 «Тест-инструменты»):** when adding/moving a step, `grep -n "_begin_step\|_skip_component\|STEP_LABELS\|DEPLOY_STEPS\|STEP_GROUPS"` and re-check that indices 1..14 begin exactly once in BOTH modes and both `change_ssh_port` paths; haproxy reuses slot 10 / skips 11–14. Silent secrets/share-links (panel `.env`, xray-links, backup `config.env`) go through `SSHSession.get_script_output` (stdin, not argv) with single-quoted heredocs — never `run_script` (which logs) and never argv (`/proc/cmdline`).
- **Let's Encrypt rate limit:** issue **per-FQDN** certs, never the root wildcard (`root` + `*.root` is the SAME identifier set for every node → 5 certs/168h `429 rateLimited`). `$domaincert` is the FQDN now; the old `_root_domain()` helper was removed.
- **acme.sh `--list` is unreliable:** a stale/partial (or prior RSA) registry entry can show "issued" while `_ecc/*.cer` is missing → `--install-cert --ecc` fails (exit 2). Gate on the real files; `--force` only when absent; verify installed files are non-empty.
- **Cert providers (`step_ssl` branches on `cert_provider`):** `cloudflare` = DNS-01 (`--dns dns_cf`, upserts the A record for us). `letsencrypt`/`zerossl` = **HTTP-01 standalone** — they need port 80 free (`fuser -k 80/tcp` first) and the FQDN must ALREADY resolve to the node (no CF API to set DNS). **ZeroSSL requires an EAB-bound account**; we register by email (`acme.sh --register-account --server zerossl -m <email>`) which makes acme.sh fetch the EAB kid/hmac automatically — no manual EAB kid/hmac field needed. If a future environment demands explicit EAB, add optional kid/hmac fields to the deploy form and pass them to `--eab-kid`/`--eab-hmac-key`.
- **`cloudflare` and `letsencrypt` use the SAME CA** (`--server letsencrypt`; they differ only in the challenge — DNS-01 vs HTTP-01), so their certs are interchangeable. **Only `zerossl` is a different CA.** The skip-re-issue guard therefore keys off a **CA marker** (`zerossl` vs `letsencrypt`) grepped from acme.sh's `{domain}_ecc/{domain}.conf` (`Le_API`): a retry that switches provider ACROSS CAs (→/from zerossl) re-issues instead of silently installing the old CA's cert; a cloudflare↔letsencrypt switch correctly reuses.
- **`domain`/`email` are shell-safety-validated server-side** (`DeployRequest.field_validator`): they're interpolated into root-run bash (acme.sh install, zerossl register, cert paths), so the model rejects any value outside `[A-Za-z0-9.-]` hostname / a strict email charset (empty allowed for haproxy mode; presence enforced by the mode validator). This closes shell-injection in both `step_ssl` and the pre-existing `step_certbot_ssl`. Regression: `backend/tests/test_deploy.py` (`test_domain_rejects_shell_metacharacters`/`test_email_rejects_shell_metacharacters`).
- **`allow_ssh_all` + `change_ssh_port`:** the dual-port script already opens the new port to all sources (`ufw allow {new}/tcp`), so `_firewall_extra_script` emits its own SSH-open-to-all rule **only when `change_ssh_port` is off** — avoids a duplicate UFW rule that a Scenario-Б rollback's single `ufw delete` wouldn't fully remove.
- **New deploy flags gate their steps:** `install_vnstat` (default true) gates the step-2 vnstat install AND the DeployCard traffic block (`savedForm.install_vnstat !== false`); `install_trafficguard` (default true) gates `step_traffic_guard`. Whitelist/UFW behavior lives in `step_system_optimize` (`_parse_ip_list` → fail2ban `ignoreip` + `_firewall_extra_script`). All these script generators are unit-smoked in `backend/tests/test_pipeline_scripts.py` (imports pipeline with `asyncssh` stubbed; runs on the global pydantic).
- **`na-ctguard` is owned by Node Accelerator, NOT TrafficGuard:** the CDN-guard iptables rules (comment-marked `na-ctguard`) are installed by `step_node_accelerator`'s `behind_cdn` block, not `step_traffic_guard` (which clones `DonMatteoVPN/TrafficGuard-auto`). So the **Ф7 uninstall** flushes `na-ctguard` in `_u_node_accelerator` (owner), while `_u_trafficguard` only removes the `/opt/TrafficGuard-auto` clone. Don't move the flush back to trafficguard — that regressed CDN guard on a TrafficGuard uninstall (fixed).
- **Remnanode reinstall for auto-registered nodes:** `create_in_remnawave` nodes never stored the token client-side (it was fetched server-side via `GET /api/keygen`). `node_ops._reinstall` re-fetches the (stable) SECRET_KEY via `_fetch_node_secret_key` (needs Remnawave configured in the account's settings) when the saved token is empty and `create_in_remnawave` is set.
- **Backend is a centralized-tests repo:** tests live in `backend/tests/` with non-mirror names (e.g. `test_pipeline_scripts.py` covers `pipeline.py`), so `MAIN_SKILL_VERIFY_IGNORE_GLOBS` ignores `**/backend/app/**` (in `.claude/settings.local.json`). Coverage is real — run `python -m pytest` in `backend/` (needs `pydantic-settings httpx fastapi bcrypt asyncssh` on the global Python 3.14; the pinned pydantic 2.7.1 has no 3.14 wheel but the global pydantic 2.12 works). Frontend keeps the mirror convention (`Foo.test.tsx`).
- **SECRET_KEY source:** `GET /api/keygen` `pubKey`, NOT `POST /api/nodes`. Manual token from the form is passed through unchanged.
- **Certbot ↔ remnanode mount:** remnanode already mounts `/etc/letsencrypt`; Docker rejects two mounts on one target. Step 10 **awk-replaces** the remnanode block's mount (scoped to the `remnanode:` service only; nginx mounts untouched) — idempotent.
- **WARP kills SSH if naive:** plain WARP/`wg-quick` injects a default route and drops the panel's SSH. Use wgcf with `Table = off`. `warp-cli` CLI changed in 2024 (`registration new`, `mode`, global `--accept-tos`) — we avoid it by using wgcf.
- **SSH port change = Dual-Port + reboot (current strategy):** Step 5 makes sshd listen on BOTH old+new ports, validates (`sshd -t`), then **cold-reboots** the server; Step 6 polls (20s + up to 90s) and decides Scenario А (new works → cleanup, keep new) / Б (only old works → rollback, FAILED) / В (lockout → FAILED). This survives the case where the new port fails to bind *after an OS restart* — which a same-session swap could not catch. (Superseded the earlier "parallel Session #2 test, never close Session #1" approach; an established TCP session survives a plain `sshd restart`, but NOT a reboot, hence the poll-and-reconnect design.) `reboot` is issued detached (`systemctl reboot --no-block`) so the run returns before the connection drops.
- **DeployForm prefill:** pass NO `initial` for new deploys, else the settings-overlay (`if (!initial)`) is skipped and email/Cloudflare/XHTTP stay empty.
- **Dashboard `global:{}` guard (white-screen fix):** `/api/checker/statuspage` can return `container:"running"` with an **empty `global:{}`** (checker up but HTTP-unreachable — `reachable:false` + `error` set), even though the `StatusResp.global` type declares a full `Global`. `Dashboard.tsx` must gate on **`g?.state`** (not just `g` truthiness) — an empty global has no `state`, so `BANNER[undefined].cls` (and `g.protocols.length`/`.join`) threw and crashed the whole SPA (no error boundary). All banner/protocol reads use `g?.state`/`g?.protocols?.` and degrade to the "unknown" banner. Regression test: `frontend/tests/Dashboard.test.mjs` (`npm test`) — framework-free node script (repo has no runner) that models the guard expressions.
- **Template substitution:** replace `$domaincert` BEFORE `$domain` (`$domain` is a prefix → would corrupt). Only system vars (`$domain`/`$domaincert`/`$path`/`$nodeport`/`$token`) are replaced; native nginx vars (`$http_upgrade`, `$proxy_add_x_forwarded_for`, …) must pass through untouched.
- **Bash via `bash -s`:** `$RANDOM`, `mapfile`, arrays work. In Python f-string scripts, literal braces need `{{}}`; keep `awk` programs in **non-f** strings to avoid brace clashes.
- **Country/location rendering (frontend standard):** always render ISO alpha-2 codes as flag emoji via **`getFlagEmoji(code)`** in `frontend/src/utils/format.ts` (regional-indicator conversion; empty/null/invalid → `🌐`). Never show raw codes (`US`, `DE`) in the UI. Wired into `CountrySelect` (dropdown rows + selected input), `Dashboard` status-page node rows (`flagFor` routes group/code/embedded-emoji through it), and `InfraProviders` (flag shown when a provider has an optional `countryCode`). Windows caveat: some builds show the two letters instead of a flag (OS font limit) — swap for an SVG icon set (flag-icons) at call sites if pixel-perfect flags are required.
- **`apt-get update` always runs** at step 2 (fresh servers had stale package lists → "Unable to locate package").
- **HAProxy mode reuses step slot 10:** the frontend `DEPLOY_STEPS`/backend `STEP_LABELS` arrays are fixed (**14**); haproxy runs one step at index 10 (backend log label overridden via `_begin_step(task, 10, label=…)`, but the frontend card still derives its tiny step label from `DEPLOY_STEPS[9]` = "Cloudflare DNS + SSL" during the ~1 min install). Cosmetic only — on success the card shows 100%/SUCCESS. `mode == "remnanode"` requires domain/email (+ Cloudflare only for the cloudflare provider) — optional model fields gated in `DeployRequest.validate_by_mode`.

## 8. Wave 2 — ЗАВЕРШЕНА: правила (Ф1+Ф2) · MCP (Ф3) · ИИ-агент (Ф4) · синк (Ф5+Ф6) · миграция (Ф7+Ф8) · профили (Ф9)
> План `docs/superpowers/plans/2026-07-07-remnawave-wave2.md`. Всё per-account, secrets-not-at-rest **кроме** module-scoped Fernet-vault правил и MCP-токена (фон/контейнер без per-request creds — как infra_billing). Остальные фазы (Ф4 ИИ-агент, Ф5/Ф6 синк, Ф7/Ф8 миграция) — TODO.

### 8c. MCP-сервер (Ф3)
- **`mcp/`** — форк `TrackLine/mcp-remnawave` (MIT): TS MCP-сервер, `@remnawave/backend-contract` **2.9.14** (бамп с 2.6.27 + починка разломов: `USERS.GET_BY.{TELEGRAM_ID,EMAIL,TAG,SUBSCRIPTION_UUID}` и `HOSTS.BULK.{SET_INBOUND,SET_PORT}` удалены; `IP_CONTROL`→`CONNECTIONS`), zod **3.x** (не 4 — апстрим под raw-shape). **`src/tools/node-assistant.ts`** — read-only инструменты в наш backend (JWT Bearer, `NODE_ASSISTANT_BASE_URL`/`_TOKEN`): rules/checker-status/incidents/node-load/top-users/subscriptions/domains/host-templates/infra-summary. **`src/index.ts`** — Streamable HTTP транспорт (session-based, `Bearer MCP_AUTH_TOKEN`-гейт, `/health` ungated) когда заданы `MCP_HTTP_PORT`+`MCP_AUTH_TOKEN`, иначе stdio. `readonly` (env `REMNAWAVE_READONLY`) регистрирует только read-инструменты. Verify: `smoke.mjs` (156 инструментов, initialize+tools/list по HTTP, 403 без токена).
- **`services/mcp_server.py`** — DooD-оркестрация контейнера `node-installer-mcp` (по образцу `xray_checker.py`): `start(account_id)` = `docker run` на `node-assistant-net` с env из настроек АКТИВНОГО аккаунта (Remnawave-креды + свежий `issue_token` JWT + расшифрованный MCP-токен); `stop`/`container_state`/`reachable`/`status`. **MCP_AUTH_TOKEN — Fernet-зашифрован** (`McpConfig.auth_token_enc`, ключ = SHA-256 `encryption_key`); `ensure_auth_token` генерит при первом включении. ⚠️ Контейнер ОДИН общий — несёт креды последнего включившего аккаунта (документировано).
- **`api/mcp.py`** (под `require_account`): `GET/POST /api/mcp/config` (enable/readonly/http_port; токен plaintext возвращается ТОЛЬКО владельцу для копирования; Docker-absent → `{ok, warning}` не 500), `GET /api/mcp/status`. `McpConfig` на `AppSettings`. Frontend `settings/McpTab.tsx` — вкладка Settings→«MCP» (статус контейнера, тумблеры enabled/readonly, порт, endpoint+токен с копированием, инструкция). Compose: сервис `mcp` (profile `mcp-build` — образ собирается, но compose его не стартует; backend оркестрирует). Тесты: `test_mcp.py` (config CRUD/токен-стабильность/шифрование at-rest/status-no-docker/порт-валидация).

### 8a. Rules engine (Ф1)
- **`services/rule_engine.py`** — ЧИСТЫЙ `evaluate(rule, event, now, state)` → `{should_fire, matched_actions, reason, dry_run}`, никогда не бросает. Гейты по порядку: `enabled` (дефолт **false** — действия деструктивны) → trigger (`xray_down` гистерезис N-мин / `webhook` event+scope / `cron`) → **cooldown** → conditions (and/or цепочка, операторы `eq/ne/gt/gte/lt/lte/contains/in/exists`). **Cooldown — per-scope:** `cooldown_scope(event)` = stableId/node для `xray_down` (per-node), `""` для webhook/cron. `_last_fired_for` читает per-scope карту `rule.last_fired`, а при её отсутствии — legacy-скаляр `last_fired_at` (глобально, обратная совместимость).
- **`services/rules_store.py`** — per-account `rules.json` + Fernet-vault `rules_secrets.db` (ключ = SHA-256 `settings.encryption_key`). Telegram `bot_token` → `token_ref` (opaque), plaintext НИКОГДА в json. `put/read/delete_secret` + async-обёртки. `update_rule` **GC-ит** осиротевшие `token_ref` при замене списка actions (diff old/new `_token_refs`); `remove_rule` GC-ит все. **`mark_fired(rule_id, scope, now)`** — read-modify-write per-scope карты (последовательные fire в одном тике node A→B аккумулируются, не затирают друг друга).
- **`services/rule_actions.py`** — `execute_actions(actions, ctx, account_id, dry_run)`, fail-soft (не бросает, лог через `telegram.redact`), idempotent. Actions: `telegram` (vault-token, `$placeholder` подстановка longest-key-first), `hide_hosts`/`show_hosts` (bulk, **селектор ОБЯЗАТЕЛЕН** — `host_uuids`/`node_uuid`/`config_profile_uuid`; без него отказ, НЕ трогаем все хосты; idempotent — только не-в-целевом-состоянии), `node_disable/enable`+`user_disable/enable` (**uuid валидируется `_UUID_RE`** до интерполяции в URL-путь). dry_run возвращает masked-план без секретов.
- **`services/telegram.py`** — `send_message` (httpx, best-effort) + **`redact(text, extra?)`** (маскирует bot-token regex `\d+:[A-Za-z0-9_-]{30,}` + явный секрет).
- **`api/rules.py`** — 2 роутера: `router` (`/api/rules`, gated CRUD) и **`webhook_router`** (`/api/webhooks/remnawave`, **UNGATED** — capability = валидная HMAC-подпись). `_verify_hmac` (SHA-256, `hmac.compare_digest`, `sha256=`-префикс). **Anti-replay:** `_replay_fresh(payload.timestamp, now)` — окно ±300с по ПОДПИСАННОМУ телу (заголовок `X-Remnawave-Timestamp` НЕ подписан → не используется); отсутствует/непарсится → пропуск (fail-open, HMAC уже аутентифицирует). **Dry-run — ДВА эндпоинта** (общий `_dry_run`): `POST /api/rules/test` (тело-драфт, **НИЧЕГО не персистит и не вакуумит токен** — фронт превьюит НЕ создавая orphan) и `POST /api/rules/{id}/test` (по id сохранённого правила). Оба — masked-план без секретов. `rules_loop` (lifespan, per-account explicit account_id, mirror `poller_loop`): `_xray_down_events` возвращает **событие НА КАЖДУЮ** down-ноду (не только worst) → per-node fire + per-node cooldown. Webhook гоняет `webhook`-правила по ВСЕМ аккаунтам. Все exc-логи через `telegram.redact`.
- **`remnawave_client.py`** (доп.): `enable_node`/`disable_node`/`enable_user`/`disable_user` (`POST /api/{nodes,users}/{uuid}/actions/{enable,disable}`), `list_hosts`, `bulk_disable_hosts`/`bulk_enable_hosts` (`POST /api/hosts/bulk/{disable,enable}` body `{uuids}`). `config.webhook_secret_header` (env, пусто=webhook выключен). Тесты: `test_rule_engine.py` (scope/операторы), `test_rule_actions.py` (селектор-обязателен/uuid-валидация), `test_rules_api.py` (GC-on-update/anti-replay/loop per-node/draft-test-no-persist).

### 8a-fe. Rules UI (Ф2)
- **`components/automation/`** — раздел «Автоматизация» (Sidebar-группа, tab `automation` → `RuleBuilder`) + «Уведомления» (tab `notifications` над «Настройки» → `Notifications`). `rulesApi.ts` — общий типизированный клиент `/api/rules` (+ метки триггеров/действий/операторов, `TOKEN_MASK`, `TEXT_PLACEHOLDERS`); `listRules` **бросает** при не-OK (не маскирует ошибку под пустой список); `jsonOrThrow`/`formatError` форматируют FastAPI-422 **без эха `input`** (иначе plaintext bot-token утёк бы в сообщение об ошибке).
- **`RuleBuilder.tsx`** — список правил (тоггл enabled, dry-run-бейдж) + модалка-редактор: триггер (xray_down N-мин/фильтр-нода · webhook event+scope · cron мин) → условия (and/or, операторы, `in`→список) → действия (telegram токен+chat_id+text с плейсхолдерами / hide-show_hosts с обязательным селектором / node-user enable-disable) + cooldown/enabled/dry_run. **«Проверить» → `POST /api/rules/test` (stateless — НЕ создаёт правило)**. Токен существующего правила показывается как `••••` (`type=password`); служебные `_`-ключи и пустой `bot_token` при наличии `token_ref` вычищаются из payload (не затирают vault-токен). Валидация зеркалит бэкенд-гейты (minutes>0, ≥1 действие, селектор хостов, непустое поле условия). Ошибки toggle/delete → `toast`.
- **`Notifications.tsx`** — упрощённый раздел: быстрое telegram-уведомление (обёртка над `/api/rules` с одним telegram-действием) + список нотификаций (фильтр по наличию telegram-действия), редактирование через общий `RuleModal`. Тесты: `RuleBuilder.test.tsx` (пустой/список/валидация/маскировка/stateless-dry-run-без-orphan/ошибка-загрузки/Notifications-фильтр+валидация).

### 8b. Xray-профили — редактор (Ф9)
- **`components/profiles/`** — визуальный редактор Xray-конфига (форк bropines/xray-config-ui-editor MIT). `core/`: `types`/`schema` (ajv JSON-Schema, `additionalProperties:true` — ловит СТРУКТУРНЫЕ ошибки; enum PROTOCOLS/NETWORKS/SECURITIES **закрытые**)/`validators` (ajv + cross-ref; `ValidationError.keyword` несёт ajv-keyword → UI down-ранкает `enum`-нарушения в **warning**, не блокирует синк, т.к. наши enum-списки могут отставать от Xray)/`diagnostics`/`crypto` (**CSPRNG `crypto.getRandomValues`** для UUID/shortId/spiderX — REALITY-материал, НЕ Math.random; X25519 через tweetnacl)/`factories`/`warp`/`links` (share-link ↔ outbound: vless/**vmess**(base64-JSON, парсится ДО `new URL`)/trojan/ss + WireGuard/AWG import). `store/configStore.ts` (Zustand+Immer, per-account `xray_profile_<id>` в localStorage — **черновик персистится на каждом commit**; `dirty` = «не синхронизировано с панелью», НЕ «не сохранено»). `DiagnosticsPanel` (critical=blocker, enum→warning), `Profiles.tsx` (импорт с **лимитом 5 МБ**). Синк браузер→панель — НЕ прямой вызов (TODO). Тесты: `crypto.test.ts`, `configStore.test.ts`, `links.test.ts` (vmess round-trip), `validators.test.ts` (enum-keyword/balancer). Роут `rw-profiles` в `App.tsx`.

### 8d. Встроенный ИИ-агент (Ф4)
- **`services/ai_agent.py`** — provider-agnostic agent-loop с tool-calling. Провайдеры OpenAI-совместимый (`/chat/completions`) + Anthropic (`/v1/messages`) за общим `_provider_turn` → `{text, tool_calls, raw}`. **SSRF-гард `net_guard.is_safe_url(base_url)` на КАЖДОМ тёрне** (base_url пользовательский). Anthropic system — **top-level `system`** (не в messages). Read-only tools in-process: `list_rules`/`list_subscriptions`/`node_health`/`list_nodes` (account_id из сессии, не из промпта). Last-step **tools-off** (агент синтезирует финал, не упирается в лимит). API-ключ в Fernet-vault (`AiConfig.api_key_enc`), NEVER в логах/ответах (только `has_key`); ошибки через `redact(..., key)`. Cap tool-результата 4000 в истории. Парсинг ответа обёрнут в try→AgentError (контракт «не бросает»).
- **`api/ai.py`** (под `require_account`): `GET/POST /api/ai/config` (ключ write-only, blank=keep, unknown-provider→422), `POST /api/ai/chat` (стрим ndjson-событий tool_call/tool_result/text/done/error). Frontend **распилен Волной 6 (План C Ф1)**: `automation/AiChat.tsx` — ТОЛЬКО чат (таб «Ассистент»), корень `flex-1 min-h-0`, лог со своим `overflow-y-auto` вместо `max-h-80`, композер приколот снизу; страница ничего не сохраняет (GET конфига только ради гейта композера). Конфигурация — `settings/AiSettingsTab.tsx` во вкладке **«Настройки → Ассистент»** (рядом с MCP), туда же переехал `PromptPresets`, ставший **контролируемым** (`activeId`/`onPickActive`) — раньше он сам делал GET-modify-POST по `/api/ai/config` и гонялся с формой за один документ. Стрим через `res.body.getReader()`; patchLast **иммутабельный** (StrictMode), AbortController на unmount, tool_result матч по id. Тесты: `test_ai.py`, `automation/AiChat.test.tsx`, `settings/AiSettingsTab.test.tsx`.
- **Промпт-пресеты (Wave-5 План I):** системный промпт агента больше НЕ захардкожен — `build_system(account_id, cfg)` = текст активного пресета + неотключаемый `_TOOLING_SUFFIX` (чтобы «чужой» пресет не потерял наши read-only инструменты). `system` пробрасывается в оба пути (`_openai_turn` system-message + `_anthropic_turn` top-level). Встроенные пресеты — read-only ассеты `backend/app/assets/prompts/` (`PRESETS.json` + `default`/`precise`/`cloudflare-agent-setup`); Cloudflare — **вендоренный плейсхолдер `unavailable`** (CC-BY-4.0 атрибуция, текст не выдуман — операторский вендоринг-TODO). Пользовательские пресеты per-account `prompt_presets.json` (не секрет → обычный JSON). `services/prompt_presets_store.py` + `api/ai_prompts.py` (`GET/POST/PUT/DELETE /api/ai/prompts`, `POST /{id}/fork`, builtin→400); активный выбирается `AiConfig.active_preset_id` через `/api/ai/config`. Frontend `settings/PromptPresets.tsx` (в «Ассистенте»). Тесты `test_ai_prompts.py`.
- **CLIProxyAPI-шлюз (Wave-5 План J):** `AiConfig.gateway` (`none`|`cliproxy`) + `gateway_internal` — режим, где агент ходит к любому провайдеру через единый шлюз **CLIProxyAPI** (`router-for-me/CLIProxyAPI`, порт 8317). ⚠️ **«MCP через opencliproxy» ≠ MCP-транспорт:** CLIProxyAPI — LLM-**шлюз** (OpenAI+Anthropic форматы), в ядре MCP не поддерживает → перенаправляем LLM-хоста (наш `ai_agent`), `mcp_server.py` НЕ трогаем. SSRF-гард вынесен в `ai_agent._check_base_url` — **exempt** только для внутреннего контейнера-шлюза (`node-installer-cliproxy`/`cli-proxy` при `gateway_internal`, как `xray_checker` для локального чекера), внешний шлюз проверяется как раньше. `ai_agent.list_models` (`GET {base_url}/models`, graceful []) + `GET /api/ai/models` — **Волна 6, План C Ф2: гейт `gateway != cliproxy → []` СНЯТ** (каталог отдаётся любому провайдеру: и OpenAI-совместимые, и Anthropic возвращают одинаковый `{"data":[{"id"}]}`), заголовки по провайдеру (`x-api-key`+`anthropic-version` для anthropic, иначе Bearer), и **ранний выход при пустом ключе — сети без ключа нет вовсе**; `AiConfigBody.gateway` валидируется. Frontend `AiChat.tsx`: селектор «Шлюз» + селектор моделей (fallback ручной ввод). Тесты `test_ai_gateway.py`. **Отложено (опц. Ф2):** self-host CLIProxyAPI DooD-контейнером (`cliproxy_server.py`/`api/cliproxy.py` + compose `cli-proxy`) — дефолт указывает на внешний CLIProxyAPI.

### 8e. Синхронизация панелей (Ф5 backend + Ф6 frontend)
- **`services/sync_store.py`** — per-account `panel_groups.json`: `Group{members[]{panel_key(=panel_jobs id), priority(выше=важнее), role:primary|standby}}`; `nearest_higher_primary` = primary с мин. приоритетом строго > standby. Атомарная запись (temp+os.replace); уникальность priority И panel_key.
- **`services/panel_sync.py`** — `plan_sync` (guard: restore ТОЛЬКО на standby, не primary) + `run_sync`: backup primary → **SFTP-релей бандла primary→backend→standby** (`SSHSession.download/upload_file`) → restore КОНКРЕТНОГО бандла (`backup_service.restore_script(confirm, bundle_path)` + `newest_bundle_cmd`; distillium-wrapper иначе восстановил бы ЛОКАЛЬНЫЙ бэкап standby). In-flight lock по standby-ip; sanity размера бандла (<128б→отказ); backup-fail останавливает до relay. **Нет server-side sync_loop** — SSH-креды в panel_jobs (localStorage), не хранятся; синк client-initiated.
- **`api/panel_sync.py`** (`/api/sync/groups*`): CRUD + `POST /{id}/run` (creds обеих панелей per-request, **confirm обязателен**, стрим-Task). Frontend `rw/SyncGroupPanel.tsx` в `PanelDashboard`: группы, роли/приоритеты, ручной синк (fresh re-load групп перед вычислением nearest-primary, guard двойного клика, `nearestHigher` зеркалит бэкенд). Тесты: `test_panel_sync.py` (relay-order/backup-fail-stops/inflight), `SyncGroupPanel.test.tsx` (6).

### 8f. Миграция marzban→remnawave (Ф7 backend + Ф8 frontend)
- **`services/marzban_migrate.py`** — Marzban admin API (`marzban_login`/`marzban_counts`/`marzban_core_config`, **SSRF-гард на КАЖДОМ фетче**) + `migrate_docker_args` (pure) + `parse_migrate_output` (pure, толерантный) + `run_migrate` (docker `remnawave/migrate`, стрим, `_redact` секретов). **Образ ПИННИТСЯ server-side** (`_MIGRATE_IMAGE` в `api/migrate.py`, не поле body — иначе произвольный docker-образ через DooD).
- **`services/marzban_reality.py`** — `build_reality_patch` (PURE, deepcopy): переносит `realitySettings` по tag в существующий config-profile inbound (**security=reality форсится**; не добавляет/не удаляет inbounds; unmatched отчёт). `legacy_secret_cmd` — `SELECT secret_key FROM jwt` (silent через `get_output`).
- **`api/migrate.py`** (`/api/migrate/*`): `preview` (счётчики+отчёт потерь, без записи), `reality` (PATCH профиля, `net_guard` на remnawave_url), `run` (confirm+стрим), `legacy-secret` (SSH). `remnawave_client`: `list/get/update_config_profile`. Frontend `rw/Migration.tsx` (замена заглушки rw-migration): 5-секционный визард, пароли/токены/секрет type=password, confirm перед migrate, любая операция блокирует все кнопки. Тесты: `test_migrate.py` (18), `Migration.test.tsx` (5).


## 9. Wave 3 + Wave 4 — shipped deltas (планы `docs/superpowers/plans/2026-07-19-wave{3,4}-*`)
> Кратко: изменения модели/пайплайна/дешборда, доехавшие в код. Планы — источник детализации.

### 9a. Deploy: варианты ноды + тумблеры (Wave3 План B)
- `DeployRequest` (models/deploy.py): `node_variant: "egames"|"vanilla"="egames"`, `install_hysteria2:bool=True`, `docker_mirror:bool=False`, `cookie_gate:bool=False`. E3 «torrent blocker» был добавлен и **откачен** (revert a7e60bd) — поля `install_torrent_blocker` НЕТ.
- **Vanilla** (`pipeline.step_remnanode_vanilla` + `_render_vanilla_compose`/`_VANILLA_COMPOSE_TPL`): официальный `remnawave/node`, `network_mode: host`, только NODE_PORT/SECRET_KEY, БЕЗ nginx. В `run_pipeline`: `is_vanilla` → шаг 10 (SSL) и 12 (маскировка) ПРОПУСКАЮТСЯ, шаг 11 = vanilla-инсталл. `validate_by_mode`: vanilla → domain/email/cf опциональны, НО hysteria2 требует domain.
- Шаг 14 (Hysteria2) гейтится: `if "hysteria2" in skip or not req.install_hysteria2`. `docker_mirror` → `_docker_mirror_script()` пишет `/etc/docker/daemon.json` registry-mirrors в шаге 2.
- Frontend `DeployForm.tsx`: суб-табы eGames/Vanilla, тумблеры Hysteria2/Cookie-gate/Docker-mirror. **Селекторы сквадов УБРАНЫ** (5a) — авто-привязка на бэкенде (`step_create_node`: если `int_squads` пуст → берёт все внутренние сквады).

### 9b. Dashboard: 2 вкладки Xray/Server uptime (Wave3 План A)
- `Dashboard.tsx` — переключатель Xray uptime (2-уровневая группировка подписка→страна) / **Server uptime** (ручные серверы + подтяжка из `deploy_jobs`).
- **`server_monitor` (backend, NEW):** `services/server_monitor_store.py` (per-account SQLite `server_monitor.db`, servers+samples, sync_deployed, аналитика как `metrics_store`) + `api/server_monitor.py` (`/api/server-monitor` CRUD + `/statuspage` + `/incidents`; `_probe` = TCP порт→22 fallback + ICMP; `monitor_loop` 60с). Роутер под `_auth` в main.py; `monitor_loop` в lifespan.
- **Скрытие серверов (Волна 6, План B Ф4):** колонка **`servers.hidden`** (+ идемпотентный `ALTER TABLE` для БД,
  созданных раньше — приём из `metrics_store`/checker_id). `PATCH /servers/{id}` с `hidden` идёт через отдельную
  `store.set_hidden` и работает для **ЛЮБОГО `source`** — в отличие от прочих полей, ограниченных
  `source='manual'`. Это закрывает реальный тупик: deployed-строку нельзя ни отредактировать, ни удалить
  (`_sync_deployed` вернёт её из `deploy_jobs`), то есть убрать с глаз было нечем. Флаг **переживает ре-синк** —
  апсерт трогает только `name/country/port`. `statuspage` считает `total/online/state/uptime30d` **только по
  нескрытым**, но отдаёт скрытые с флагом (UI показывает их свёрнутым блоком «Скрытые (N)»); побочно это же даёт
  подавление вкладке «Статистика» с `cid='server-monitor'`. **Пробы продолжаются** (`monitor_loop` не тронут), так
  что после возврата история и 30-дневный аптайм на месте — в отличие от удаления, которое стирает
  `server_samples`.
- `XrayCheckerConfig.enabled` дефолт **True** (мониторинг вкл. по умолчанию); `xray_checker.autostart_checker()` стартует общий контейнер на буте; `subscriptions._schedule_checker_reload()` (debounce 8с) перезапускает чекер при CRUD подписок.
- Users-статы (`stats/UsersStats.tsx`): большой мульти-лайн график загрузки нод (6a), селектор чекера включает «Server uptime».

### 9j. Скрытие отдельных узлов на «Xray uptime» (отложенный пункт, сделан)
- **`POST /api/stats/users/hidden/checker`** (`api/user_stats.py`) — тогл ОДНОГО `(checker_id, stableId)` в
  наборе `hidden.checker` с СОХРАНЕНИЕМ layout (read-modify-write, не full-replace). Чистая альтернатива
  `PUT /widgets` для дэшборда, который не владеет раскладкой виджетов.
- **`/api/checker/statuspage` и `/incidents`** (`api/xray_checker.py`) читают `_hidden_checker_nodes(cid)` и:
  узлы отдаются с флагом `hidden`, но счётчики/баннер/`global.uptime30d` считаются ТОЛЬКО по нескрытым
  (как «Server uptime» в §9b); инциденты скрытых отбрасываются. Пробы НЕ трогаются — история/аптайм живут.
- ⚠️ **Один и тот же набор `hidden.checker[cid][stableId]`**, что у пикера «Серверы» статистики: обе оси
  ключуются на `(checker_id, stableId)`, поэтому скрытие на дэшборде и в статистике — ОДНО множество
  («не отслеживаю этот xray-узел»). Server-monitor скрывается ОТДЕЛЬНО (`servers.hidden`, §9b) — другое
  пространство идентичности (row-id SQLite).
- Frontend `Dashboard.tsx` (`XrayUptime`): кнопка EyeOff в строке узла (`trailing`) → скрыть; секция
  «Не отслеживаются (N)» со скрытыми и кнопкой Eye → вернуть. `toggleHidden` шлёт тогл + перечитывает.

### 9c. Прочее Wave 3
- **Автодетект существующего сервера (План B, 502b837 backend + фронт Ф2):** `node_ops._DETECT_SETTINGS_SCRIPT` echo `NIVAL:key=value` (ssh_port/open_ports/domain/remnanode_port/xhttp_path/**has_token** — сам токен НЕ читается) + `_parse_settings`; `/api/node/detect` → `{results, settings}`. `DeployRequest.skip_components` пропускает уже установленные компоненты. **Фронт Ф2:** `DeployDashboard` «Существующий сервер» читает `settings` → блок «Обнаружено на сервере» + маппит в `preset` формы (preset > settings-дефолты; тумблеры warp/optimize/trafficguard/hysteria2 зеркалят detect-компоненты). Vanilla-нода: nginx.conf/серта/UFW нет → detect отдаёт только ssh_port+has_token (проверено вживую).
- **Templates CodeMirror (План C 4a):** `Templates.tsx` `<textarea>`→`<JsonEditor>` (переиспользован из profiles); `$xhttp_path` добавлен в подстановку config-profile И в `_subst_host_vars` (step_create_hosts).
- **SSL терминал сворачиваемый (План E 3a):** `App.tsx` certs-таб — `termOpen` (дефолт свёрнут → показаны Домены), деплой серта авто-раскрывает.
- **Профили/ИИ-чат вынесены (План C 10a/E 11a):** `rw-profiles` — **в группе «Remnawave»** (перенесён из `NAV_MAIN` Волной 6, План A); `assistant` таб (AiChat) в группе Автоматизация. **Отменено:** синхронизация профили↔шаблоны (10b, 9bf2f20).
- `check_remnawave` принимает опц. тело `{panel_url, api_token}` (тест значений формы до сохранения) — 11b.

### 9d. Хостинги (Wave 4 План A) — полный стек
- Backend (594dfbb): `models/hostings.py` (`HostingBody`/`Tariff`/`Location` lat∈[-90,90]/lng∈[-180,180]), `services/hostings_store.py` (per-account `hostings.json`, атомарно+lock, MAX 500), `api/hostings.py` (`/api/hostings` CRUD под `_auth`). `test_hostings.py`. **geo-resolve эндпоинта НЕТ** (план предполагал — не реализован; резолв клиентский).
- Frontend (926c94f): `components/hostings/{api,geo,HostingsCatalog,HostingsMap}.tsx`. Сайдбар-группа «Хостинги» (`hostings-map`/`hostings-list`).
- **Карта — квирки:** `react-simple-maps` + `world-atlas` (топоjson `countries-110m.json` бандлится через `import`, нужен `src/vendor.d.ts` ambient-декларация `any`); `<Geographies geography>` получает **массив features** (`feature(topo, objects.countries).features`), НЕ FeatureCollection-объект (иначе не мапится); анимации `motion` (`motion/react`). Тоглы континентов фильтруют маркеры + «приблизить» (dimming стран нет). Новые npm-deps ставятся `npm install` внутри Docker-билда фронта — хосту npm не нужен.

### 9e. Prometheus-метрики панели (Wave 4 План C / E5)
- **R1 (метрики Remnawave :3001):** порт **3001** (env `METRICS_PORT`), endpoint `/metrics`, bind **127.0.0.1** (наружу НЕ проброшен), basic-auth `METRICS_USER`/`METRICS_PASS` (в `/opt/remnawave/.env`; `METRICS_PASS` — protected secret key, маскируется `_is_secret_env_key`). Известные метрики (Remnawave ≥2.x, по докам — снять с живой панели для точных лейблов): `remnawave_users_online_stats` (онлайн distinct-юзеры), `remnawave_users_status{status=ACTIVE|DISABLED|LIMITED|EXPIRED}` (юзеры по статусам), `remnawave_node_status{node_uuid,node_name}` (1=connected/0). Есть ~30 gauges (per-node CPU/RAM/трафик) — surface'им несколько + счётчик метрик.
- **R2 (доступ):** скрейпим **по SSH на боксе панели** (`curl -fsS -u user:pass 127.0.0.1:$PORT/metrics`), креды читаются из .env на боксе и используются на боксе — НЕ возвращаются/не логируются (silent `get_script_output`).
- **`services/panel_metrics.py`** — `parse_prometheus` (чистый, `name{labels} value`, ±Inf/NaN отброс), `summarize` (curated: online/by-status/nodes, graceful при переименовании метрик), `metrics_scrape_script`. **`api/panel_metrics.py`** `POST /api/panel/metrics` (`EnvReadRequest` creds per-request; .env нет→404, скрейп упал→502). Frontend: `PanelManageModal` «Статистика» → `PanelMetricsBlock` (плитки, чипы статусов, свой refresh; только для target с панелью). Тесты `test_panel_metrics.py`. ⚠️ Полная проверка плиток — при живой панели (у меня нет).

### 9f. Смена домена (Wave 4 План E / E7)
- **`services/replace_domain.py`** (чистые генераторы) + **`api/replace_domain.py`** (`POST /api/replace-domain/{node,panel}`, стрим-Task, creds per-request). `node_replace_script` — scoped `sed old→new` в `/opt/remnanode/{docker-compose.yml,nginx.conf}` (dots экранируются, nginx-вары целы) + cert-мост + рестарт; OLD автодетект из `server_name`; идемпотентно. `panel_replace_script` — `.env`/compose/Caddyfile/nginx. Переиспользует `pipeline.build_ssl_script`+`upsert_a_record` (caddy→серт авто). Frontend `rw/ReplaceDomainModal.tsx` (node|panel, double-confirm, стрим) + кнопки в `DeployCard`/`PanelManageModal`. Тесты `test_replace_domain.py`.

### 9g. Certwarden — централизованный ACME (Wave 4 План D / E6)
- **R1/R2/R3:** образы ghcr `certwarden`/`certwarden-client`; сервер порты 4050(UI/API)/4055(HTTPS)/4060(HTTP-01), том `./data`. Download-API: `GET /certwarden/api/v1/download/{certificates,privatekeys}/<Name>` заголовок `X-API-Key` (у серта и ключа РАЗНЫЕ ключи). Клиент — **наш** cron-скрипт (curl двух эндпоинтов в `/etc/ssl/...` + `docker restart`; docker.sock клиенту НЕ даём). Certwarden — АЛЬТЕРНАТИВА per-node acme (не гонять параллельно).
- **`services/certwarden.py`** (`server_deploy_script`/`client_install_script`/реестр `certwarden.json`) + **`api/certwarden.py`** (`/server/{deploy,GET,DELETE}`, `/client/install`; API-ключи через SILENT-канал + charset-guard). Frontend Settings→«Инфраструктура» `settings/InfraTab.tsx`. Тесты `test_certwarden.py`. ⚠️ Тумблер деплоя `use_certwarden` НЕ вшит в 14-шаговый пайплайн — клиент ставится отдельной операцией.

### 9h. Netbird — self-hosted mesh (Wave 4 План F / E8)
- **R1/R2/R3:** стек `netbirdio/netbird-server`(+встроенный Dex, IdP не нужен)+`dashboard`+`reverse-proxy`+`traefik` через `getting-started.sh` (env `NETBIRD_DOMAIN`/`NETBIRD_LETSENCRYPT_EMAIL`, порты TCP 80/443 UDP 3478, нужен публичный FQDN+A). Setup-key: `POST /api/setup-keys` заголовок `Authorization: Token <PAT>` → поле `key`. PAT = service-user token из Dashboard → **Fernet-волт**. Агент: `netbird up --setup-key … --management-url … --disable-client-routes --disable-server-routes` (ОБЯЗАТЕЛЬНО — иначе перехват дефолт-роута и потеря SSH, урок WARP §6). Overlay-IP `netbird status --json|jq .netbirdIp` (100.64.0.0/10).
- **`services/netbird.py`** (Fernet-волт PAT `netbird.json`, генераторы, `setup_key_payload`) + **`api/netbird.py`** (`/control-plane/{deploy,GET,DELETE}`, `PUT /pat`, `POST /setup-key` (management API+net_guard), `/agent/join`; setup-key через SILENT-канал). Frontend `settings/InfraTab.tsx`. Тесты `test_netbird.py`. ⚠️ Тумблер `join_netbird` НЕ вшит в пайплайн — нода подключается отдельной операцией `/agent/join`.

### 9i. Mihomo-конфигуратор (Wave 5 План E — первая доехавшая часть)
- **Встроен КАК ЕСТЬ через iframe, НЕ порт в React.** Форк `123jjck/mihomo-configurator` (vanilla-JS: `index.html` + `app/{state,parsers,ui,generate}.js` + `style.css`, глобальные скрипты с inline-`onclick`, 4-шаговый визард DNS→Серверы→Правила→Скачать; парсит share-ссылки vless/vmess/ss/trojan/hysteria2/tuic/vpn + WireGuard/AmneziaWG `.conf` + подписки, генерит mihomo YAML через `jsyaml`). Ре-имплементацию в React (как планировал План E) заменили на **встраивание** — 3.5k строк DOM-кода, полностью самодостаточно, ноль сетевых вызовов (всё client-side, backend не трогается). ⚠️ **Лицензии у апстрима НЕТ** (all rights reserved) — учитывать при публичном распространении; ссылку на автора в шапке сохранили (атрибуция).
- **Размещение:** `frontend/public/mihomo/` (`index.html`+`app/`) → Vite копирует в `dist/mihomo/`, nginx отдаёт по `/mihomo/`. `frontend/src/components/MihomoEditor.tsx` = same-origin `<iframe src="/mihomo/index.html">` (без sandbox → работают localStorage `ui-lang`, clipboard, download, file-import). Nav: `Tab "mihomo"` (`Sidebar.tsx`, пункт «Mihomo» с `Waypoints` в **группе «Remnawave»** после «Профили» — Волна 6, План A), `App.tsx` CRUMB `["Remnawave","Mihomo"]` + маршрут.
- **CSP-self-contained:** апстрим грузил `js-yaml` с CDN — заменено на локальный `vendor/js-yaml.min.js`, копируется из `node_modules` **на этапе билда** скриптом `frontend/scripts/vendor-mihomo.mjs` (добавлен dep `js-yaml`, шаг вписан в `build`/`dev` в package.json; сам блоб в `.gitignore` — `public/mihomo/vendor/`). **Форвард (План D):** привязка к mihomo-шаблонам (загрузка/сохранение конфига в наш template-стор) — через postMessage поверх iframe, пока standalone-инструмент.

## 10. Wave 5 План M — опциональный распил на сервисы (strangler, ОТГРУЖЕНО)
> План `docs/superpowers/plans/2026-07-21-wave5-m-microservices.md`. Ф1–Ф3 + Ф5 реализованы; Ф4 (library/billing/ai
> как сервисы) осознанно НЕ делали — план сам помечает его «по потребности».

### 10a. Ключевое решение: общий том, БЕЗ service-to-service HTTP
- Разведка показала, что **все** сторы — SQLite/JSON на общем томе `node-data`. Поэтому HTTP-прокси между gateway и
  вынесенными сервисами (как предполагал Ф2) и контракт service-аутентификации (Ф1c) **не реализованы намеренно** —
  это был бы спекулятивный код (CLAUDE.md §2). Вынесенные процессы **не поднимают HTTP вообще**: они только
  выполняют фоновую работу, а все чтения gateway делает сам с того же тома. Меньше кода, нет проброса auth, нет
  новой поверхности атаки. Решение подтверждено пользователем.
- Следствие: **nginx/фронтенд не трогали** — SPA по-прежнему ходит только в `backend`. Ф1c заменён на `worker_lease`.

### 10b. `worker_lease` — почему распил опционален и обратим
- **`services/worker_lease.py`** — таблица `leases` в `DATA_DIR/tasks.db`. Каждая фоновая обязанность (`monitoring`,
  `deploy-worker`) обёрнута в аренду: кто держит — тот работает, остальные простаивают. Монолит → gateway держит всё,
  поведение прежнее. `--profile split` → выделенные контейнеры перехватывают. Контейнер умер → аренда протухает
  (TTL 180с) и **gateway сам возобновляет обязанность** — это и есть критерий отката Ф5.
- **Захват через `SERVICE_ROLE`:** процесс, запущенный ПОД обязанность (`SERVICE_ROLE=monitoring`), берёт аренду
  **безусловно** (перехватывает у gateway, который загрузился раньше). Все остальные берут только свободную/протухшую.
  Так распил сходится без таймингов и порядка загрузки. Ровно один контейнер на обязанность (несколько — не поддержано).
- **Fail-open:** любая ошибка БД аренд → `acquire` возвращает True. Сломанная таблица не должна затыкать мониторинг.
- 5 лупов (`poller_loop`/`collector_loop`/`rules_loop`/`monitor_loop` + one-shot `autostart_checker`) гейтятся на
  `MONITORING`. `autostart_checker` — через `held_elsewhere`, чтобы два процесса не гонялись создавать один контейнер.

### 10c. Task store: два бэкенда за одним синглтоном
- **`services/task_types.py`** (NEW, лист-модуль) — `TaskStatus` + `STEP_LABELS`. **⚠️ Вынесены сюда из `task_store`
  ради разрыва цикла:** `task_store` на импорте выбирает реализацию и потому импортирует `shared_task_store`, а тот
  раньше импортировал имена обратно из `task_store` → `ImportError` при импорте `worker_lease` ПЕРВЫМ (порядок воркера;
  порядок gateway случайно работал). `task_store` их ре-экспортирует — ~250 старых импортов не тронуты.
  Регрессия ловится **в подпроцессе** (`test_shared_task_store.py`), иначе уже импортированный модуль всё скрывает.
- **`services/shared_task_store.py`** (NEW) — duck-type близнец `Task`/`TaskStore` на SQLite `DATA_DIR/tasks.db`
  (WAL + `busy_timeout`, одно соединение под `threading.Lock`). Мутаторы остались **синхронными** (их зовёт
  `SSHSession._drain` и ~250 мест пайплайна). `subscribe()` отдаёт ту же `asyncio.Queue` с теми же 3 формами кортежей;
  для shared она преднагружается снимком и дальше **тейлится** фоновой задачей (poll 0.4с) — `api/ws.py` не менялся.
  Ретенция 24ч (у in-memory `cleanup()` было 0 вызовов — задачи текли вечно).
- Выбор: env **`TASK_STORE=memory|shared`**, дефолт `memory` → монолит байт-в-байт как был.

### 10d. Очередь задач и `deploy-worker`
- **`services/job_runner.py`** — реестр `kind → handler`. Gateway кладёт задание в ту же таблицу `tasks`
  (`payload_enc` — **Fernet**, ключ = SHA-256 `encryption_key`; SSH-креды не лежат в открытую и **затираются в
  `finish()`**). Воркер клеймит атомарно (`UPDATE ... WHERE claimed_at IS NULL`, rowcount=1) и стримит логи туда же.
- **Фолбэк — дефолт:** `offload()` отдаёт работу ТОЛЬКО если (а) `TASK_STORE=shared` И (б) живая аренда
  `deploy-worker` у другого процесса. Иначе `False` → вызывающий выполняет всё у себя, как раньше. Ни один
  вынесенный сервис не является жёсткой зависимостью.
- **Отменa между процессами:** `POST /api/deploy/stop` сначала пробует локальный asyncio-хэндл (`_running_tasks`),
  иначе ставит флаг `cancel_requested`; воркер опрашивает его раз в секунду и отменяет свою задачу.
- **Живучесть очереди (по итогам adversarial-ревью — 27 кандидатов, 8 подтверждено).** Ничто не должно
  «зависнуть навсегда», потому что заклеймленную строку может двигать ТОЛЬКО заклеймивший процесс:
  - `run_forever` держит `running`-сет и на выключении **проваливает все in-flight задачи** + **отдаёт аренду**
    (`worker_lease.release`) — иначе gateway ждал бы TTL 180с, а карточка крутилась бы вечно.
  - `app/worker.py` ставит обработчики **SIGTERM/SIGINT** (PID 1 по умолчанию ИГНОРИРУЕТ SIGTERM → docker бил бы
    SIGKILL через 10с без всякого cleanup). `_until_shutdown` принимает **только бесконечные** таски: `autostart_checker`
    — one-shot, и его попадание в wait-сет роняло весь monitoring-воркер сразу после старта (ловится e2e-харнессом).
  - `job_runner.reap_orphans()` проваливает строки, заклеймленные процессом, которого больше нет («нет» = не держит
    аренду и это не мы; здоровая долгая задача не трогается, т.к. её воркер продлевает аренду).
  - `claim_next` уважает `cancel_requested` (отменённая в очереди задача НЕ стартует — `/deploy/stop` уже ответил ok)
    и проваливает задание, чей payload не расшифровался (иначе строка осиротела бы навсегда с кредами на диске).
  - `execute` различает «отменил пользователь» (работаем дальше) и «нас выключают» (добить ребёнка и пробросить
    отмену) — раньше второй случай **вешал цикл навсегда**, а пайплайн продолжал крутиться отцеплённым.
  - `_run_one` гарантирует вердикт даже если `execute` выбросит (его же бухгалтерия читает SQLite).
  - `_tail` **дочитывает** `task_logs` до конца перед `('done',)` — LIMIT 500 + чтение статуса ПОСЛЕ выборки
    логов теряли хвост большого всплеска (ws.py выходит из цикла по `done`).
  - Конкурентность воркера = `settings.max_ssh_sessions` (как в монолите), а gateway при offload отдаёт **тот же
    503** по глубине очереди — иначе распил менял 5 параллельных деплоев на 1 + невидимый безлимитный бэклог.
- **⚠️ ContextVar НЕ переживает очередь — задание несёт `account_id`.** `pipeline.py` читает
  `_storage.load_settings()/load_hosts()/load_templates()/load_traffic_rules()` БЕЗ account_id, т.е. через
  `current_account`, который в gateway ставит `require_account`. В воркере запроса нет → был бы
  `RuntimeError("No active account in context")` на шаге 11 у любого деплоя с `create_in_remnawave`. Поэтому
  `tasks.account_id` пишется при `create()`, а `job_runner.execute` **переустанавливает `current_account` ДО
  `asyncio.create_task`** (задача копирует контекст в момент создания) и сбрасывает в `finally`. Любой новый
  handler получает это бесплатно; тест — `test_account_context_survives_the_queue_hop`.
- Зарегистрированы **`deploy`** (14-шаговый пайплайн — критерий готовности плана) и **`node-op`**. Остальные
  task-виды (certs/panel/backup/testserver/replace-domain/certwarden/netbird/panel-sync/migrate) **осознанно
  оставлены в gateway** — короткие операции; добавление любого = 3 строки `job_runner.register(...)`.
- **14-шаговый пайплайн НЕ менялся** — распил меняет ГДЕ он выполняется, а не ЧТО делает.

### 10e. Запуск
- `python -m app.worker monitoring|deploy` (`app/worker.py`) — HTTP не поднимает. Compose-сервисы `monitoring` и
  `deploy-worker` под `profiles: ["split"]`, тот же образ backend, монтируют `node-data` + docker.sock.
  `docker compose up` = монолит; `docker compose --profile split up -d` = распил. `deploy-worker` жёстко требует
  `TASK_STORE=shared` (иначе `SystemExit` с внятным сообщением).
- **`GET /api/health`** (ungated, он же compose-healthcheck) теперь отдаёт `{ok, role, taskStore{mode,active,queued},
  duties[{name,holder,fresh,self}]}` — единственная точка наблюдаемости распила.
- **Верификация: `cd backend && python tests/e2e/split_smoke.py`** (НЕ pytest — имя не `test_*`, поэтому не
  собирается; ~1.5 мин, поднимает реальные процессы в temp DATA_DIR). 4 фазы: gateway один держит `monitoring` →
  воркеры перехватывают обязанности → **реальный `POST /api/deploy` уходит в очередь, 14-шаговый пайплайн крутится
  в ВОРКЕРЕ, а его логи приходят подписчику `/ws/logs/{task_id}` на GATEWAY** (деплой целится в 192.0.2.1,
  TEST-NET-1 — не маршрутизируется, падает на шаге 1) → воркеров убивают, gateway сам возвращает мониторинг.
  ⚠️ **Гонять после любой правки** `worker_lease`/`shared_task_store`/`job_runner`/`app/worker.py`: юнит-тесты
  живут в ОДНОМ интерпретаторе и оба межпроцессных бага (циклический импорт, ContextVar) поймал именно этот
  харнесс, а не они.

### 10f. Побочно исправлено (найдено разведкой/реальным прогоном)
- **Фоновый поллер НИКОГДА не сэмплил общий xray-checker.** `xray_checker._base_url()` резолвил `_cfg()` ДО проверки
  `_network()`, а `_cfg()` требует account-контекст, которого у лупа нет → `RuntimeError` глушился `except Exception:
  return 0`. Теперь DooD-ветка возвращает URL по имени контейнера, не трогая cfg; `fetch_proxies(cfg=...)` и
  `_sample_once(cfg)` принимают конфиг явно, а поллер передаёт cfg включившего аккаунта. Сбой сэмпла **логируется**.
- **`metrics_store._bars` читал ТОЛЬКО in-process ring** → в split-режиме бары на статус-странице замерли бы навсегда
  (ring пополняет лишь `record_samples`, т.е. процесс-поллер). Теперь SQLite; ring остался фолбэком для sqlite < 3.25.
  ⚠️ **Первый вариант (одна `ROW_NUMBER()`-выборка) мерился 1.5–2.0 с** на 504k строк — на ручке, которую дашборд
  опрашивает раз в 10с. Итог: **дискавери нод по covering-индексу `idx_samples_cid_sid` (NEW) + отдельный
  `ORDER BY ts DESC LIMIT n` на ноду** по `idx_samples_sid_ts` → **111 мс** на тех же данных. Не возвращать
  window-функцию: она ранжирует ВСЁ окно ретенции (35 дней) до фильтра `rn <= n`. Соседний `_uptime_30d` (был 907 мс)
  оптимизирован в задаче ревью (Волна 7): один grouped-scan вместо двух (global выводится из per-node
  сумм Σonline/Σcount, идентично старому AVG) + covering-индекс `idx_samples_cid_sid_ts_online`
  `(checker_id, stable_id, ts, online)` → **925 мс → 291 мс на 504k строк** (index-only, GROUP BY без temp-sort;
  измерено, EXPLAIN подтверждён; регрессия `test_metrics_store.py`).
- `infra_billing_store` — все 23 публичные функции приняли `account_id: Optional[str] = None` (ContextVar остался
  дефолтом, вызовы роутов не тронуты). В `**f`-функциях `account_id` стоит ВТОРЫМ позиционным, чтобы его не съел
  kwargs-мешок.

## 11. Волна 7 — отгруженные части (планы `docs/superpowers/plans/2026-07-22-wave7-*`)
> Планы A–F + зонтичный индекс. Отгружены A, B, C, D и E Ф1. Не сделаны: **План G**
> (= Волна 6, План E Ф2–Ф7, редактор страницы подписок), **E Ф2–Ф3** (единый ассистент),
> **F** (CLIProxyAPI через OAuth).

### 11a. Флаги стран — ТОЛЬКО SVG, никаких эмодзи (План A)
- **`components/common/FlagChip.tsx`** — единственный способ показать страну. Вынесен из `CountrySelect`;
  оттуда же его берут `Dashboard`, `HostingsMap`, `CountryPanel`, `ImportFromSubscription`.
  ⚠️ **Не возвращать эмодзи-флаги в UI:** `getFlagEmoji` строит пару regional-indicator, а несколько сборок
  Windows рисуют её двумя мелкими буквами. `getFlagEmoji` остался в `utils/format.ts` только ради
  `infra/InfraProviders.tsx:78` (чужая фаза).
- **`utils/countryAliases.ts`** — `resolveCountryCode(label) -> alpha-2` и `splitFlagEmoji(s)`. Порядок:
  эмодзи-флаг → известный 2-буквенный код (целиком или первым токеном) → английское имя из `COUNTRIES` →
  русский алиас. ⚠️ `COUNTRIES` (`CountrySelect.tsx`) — **англоязычный** (зеркало пикера панели), поэтому
  русские `groupName` из подписок резолвятся только через `RU_ALIASES`. Тест требует алиас для КАЖДОГО кода
  из `COUNTRIES` — при расширении списка правится и таблица.
- `Dashboard.NodeRow` вырезает эмодзи-флаг из имени узла и рисует его чипом; имя из одного лишь флага
  откатывается на код, чтобы строка не осталась без подписи.

### 11b. Гейт хардкод-цветов (План A Ф3)
- `theme/contrast.test.ts` дополнен обходом `src/**/*.tsx`. **ИЗМЕРЕНО:** широкая регулярка дала 11
  «нарушений», настоящим было ОДНО (`bg-blue-600/20 text-blue-300` на аватарке аккаунта). Остальные —
  два корректных идиома: `bg-white` (9 мест) — белый кружок тумблера на цветной дорожке (белый в обеих
  темах, как в iOS), `bg-black/75` — затемнение под модалкой. Поэтому правило: **white/black как ФОН —
  можно; любой именованный оттенок палитры Tailwind — нельзя** (игнорирует и тему, и акцент).
  Allow-list: `auth/AuthScreen.tsx` (намеренно тёмный гейт до выбора темы).

### 11c. Реестр панелей стал общим (План C)
- **`services/panel_registry.py`** — единственный резолвер `panel_id → PanelEntry | RemnavaveClient`.
  Пустой `panel_id` = активная панель (все прежние вызовы не изменились). ⚠️ **Неизвестный id бросает
  `PanelNotFound` (→404), а НЕ откатывается на активную** — тихая подмена записала бы конфиг в чужую панель.
- `api/config_templates.py` — `panel_id` у export/import; `GET /import/panel` эхом отдаёт `panel_id`.
- **`components/common/PanelPicker.tsx`** (контролируемый; прячется при одной панели) и
  **`components/common/PanelRegistry.tsx`** — список панелей вынесен из `Settings.tsx` и подключён ещё в
  «Установку» (`rw/PanelDashboard.tsx`). ⚠️ Второго списка над `active_panel_id` заводить нельзя.
  Выбор панели-источника синка живёт в состоянии страницы и НЕ пишет `active_panel_id`.
  `panel_jobs` (SSH-креды) остаётся клиентским: в реестр переносится только URL, токен вводится вручную.

### 11d. Хостинги (План D)
- `models/hostings.py::Tariff.bandwidth: str` — ширина канала свободным текстом (порт+гарантия+лимит одним
  куском). Учтён в фильтре «пустых» тарифов на клиенте, иначе тариф с одним каналом молча пропадал.
- `hostings/geo.ts::NUMERIC_TO_ALPHA2` + `alpha2OfGeo` — фичи `world-atlas` несут **числовой** ISO-id
  («528»), не alpha-2. Таблица сгенерирована из бандла, 62 из 64 стран.
  ⚠️ **Сингапура и Гонконга в `countries-110m` НЕТ** (датасет выбрасывает города-государства; в `50m` они
  есть — 702 и 344, но это +634 КБ против 1.43 МБ JS из перф-базы Волны 6). Поэтому панель страны
  открывается **и по клику на маркер** — маркеры рисуются по lat/lng и от полигонов не зависят.
  Три территории без `id` (Сев. Кипр, Сомалиленд, Косово) инертны.
- Границы: `stroke=var(--line)`, `0.6`, **`vectorEffect="non-scaling-stroke"`** — иначе `ZoomableGroup`
  масштабирует обводку (невидима на обзоре, жирная на зуме).
- `hostings/search.ts` — чистые `matchHosting`/`matchedCountries`/`parsePriceQuery` (`<20`, `>5`, `10-30`).
  Заменили тоглы континентов; «приблизить к региону» переехало в выпадашку. Подсветка стран считается
  один раз в `useMemo`, а не поиском внутри рендера каждой из 177 фигур.
- `HostingsCatalog` — клик по карточке открывает полный просмотр; иконки получили `stopPropagation`.

### 11e. Импорт серверов из подписки (План B)
- **`services/subscription_import.py`** — `decode_subscription` (base64 и plain-text) + `link_to_candidate`
  (через существующий `parse_xray_link`, второго парсера не заводим) + `country_of`.
  ⚠️ Ссылки несут секреты (у trojan пароль — это сама ссылка): ошибки не эхают вход, есть тест.
- **`POST /api/server-monitor/import/subscription`** — фетч и разбор на бэкенде (CORS + секреты),
  SSRF-гард, `follow_redirects=False`, лимит 4 МиБ. `dry_run=true` по умолчанию.
  Дедуп по `(host, port)`; статусы `new | duplicate | unresolved`.
  ⚠️ **Домен резолвится в IPv4 при импорте**, оригинал кладётся в `note`: `servers.ip` — адрес. Цена —
  узел за round-robin DNS пинится к одной A-записи. Альтернатива (хранить домен, резолвить при пробе)
  дешёвая — `_probe` и так принимает hostname, — но меняет смысл поля и `_valid_ipv4` в `sync_deployed`.
  ⚠️ `source='manual'`, а НЕ новый `'subscription'`: `update_server` ограничен manual, ре-синк трогает
  только `deployed` → отдельный source сделал бы строки нередактируемыми (тупик Волны 6).
- Frontend `components/ImportFromSubscription.tsx` (кнопка «Из подписки» на вкладке «Доступность серверов»).

### 11f. Настройки: вкладки в несколько рядов (План E Ф1)
- `.seg-wrap` в `index.css` — **модификатор**, базовый `.seg` не трогать (используется ещё в ~6 местах).
  Многорядный вариант рисует отдельные «пилюли»: скругления сегментного контрола рассчитаны на один ряд.

### 11g. Верификация фронтенда — квирк окружения
- Node на машине разработки нет; всё гоняется в Docker (`ni-frontend-test` из builder-стадии
  `frontend/Dockerfile`, Playwright — `mcr.microsoft.com/playwright:v1.61.1-jammy`). В Git Bash монтировать
  с `MSYS_NO_PATHCONV=1` и **абсолютным** путём (относительный `$(pwd -W)/frontend` ломается, если shell уже
  внутри `frontend/` → «No test files found»).
- ⚠️ **vitest в контейнере теряет файлы: `Failed to start worker` / `Timeout waiting for worker to respond`**
  (ровно 60 с, `transform 0ms`). Это НЕ падение теста — воркер вообще не стартовал, и файл просто **не попадает
  в итог**, из-за чего «N passed» выглядит зелёным, покрывая лишь часть набора. Всегда сверять число файлов.
  **Измерено (оба прогона под конкурирующей нагрузкой):** без ограничений — 26 файлов из 37 не стартовали
  (в итог попало 11 файлов / 89 тестов); с `--maxWorkers=2` — **12** не стартовали (25 файлов / 150 тестов).
  Ограничение помогает вдвое, но **не лечит**: `--minWorkers` в vitest 4 не существует (падает `CACError`).
  Практика: `--maxWorkers=2`, не запускать параллельно с другой тяжёлой задачей, «упавший» файл перезапустить
  по одному прежде, чем чинить. В `vitest.config.ts` лимит НЕ прописан: это ограничение машины, не проекта.
- **Пре-существующие падения** (не связаны с Волной 7, воспроизводятся на чистом `main`):
  `rw/PanelManageModal.test.tsx` «Статистика» (ждёт `/api/stats/node`, получает `/api/panel/metrics`) и
  `theme/tweaks.test.ts` «exactly the two skin options» (скинов три). Не приписывать их своим правкам.

### 11h. Страница подписок — редактор (План G, отгружено 2026-07-23/24)
> Все ЧЕТЫРЕ блокирующих неизвестных сняты на живой панели 2.x (`scripts/probe_subpage_config.py`) +
> bind-mount-эксперименте. Бэкенд Плана G отгружен ЦЕЛИКОМ (Ф2/Ф4/Ф5/Ф6). Осталось только фронт-редактор
> поля `config` и селектор варианта в форме деплоя.

- **Ф2 — каталог оформления ПАНЕЛИ** (`api/subpage_configs.py`, `/api/subpage-configs`; клиентские методы в
  `remnawave_client.py`). Прокси, локального стора нет. ⚠️ **Листинг панели отдаёт `config: null` у ВСЕХ
  записей** — `config` приходит только в детали `GET /{uuid}`. Редактор ОБЯЗАН тянуть деталь per-config.
  ⚠️ Имя 2..30 (`^[A-Za-z0-9_\s-]+$`), НЕ [:255] как у templates. Обновление — PATCH на КОЛЛЕКЦИЮ с uuid в
  теле (PATCH/{uuid} нет). Реордер — `{items:[{uuid,viewPosition}]}` (не `{uuids}` как в MCP-форке). Клон —
  `POST /actions/clone {cloneFromUuid}` (НЕ `/{uuid}/clone`).
- **Форма поля `config` (снята с живой панели):** структурированный объект `{baseSettings, baseTranslations,
  brandingSettings, locales, platforms, svgLibrary, uiConfig, version}`; `platforms.{ios,…}.apps[].blocks[]`
  с локализованными title/description/buttons. **PATCH — MERGE** (name-only PATCH оформление НЕ трогает →
  переименование безопасно). Привязка config→юзер идёт через **внешний сквад** (`subpageConfigUuid`), поэтому
  контейнеру **`SUBPAGE_CONFIG_UUID` в .env НЕ нужен**.
- **Ф5 — overlay-стор** (`services/subpage_store.py`): `kind: html|overlay`. Legacy html не тронут. Дерево
  `accounts/<id>/subpages/<page_id>/files/<relpath>`, отдельный `manifest.json`. ⚠️ overlay-запись несёт
  числовой `size` (каталог рисует `fmtSize(p.size)`, undefined→«NaN КиБ»). `normalize_relpath` — свой гард на
  relpath (`\` НЕ нормализуется в `/`, а отвергается). Члены отдаются **непрозрачной загрузкой** (octet-stream
  + attachment + nosniff), НИКОГДА не рендерятся на нашем origin. Роуты `/api/subpages/overlay|{id}/files|
  {id}/download`.
- **Ф4 — baseline из образа** (`services/subpage_baseline.py`, `/api/subpages/baselines/*`): docker create+cp
  дерева `/opt/app/frontend`, кэш по digest (глобальный — только вендорская сборка). ⚠️ **`docker create` ДО
  `inspect`** (create авто-пуллит, inspect — нет). tar-slip гард полный и ДО записи (абсолютные/`..`/симлинки/
  бюджет). ⚠️ `_tar_name` снимает ровно один ведущий `./`, НЕ `lstrip("./")` (тот съел бы `/etc`→`etc`,
  `../evil`→`evil`).
- **Ф6 — деплой overlay на ноду** (`panel_pipeline.py` + `models/panel_deploy.py:subpage_variant_id` +
  `api/panel_deploy.py` прокидывает `account_id`). `_subpage_compose`: overlay → маунт КАТАЛОГА
  `./frontend:/opt/app/frontend`, legacy → файл. `_deploy_subpage_overlay`: digest-warn → материализация из
  образа → SFTP zip → unzip. ⚠️ **`find <dir> -mindepth 1 -delete`, НЕ `rm -rf <dir>`** — под живым bind-mount
  `rm -rf` точки монтирования даёт rc=1 (обрывает `set -e`) и оставляет контейнер без файлов; проверено на
  Docker. Контейнер НЕ останавливается; финал `up -d --force-recreate` перезапускает приложение (на ФС файлы
  видны сразу, Node/EJS кэширует шаблон — нужен рестарт).
- **Фронтенд Ф3+часть Ф7** (отгружено фоновой сессией 2026-07-23, `rw/SubPages.tsx` — две вкладки). Детали в
  памяти [[wave7-plans]]. **НЕ сделано:** редактор поля `config` (форма известна, но SubPages.tsx правился
  параллельно — не коллизили) и селектор варианта в `PanelDeployForm` (textarea→`<select>`).
- **`scripts/probe_subpage_config.py`** — самодостаточный (без jq, сам находит запись, PATCH-тест на КЛОНЕ →
  оригинал не тронут) снималка формы/семантики с живой панели. `PANEL=… TOKEN=… python scripts/…`. Windows:
  `sys.stdout.reconfigure(utf-8)` внутри, иначе cp1251 роняет кириллицу.

## 12. HAPROXY — интеграция панели NodeFlow (deploy + proxy)
> Запрос (2026-07-25): «Интегрируй функции прикреплённой панели NodeFlow в новую группу разделов HAPROXY».
> Архитектура (выбор пользователя): **deploy + proxy** — node-installer РЕГИСТРИРУЕТ инстанс NodeFlow (URL +
> `PANEL_ADMIN_TOKEN`) пер-аккаунт и проксирует его `/api/v1/*`, а разделы группы «HAPROXY» — нативные React-
> страницы, бьющие в наш прокси. Реальный Go-агент + HAProxy-движок NodeFlow ПЕРЕИСПОЛЬЗУЕТСЯ, НЕ переписывается.
> План `docs/superpowers/plans/2026-07-25-haproxy-nodeflow-integration.md`. **Обновление (2026-07-25):** по
> умолчанию локальный NodeFlow **авто-деплоится** (см. §12b); «существующая панель» стала опцией.

- **Что такое NodeFlow:** отдельный продукт — Go-панель + Postgres + mTLS-PKI + скомпилированный node-agent +
  подписанные релизы. Ноды гоняют агент по mTLS, тот управляет **HAProxy** TCP-relay «маршрутами». Панель:
  `GET /overview`, `GET/PATCH /settings`, `POST /bootstrap`(+`/host-key`,`GET /{job}`), `GET/POST /nodes`(+`/order`),
  пер-нода `{id}`: `GET/PATCH/DELETE`,`/operational`,`/audit`,`/traffic`(+`/history`),`/firewall`,`/haproxy`(вкл/выкл),
  `/agent-update`(+`/rollback`),`/routes`(GET/POST,+`/order`,+`/{rid}` GET/PATCH/DELETE),`/rotate-credentials`,
  `/reinstall`,`/config-revisions`, `GET/POST/DELETE /agent-releases`.
- **⚠️ Ключевая находка (auth):** `/api/v1/*` принимает `Authorization: Bearer <PANEL_ADMIN_TOKEN>`, и для
  bearer-пути NodeFlow ПРОПУСКАЕТ same-origin/cookie/CSRF-проверки (они только для браузерных сессий). Поэтому
  серверный прокси, инжектящий токен, гоняет ВСЕ функции без сессии. (`internal/panel/http.go::admin()`.)
- **Backend:** `HaproxyConfig` на `AppSettings` (`enabled`, `base_url`, `admin_token_enc` — **Fernet**, как
  MCP/cliproxy; наружу токен не отдаётся, только `has_token`). `services/nodeflow_client.py` — httpx-клиент:
  `request(method, subpath, params, content, headers)` → `{base}/api/v1/{subpath}` с bearer, `follow_redirects=
  False`, **SSRF-гард `net_guard.is_safe_url` и при регистрации, И на каждом запросе** (DNS-rebinding). `check()`
  = health + аутентифицированный GET /settings. `api/haproxy.py` (под `require_account`): `GET/POST /api/haproxy/
  config`, `POST /test`, и **дженерик-прокси `ANY /api/haproxy/proxy/{path:path}`** — сырое тело passthrough
  (JSON И multipart-загрузка релиза), стрипает hop/auth-заголовки, инжектит bearer, доступен только префикс
  `/api/v1/`. **Изоляция:** каждый аккаунт регистрирует СВОЙ инстанс → прокси всегда целится в его панель с его
  токеном. Роутер подключён в `main.py` под `_auth`. Тесты `test_haproxy.py` (7: гейт, SSRF-reject на save,
  шифрование-at-rest + blank-keeps, not-configured-гарды, forward метод/subpath/query + upstream-статус, Fernet).
- **Frontend `components/haproxy/`:** `contracts.ts` (типы портированы из NodeFlow), `api.ts` (config/test +
  `nf()`-прокси, `messageOf` парсит и наш `{detail}`, и NodeFlow `{error}`; `asList` нормализует
  bare-array vs `{nodes|routes|releases:[]}`), `format.ts` (байты/битрейт/аптайм/тон). **Гейт вынесен в
  `gate.tsx`** (`useHaproxyReady`+`NotConnected` — импортят 6 операционных страниц; текст ведёт в
  Настройки→«HAProxy»). Страницы: `HaproxyConnect` (connect-UI: сегмент local/remote — теперь это **вкладка
  Настройки→«HAProxy»**, БЕЗ `Page`-обёртки), `HaproxyOverview` (KPI+топ-маршруты, 15с-поллинг), `HaproxyNodes`
  (+`HaproxyAddNode` 3-шаговый мастер bootstrap: host-key-скан→подтверждение отпечатка→POST /bootstrap + polling
  job; **секреты чистятся при сабмите**; +`HaproxyNodeDetail`: heartbeat-KPI, вкл/выкл HAProxy, ротация кредов,
  удаление), `HaproxyRoutes`(+редактор: `routeModel.ts` draft↔record↔payload, лёгкая клиент-валидация — глубокую
  делает `route_validation.go`), `HaproxyTraffic`, `HaproxyFirewall` (off/observe/apply + порты), `HaproxyReleases`
  (список+удаление; загрузка/подпись — в самой NodeFlow).
  **Реорг нав-групп:** группа «HAPROXY» в `Sidebar.tsx` (после Remnawave) = **Ноды/Маршруты/Файрвол/Релизы**;
  статистические **«Обзор»→«HAProxy: обзор»** (`haproxy-overview`) и **«Трафик»→«HAProxy: трафик»**
  (`haproxy-traffic`) перенесены в группу **«Статистика»** (`CRUMB` → `["Статистика", …]`); «Настройки»
  (`haproxy-settings`) удалён как таб — connect-UI переехал во вкладку **Настройки→«HAProxy»** (`Settings.tsx`
  `SubTab "haproxy"` рендерит `<HaproxyConnect/>`). Табы `haproxy-overview`/`haproxy-traffic` оставили те же id
  (меньше churn), рендер-свитч в `App.tsx` не тронут для них.
  Тесты `routeModel.test.ts` (13: payload any_tcp/sni/unix, expected_version, квоты, валидация, round-trip, asList).
- **Отклонения/заметки:** Дженерик-прокси не перечисляет ~30 эндпоинтов и авто-покрывает новые версии NodeFlow.
  Reinstall ноды (нужен полный bootstrap-body с кредами) в UI пока НЕ вынесен — доступны вкл/выкл HAProxy, ротация
  кредов, удаление. Verify: `pytest tests/test_haproxy.py test_nodeflow_server.py` (15) + Docker
  `vitest routeModel.test.ts` (13) + `tsc`.

## 12b. HAPROXY — локальный авто-деплой NodeFlow (по умолчанию)
> Пользователь (2026-07-25): «Пусть добавление существующей панели — лишь опция; по умолчанию автоматически
> деплоится и включается локальная версия». Выбор: **полный авто-деплой** (вендоринг исходников + Go/Node-билд +
> DooD-стек). `HaproxyConfig.mode: local(default)|remote`.

- **Вендоринг:** исходники NodeFlow скопированы в `nodeflow/` (157 файлов, апстрим без изменений, кроме
  добавленного `Dockerfile.migrate`; **лицензии у апстрима НЕТ** — задокументировано в `nodeflow/VENDORED.md`, как
  вендоренный mihomo). Панель **собирается из исходников** (публичного образа нет).
- **Compose (профиль `nodeflow-build`, как MCP):** сервисы `nodeflow-panel` (`Dockerfile.panel`, Go 1.25 + React
  билд → `node-installer-nodeflow-panel:latest`) и `nodeflow-migrate` (`postgres:17-alpine` + запечённые миграции +
  migrate.sh → `node-installer-nodeflow-migrate:latest`). Compose их НЕ стартует; бэкенд оркестрирует по DooD.
  Билд: `docker compose --profile nodeflow-build build nodeflow-panel nodeflow-migrate`.
- **`services/nodeflow_server.py` — оркестратор (SHARED singleton, как xray-checker/mcp/cliproxy):**
  - **PKI в Python (`cryptography`), НЕ openssl/root:** `generate_pki(san_host)` — Ed25519 CA + серверный серт
    (SAN = ПУБЛИЧНЫЙ адрес хоста, агенты идут на него по mTLS :4200) + Ed25519 update-signing-key. **Идемпотентно** —
    регенерация CA осиротила бы уже зачисленные ноды. Ключи 0440 root:65532 (или 0444 fallback в тестах).
  - **Глобальный волт** `DATA_DIR/nodeflow/state.json` (Fernet): admin-токен + пароль postgres генерятся ОДИН раз
    (`ensure_state`), НЕ в per-account settings (стек общий). `san_host` фиксируется при первом деплое.
  - **`deploy()`**: docker/образы есть? → PKI → postgres(`nodeflow-postgres`) → ждать `pg_isready` → миграции
    (одноразовый `--rm` образ, миграции запечены → **без host-path-маунта** под DooD) → инициализация прав
    releases-тома → панель(`nodeflow-panel`) → ждать `/healthz`. Публикуется наружу ТОЛЬКО порт агента **4200**;
    UI-порт 8080 — по имени контейнера через наш прокси, наружу НЕ публикуется.
  - **PKI/TLS монтируются в панель через node-data `volume-subpath`** (только `nodeflow/pki`+`nodeflow/tls`, НЕ весь
    том с данными аккаунтов — третьесторонняя панель не должна видеть чужое). Имя тома резолвится инспектом
    СОБСТВЕННОГО контейнера (`_node_data_volume`, префикс compose-проекта неизвестен заранее).
  - `deploy_bg()` — фоновый деплой (~60-90с) с single-flight + `_DEPLOY{running,error}`; `POST /deploy` отвечает
    сразу, фронт поллит `/local/status`. Docker/образы отсутствуют → warning (не 500), как MCP.
  - Персистентность: у контейнеров `--restart unless-stopped` → переживают рестарт бэкенда, отдельный lifespan-
    autostart НЕ нужен.
- **API (`api/haproxy.py`):** `_client_or_400` ветвится по mode — **local**: `internal_base_url()` +
  глобальный токен + `allow_internal=True` (SSRF-гард ЭКЗЕМПТ для имени контейнера, как xray_checker); **remote**:
  per-account base_url+токен. Ручки: `GET/POST /config` (+mode), `POST /deploy` (фон, ставит local+enabled),
  `POST /stop`, `GET /local/status`. `nodeflow_client.NodeFlowClient(..., allow_internal)` пропускает гард только
  для внутреннего URL.
- **Frontend `HaproxyConnect`** (теперь вкладка **Настройки→«HAProxy»**, не отдельный нав-таб): сегмент
  «Локальная (авто)» / «Существующая панель». Local: статус контейнеров + «Развернуть/Переразвернуть»/«Остановить»
  + опц. `san_host` + **авто-деплой ОДИН раз** на маунте (нет токена + образы собраны + idle) + плашка-требование
  (публичный хост, порт 4200). Remote: прежняя форма URL+токен. **Авто-деплой срабатывает при открытии вкладки
  Настройки→«HAProxy»** (компонент монтируется там); операционные страницы группы «HAPROXY» гейтятся `gate.tsx`.
- **⚠️ Требования локального режима:** хост node-installer СТАНОВИТСЯ хостом NodeFlow-панели — нужен публичный
  IP/DNS и открытый **4200/tcp** (агенты подключаются туда). SAN авто из `backend_ip.get_backend_ip()`, override в
  форме. Стек ОБЩИЙ на все аккаунты (как прочие DooD-синглтоны) — все видят одни ноды NodeFlow.
- **Verify:** `pytest tests/test_nodeflow_server.py` (5: PKI подпись/SAN IP+DNS/идемпотентность, волт-шифрование,
  argv-билдеры) + `test_haproxy.py` (10: local-default, remote-SSRF, 409-local/400-remote гейты, forward,
  images-not-built warning, internal-SSRF-exempt). `docker compose --profile nodeflow-build build` (migrate собран;
  panel — Go/Node билд). `tsc` чисто.

## 13. Волна 8 — теги/токен/ASN · балансер(selector) · анализ подписки · автобэкап · Обновления
> План `docs/superpowers/plans/2026-07-25-wave8-tags-updates-backup-balancer-subanalysis.md` (7 пунктов, 5 фаз
> Ф1–Ф5). Всё per-account, кроме §3 «Обновления» (глобально, host-level). Отгружаемые дельты ниже.

### 13a. Ф1 — теги хостингов · просмотр токена · поле ASN
- **Теги (§1):** `HostingBody.tags: list[str]` (`models/hostings.py`, `field_validator` нормализует: `" ".join(
  raw.split())` → трим+схлоп пробелов/CR/LF, `[:24]`, дедуп, ≤10). Пул тегов НЕ отдельная сущность —
  `GET /api/hostings/tags` отдаёт `sorted(set(all tags))` для автодополнения (объявлен ДО параметризованных путей;
  `GET /{id}` нет — конфликта нет). Фронт: `hostings/TagInput.tsx` (чипы + инпут с `<datalist>` из `/tags`,
  зеркалит бэкенд-лимиты), встроен в редактор `HostingsCatalog`. Чипы на карточке **кликабельны → фильтр каталога
  по тегу** (`tagFilter` state, клиентский). `search.ts::haystack` матчит теги. ⚠️ Старые записи без ключа `tags`/
  `asns` — везде читать `(h.tags||[])`/`(h.asns||[])` (стор не ревалидирует через модель, отдаёт сырой JSON).
- **Просмотр токена (§2):** `Settings.tsx::SettingField` — password-поля получили reveal-тумблер (Eye/EyeOff,
  локальный `reveal` state, `type` password↔text). Покрывает и Remnawave api_token, и CF-токен. Токен и так уже
  в браузере (plaintext в settings.json) — это защита от «подглядывания через плечо», не секьюрити-граница.
- **ASN (§6):** `HostingBody.asns: list[AsnRef]`, `AsnRef{number:int ge=0, name:str, website:str}` (структурный,
  не список строк — §7 пишет name/website). Редактор в `HostingsCatalog` (номер/имя/сайт), блок «ASN» в details.
  Заполняется вручную ИЛИ кнопкой §7. `search.ts` матчит имя+номер ASN.
- **Проверка:** `test_hostings.py` (+3: нормализация тегов/лимиты, `/tags` дедуп+изоляция, asns roundtrip+ge=0) —
  9/9; `tsc` чисто; `search.test.ts` 13/13 (+теги/ASN/legacy-без-ключа).

### 13b. Ф2 — балансер (selector) вместо `$hostid`
- **Переменной `$hostid` НЕТ**. Host UUID дописывается в РЕАЛЬНЫЙ selector Remnawave: `remnawave.injectHosts[].
  selector={type:"uuids",values:[host-uuid…]}` (+`tagPrefix`). **⚠️ ГДЕ ЖИВЁТ SELECTOR (снято с живой панели):**
  НЕ в config-профилях (там стандартный xray log/dns/inbounds/outbounds/routing, injectHosts НЕТ), а в
  **XRAY_JSON subscription-templates** — `templateJson.remnawave.injectHosts`. Хост ссылается на такой шаблон через
  `xrayJsonTemplateUuid`; шаблон-балансер («Auto»/«NL_auto»/«RU_auto») имеет группы вроде `foreign-proxy`/
  `wl-proxy`/`russian-proxy`. `xrayJsonTemplateUuid` хоста = uuid subscription-template типа XRAY_JSON.
- **`services/xray_selector.py` (чистые хелперы, работают на `templateJson`):** `list_uuid_groups(tj)->[{tag_prefix,
  count}]`, `add_uuid`/`remove_uuid`/`remove_uuid_everywhere` → `(new_tj, changed)`. Deepcopy, дедуп, порядок
  values сохраняется, группа не найдена/битый tj → `changed=False`, НЕ бросают. (Структура `remnawave.injectHosts`
  идентична и в config, и в templateJson — модуль не менялся при смене источника.)
- **API:** `GET /api/remnawave/balancers` (`api/settings.py`) — `list_subscription_templates` → фильтр
  `templateType=="XRAY_JSON"` → per шаблон `get_subscription_template(uuid).templateJson` → `list_uuid_groups` →
  `[{template_uuid, template_name, tag_prefix, count}]`. Гейт «панель не настроена» → 400.
- **Модель:** `HostTemplateBody.balancers: list[BalancerRef]`, `BalancerRef{template_uuid, tag_prefix}`
  (`tag_prefix` charset `[A-Za-z0-9_.\-]{1,64}`).
- **Жизненный цикл:** **добавление** — `pipeline._apply_host_balancers` в `step_create_hosts` СРАЗУ после
  `create_host` (взяли `created["uuid"]`): для каждого `tpl.balancers` → `get_subscription_template` → `add_uuid`
  → `update_subscription_template(template_json=…)`. Best-effort, per-balancer failure = warn, идемпотентно.
  **Удаление** — `node_ops._cleanup_remnawave_balancers` при `component=="remnanode"`: матчит хосты ноды по
  `address==req.domain` → `remove_uuid_everywhere` по всем XRAY_JSON-шаблонам. ⚠️ Ручное удаление хоста в панели/
  удаление карточки деплоя selector НЕ чистят.
- **Frontend `Hosts.tsx`:** MultiSelect «Балансеры» (вкладка «Расширенные»), опции из `/balancers`, label
  «<шаблон> · <tagPrefix> (N)» (напр. «Auto · foreign-proxy (4)»), value `<template_uuid>::<tagPrefix>`
  (`balKey`/`balParse`). Гейт «панель не настроена» → hint. `MultiSelect` экспортирует `SelectOption`.
- **Проверка:** `test_xray_selector.py` (11), `test_hosts.py` (+balancers roundtrip+tag_prefix charset),
  `test_host_autocreate.py` (+append в templateJson после create_host/группа-не-найдена→no-write/без balancers→
  no-write) — 36 зелёных; `tsc` чисто. **Проверено на живой панели:** read даёт 5 групп в 3 XRAY_JSON-шаблонах;
  add→PATCH→read (count +1) и remove_uuid_everywhere→PATCH→read (restore) round-trip на реальном шаблоне «Auto».

### 13c. Ф3 — «Анализ подписки» (§7)
- **`services/subscription_analyze.py`** — вход url/домен/ip (`classify_input`). URL → `fetch_subscription`
  (SSRF-гард `net_guard.is_safe_url` + ручные редиректы per-hop + лимит 4 МиБ) → `decode_subscription`+
  `link_to_candidate`. **⚠️ UA-FALLBACK (`_SUB_USER_AGENTS`):** панели отдают РАЗНЫЙ формат по User-Agent, поэтому
  пробуем цепочку и берём ПЕРВЫЙ ответ, который парсится в share-ссылки: **дефолтный httpx-UA** (None) первым →
  затем клиентские `Happ` / `incy` / `Streisand` / `Shadowrocket`. **БАГ-УРОК:** сначала слали ТОЛЬКО `v2rayNG`-UA
  → `hardsub.digital` отдал 129 КБ `application/json` (xray-конфиг, НЕ ссылки) → 0 хостов («Серверы не найдены»).
  Дефолтный UA даёт стандартный base64-список (12 ссылок). Цепочка нужна для панелей, которые дают список ТОЛЬКО
  под конкретный клиент (UA-список: default → `Happ/1.16.0` → `INCY/3.3.7/android` → `Streisand` → `Shadowrocket`).
  То же у **subs-aggregator** (`_fetch_sub_lines`/`_safe_fetch(user_agent)`, детект по `_has_link_lines`: строка
  стартует со схемы vless/vmess/…) — единственная точка, где мы фетчим подписку для xray-checker (сам Go-контейнер
  в прямом режиме мы не трогаем). Гео/RDAP-клиент шлёт нейтральный `node-assistant`
  (переиспользованы из `subscription_import`) → хосты. Per-IP (`_resolve_ip`): **ASN + базовое гео** = `ip-api.com/
  json` (fallback `ipwho.is`); **факт. гео = ТРАССИРОВКА** (`_traceroute_last_hop`, ниже); **реестр. страна** = RDAP
  `rdap.org/ip` → **RIPEstat `rir-geo` fallback** (`stat.ripe.net`, закрывает ARIN/US-дыры RDAP); **сайт ASN** = RDAP
  `rdap.org/autnum` → **PeeringDB fallback** (`peeringdb.com/api/net?asn=`, кэш по ASN). Внешние API — ФИКСИРОВАННЫЕ
  публичные хосты (не user-controlled → не через net_guard); ссылки/вход в логи не попадают.
- **⚠️ Факт. гео через traceroute:** `_traceroute_last_hop(ip)` запускает системный `traceroute -n -q1 -w1 -m12`
  (Linux) / `tracert -d` (Windows), берёт ПОСЛЕДНИЙ публичный хоп (сам хост если отвечает, иначе роутер ДЦ — его
  гео ближе к реальности, чем IP-база) и геолоцирует его через ip-api; если traceroute недоступен/чёрная дыра →
  fallback на гео самого destination. ASN всегда с destination. Гео теперь ОТНОСИТЕЛЬНО сервера бэкенда (откуда
  трасса) — это и нужно. Бинарь `traceroute` добавлен в backend `Dockerfile`; Docker по умолчанию даёт `NET_RAW` +
  бэкенд root → работает без `cap_add`. `_TRACE_SEM=8`, `_TRACE_TIMEOUT=20`, `shutil.which` guard.
- **⚠️ Дедуп по ИМЕНИ хоста, не по IP (feedback):** `analyze` группирует ссылки по HOSTNAME (каждый адрес
  резолвится ОДИН раз), затем сливает по IP. Иначе один хост (напр. фронт `github.com`, встречается в 3 ссылках
  через domain-fronting) + round-robin DNS давали 3 строки с разными IP. Имена ссылок (`#fragment`/vmess `ps`)
  агрегируются per-host → `row.names` (в UI колонка «Название», через запятую; напр. `github.com` = `🇫🇲 Авто,
  🇳🇱 Нидерланды, 🇷🇺 Россия`). `row.hosts` = все адреса на этом IP. Балансер/фронт-конфиг из share-ссылки
  «разбить на реальные хосты» НЕЛЬЗЯ (реальный бэкенд скрыт за фронтом) — показываем имя + даём удалить строку.
- **⚠️ RDAP `_rdap_get`: `follow_redirects=True` ОБЯЗАТЕЛЕН** — `rdap.org` 301-редиректит на RIR-RDAP
  (`rdap.db.ripe.net`/`rdap.arin.net`/…); без этого «Реестр» был пуст ВЕЗДЕ (клиент analyze дефолтно
  `follow_redirects=False`). + retries (Cloudflare-фронт rdap.org иногда ConnectError'ит). ARIN top-level country
  НЕ отдаёт → раньше прочерк; теперь **RIPEstat `rir-geo` fallback заполняет 10/10** (github→US, 206.x→US при факт-
  гео SG = реальное расхождение, 77.x→AE). Сайт ASN: RDAP autnum часто пуст → **PeeringDB заполнил 5/10** (github/
  plym/albahost/vdsina/regxa; остальные реально без сайта).
- `group_to_hostings` — одна `HostingBody` на ASN (дедуп по номеру, локации = уник. факт. cc/city, имена ссылок →
  `notes` «Из подписки: …», записи без ASN пропускаются).
- **`api/sub_analysis.py`** (`/api/subscription-analyze`, под `_auth`): `POST ""` (dry-run → `{kind, results:[{host,
  hosts[], names[], ip, asn{number,name,website}, geo_actual{cc,city}, geo_registry{cc}}]}`; пусто→400, сбой→502) +
  `POST /to-hostings` (`{results}` → `group_to_hostings` → **upsert** в `hostings_store`: матч по ASN-номеру ИЛИ
  имени → мерж локаций, иначе новая карточка; нет ASN → 400). Роутер подключён в `main.py`.
- **Frontend `components/SubscriptionAnalyze.tsx`** — nav-таб «Анализ подписки» (группа **«Справка»**, `Tab
  "subscription-analyze"`, `ScanSearch`). Инпут → таблица (Название/Хост/IP/ASN/факт-гео `FlagChip`/Реестр/**Website**
  + `AlertTriangle` при расхождении cc + **✕ убрать строку** — удалённые не идут в «Добавить в хостинги»).
  **Колонки resizable:** `table-layout:fixed` + `<colgroup>` + per-column `widths` state; тянуть за правый край
  заголовка (`startResize`, `col-resize`); последняя колонка (✕) фиксированная. Страница расширена до `max-w-6xl`.
- **Проверка:** `test_subscription_analyze.py` (13: classify/parse_as/ip_public/group-dedup+notes/analyze-dedup-
  hostname+names/**resolve_ip-fallbacks (RIPEstat+PeeringDB+trace-hop)**/SSRF-reject/UA-default-first/UA-fallback/
  route-empty-400/route-monkeypatched/to-hostings-merge/no-asn-400). `subs-aggregator/test_app.py` 12/12. `tsc`
  чисто. **Проверено вживую:** registry 10/10 (RIPEstat), website 5/10 (PeeringDB), traceroute-гео, ~11с.

### 13d. Ф4 — автобэкап → Telegram (§4)
- **`AutoBackupConfig`** на `AppSettings` (`models/settings.py`): `{enabled, interval_hours(1..8760), include_secrets,
  chat_id, bot_token_enc(Fernet), last_run, last_error}`. Токен — Fernet-волт (ключ = SHA-256 `encryption_key`),
  наружу НИКОГДА (только `has_token`).
- **`telegram.send_document(bot_token, chat_id, filename, data, caption)`** — multipart `sendDocument`, timeout 60с,
  best-effort, `redact` в логах (как `send_message`).
- **`services/auto_backup.py`:** `encrypt_token`/`decrypt_token` (свой `_fernet`, как ai_agent/mcp_server), `run_once(
  account_id)` = `export_service.build_archive(account_id, include_secrets)` → `send_document`; пишет last_run/
  last_error. `loop()` — фон, гейт `worker_lease.MONITORING`, per-account explicit account_id, каждые 15 мин
  проверяет `enabled && now≥last_run+interval*3600`. Подключён в lifespan (`main.py`, в списке tasks).
  ⚠️ `build_archive` уже поддерживает `include_secrets` — автобэкап зовёт СЕРВИСНУЮ функцию напрямую (браузерный
  роут `/api/export` по-прежнему 400 на `include_secrets=true`, секреты наружу по HTTP не отдаём).
- **API (`api/settings.py`, под `_auth`):** `GET /api/settings/auto-backup` (стрипает `bot_token_enc`, отдаёт
  `has_token`), `POST /api/settings/auto-backup` (`bot_token` write-only, blank=keep), `POST /api/settings/
  auto-backup/run` («Отправить сейчас», ошибка→400).
- **Frontend `settings/DataTransfer.tsx`:** карточка «Автобэкап → Telegram» (тумблер, интервал, chat_id, bot_token
  password с плашкой-плейсхолдером «токен сохранён», чекбокс «Включать секреты» + amber-предупреждение про
  приватность чата, last_run/last_error, «Сохранить»/«Отправить сейчас»).
- **Проверка:** `test_auto_backup.py` (5: fernet-roundtrip, config-CRUD+token-hidden+blank-keeps, run-sends-document,
  run-no-token-400, include_secrets-propagates). `tsc` чисто.

### 13e. Ф5 — «Обновления» (§3, DooD self-update sidecar)
- **`services/updater.py` (глобальный, host-level, НЕ per-account):** бэкенд работает из ОБРАЗА (код скопирован,
  `.git` в контейнере нет) → git/compose гоняются в **короткоживущем sidecar-контейнере**, который bind-маунтит
  ХОСТ-путь репо (`project_dir()` = лейбл `com.docker.compose.project.working_dir` СВОЕГО контейнера через
  `docker inspect $(hostname)`, как `nodeflow_server._node_data_volume`) + docker-сокет. Дефолт-образ `docker:cli`
  (alpine, `apk add git` в скрипте; compose-плагин в образе). ⚠️ **`apply()` — ДЕТАЧНЫЙ** (`docker run -d`):
  переживает пересоздание бэкенда при `compose up -d`; прогресс пишется в `/data/updater_status.json` (том
  node-data, который читает пересозданный бэкенд). Конфиг — `DATA_DIR/updater.json` `{auto_update, branch, image}`,
  статус — `DATA_DIR/updater_status.json`.
- **Pure-функции (тестируемые):** `parse_check_output` (маркеры `===LOCAL/REMOTE/SUBJECT/BRANCH===`), `is_behind`,
  `_safe_branch` (charset `[A-Za-z0-9._/-]`, инъекция в скрипт закрыта), `check_argv`/`apply_argv` (билдеры sidecar-
  argv), `_check_script`/`_apply_script`. `check()` (60с кеш; Docker/git-absent → `docker:false`+warning, НЕ 500),
  `apply()` (rm старого updater-контейнера → detached run; Docker absent → warning). `auto_loop` (lifespan, гейт
  `worker_lease.MONITORING`, каждые 6ч: `auto_update && behind` → `apply`).
- **API (`api/updates.py`, под `_auth`):** `GET /api/updates/status` (check + `progress` из updater_status.json),
  `POST /api/updates/config` (`{auto_update, branch, image}`), `POST /api/updates/apply` (200 c warning при отсутствии
  Docker — как MCP/nodeflow). ⚠️ **Любой аутентифицированный аккаунт инициирует host-wide рестарт** (как прочие
  DooD-синглтоны — задокументировано).
- **Frontend `settings/UpdatesTab.tsx`** (Settings→«Обновления», `SubTab "updates"`): версия (ветка/local/remote
  коммит/behind+subject), поллинг прогресса пока sidecar работает, тумблер автообновления, ветка/образ, «Проверить»/
  «Обновить сейчас» (confirm) + плашка-предупреждение о host-wide рестарте.
- **Проверка:** `test_updater.py` (10: is_behind/safe_branch/parse-output(+missing)/check_argv/apply_argv-mounts/
  apply_script-builds+no-injection/config-roundtrip/status-roundtrip/check-no-docker). `tsc` чисто.
  ⚠️ Sidecar НЕ гонялся вживую (нужен реальный Docker+репо на хосте) — покрыты argv/парсинг/персистентность.

## 14. Публичная точка входа — nginx reverse-proxy + TLS + `install.sh`
> Запрос (2026-07-26): «Добавь контейнер с nginx для reverse proxy, а также скрипт установки, чтобы я мог на
> новом сервере включить скрипт, указать домен и сервис установится и будет доступен по указанному домену
> (сертификат получается и обновляется автоматически)». Файлы: `proxy/*`, `install.sh`, `docs/deploy.md`,
> `.gitattributes`, правки `docker-compose.yml` + `.env.example`.

### 14a. Архитектура
- **`proxy` (nginx:1.27-alpine) — ЕДИНСТВЕННАЯ публичная точка входа**, всегда в стеке, публикует `80` + `443`.
  `frontend` больше **НЕ** публикует порт (`ports: 80:80` → `expose: 80`) — его отдаёт proxy по имени контейнера.
  Без `PROXY_DOMAIN` proxy отдаёт панель по HTTP на :80 (ровно то, что раньше делал frontend) → существующие
  развёртывания не ломаются.
- **Маршруты (`proxy/snippets/app.conf`):** `/api/` и `/ws/` идут **НАПРЯМУЮ в backend**, минуя внутренний nginx
  фронтенда — у того нет ни лимита загрузки, ни streaming-твиков, ни таймаутов, поэтому вся настройка живёт в
  ОДНОМ месте. `/` → `frontend:80` (SPA). `/healthz` → 200 локально (healthcheck не зависит от upstream'ов).
  **`/internal/` → 404** (там ungated `/internal/agg-subs` — наружу не отдаём никогда).
- **⚠️ `client_max_body_size 64m`** — дефолтный 1 МБ ронял загрузки библиотеки (25 МБ/файл) и импорт архивов.
  Латентный баг существовавшего `frontend/nginx.conf`; в проксируемом пути он больше не участвует.
- **⚠️ `proxy_buffering off` + `proxy_read_timeout`** — ndjson-стрим ИИ-чата и медленные ручки (анализ подписки
  с traceroute, node-stats по SSH) → 600с для `/api/`, 3600с + upgrade-заголовки для `/ws/` (логи деплоя текут
  минутами).
- **⚠️ Upstream'ы через ПЕРЕМЕННЫЕ + `resolver 127.0.0.11`** (`set $up_backend "backend:8000"`). С литеральным
  `proxy_pass http://frontend:80` nginx резолвит имя на ЗАГРУЗКЕ конфига и **отказывается стартовать**, если оно
  не резолвится → упавший/пересобираемый frontend утащил бы всю точку входа (и TLS) за собой. С переменной
  lookup отложен до запроса → просто 502 на этом маршруте. **Проверено:** контейнер жив, `/` отдаёт 502.

### 14b. Chicken-and-egg сертификата (главный квирк)
- **nginx с `listen 443 ssl` и НЕсуществующим файлом серта не стартует вообще** → первый
  `certbot --webroot` (которому нужен живой nginx на :80) не может произойти никогда. Обходим тем, что
  **шаблон выбирается в РАНТАЙМЕ** (`proxy/entrypoint.sh`): нет серта → `app-http.conf.template` (панель по HTTP
  + ACME-путь), есть серт → `app-tls.conf.template` (редирект + TLS).
- **`envsubst '${PROXY_DOMAIN}'` — allow-list ОБЯЗАТЕЛЕН**, иначе envsubst съест собственные переменные nginx
  (`$host`, `$request_uri`, `$http_upgrade`) и молча выдаст битый конфиг (тот же урок, что в §6 про подстановку
  шаблонов). Проверено: 7 nginx-переменных в app.conf целы.
- **⚠️ ACME-путь (`acme.conf`) остаётся на HTTP и ПОСЛЕ появления редиректа** — LE валидирует renewal по http://,
  и глобальный `return 301` убил бы все продления через 60 дней. Проверено: `/.well-known/acme-challenge/` не
  редиректится.
- **Watcher в entrypoint** (каждые 6ч, `PROXY_RELOAD_INTERVAL` для тестов): ре-рендер → при изменении `nginx -t`
  + `reload` (при невалидном — откат на бэкап). Так переход HTTP→TLS после первой выдачи и все продления
  подхватываются САМИ. Проверено вживую: подкинул серт → «config changed — reloading» → https отвечает 200 без
  рестарта.
- **⚠️ `add_header` в location ЗАМЕНЯЕТ весь унаследованный набор** — из-за `add_header Content-Type` в
  `/healthz` пропадал HSTS с server-уровня (**воспроизведено**). Лечение: `default_type text/plain`. Помнить при
  добавлении любого location'а с `add_header`.
- `certbot` (образ НЕ пиннится намеренно — ACME-клиент обязан догонять изменения CA) крутит
  `certbot renew` каждые 12ч; сам certbot ничего не делает, пока до истечения >30 дней. ПЕРВУЮ выдачу делает
  `install.sh` через `docker compose run --rm --entrypoint certbot certbot certonly …`.
- Тома: `node-letsencrypt` (серты — **не терять**, иначе повторная выдача против rate-limit) и
  `node-acme-webroot` (challenge-файлы, общий с proxy).

### 14c. `install.sh` — установщик И management-CLI
- **Установка ОДНОЙ командой** (репо публичный, дефолтная ветка `main`):
  `curl -fsSL https://raw.githubusercontent.com/vitabled/node-assistant/main/install.sh | sudo bash -s -- --domain … --email …`.
  Работает потому, что: пайп → `BASH_SOURCE[0]` не файл → `SELF=""` (гард обязателен под `set -u`) → нет чекаута
  рядом → клон в `/opt/node-assistant`; промпты читают `/dev/tty`, а не stdin (иначе `read` съел бы остаток
  скрипта). **Проверено в debian:12-slim:** пайп с `--help`, пайп с management-командой («не установлено»), и
  реальный пайп-install доходит до установки prerequisites.
- **Команды:** `install` (дефолт) · `status` · `check-updates` · `update` · `set-domain <fqdn>` · `set-ports`.
  Диспетчер разбирает первый аргумент; неизвестная команда → внятная ошибка (не молчаливый install).
  `set-domain` берёт домен позиционно.
- **`check-updates` возвращает exit 10**, если апдейт есть (0 = актуально) — чтобы вешать на cron:
  `node-assistant check-updates || node-assistant update -y`. `update`: `fetch` → `merge --ff-only` →
  `build` → `up -d` → `reload_proxy` → пере-создать шорткат. **Отказывается работать при грязном дереве**
  (не перезатирает локальные правки). ⚠️ В панели есть СВОЙ апдейтер (§13e, DooD-sidecar) — оба работают с тем же
  чекаутом, конфликта нет, но не запускать одновременно.
- **Шорткат `/usr/local/bin/node-assistant`** создаётся при первой успешной установке — **симлинк**, а не копия,
  чтобы `update` не оставлял позади устаревший CLI. ⚠️ Поэтому `SELF` резолвится через **`readlink -f`**: без этого
  все management-команды искали бы репо в `/usr/local/bin`. **Проверено на Linux:** запуск шортката из `/` находит
  APP_DIR. Существующий НЕ-симлинк по этому пути не трогается (предупреждение).
- **Порты веб-входа** (`HTTP_PORT`/`HTTPS_PORT` в `.env` → `ports: "${HTTP_PORT:-80}:80"`): установщик спрашивает
  их с дефолтами 80/443 (Enter = пропустить; `-y`/`--default-ports`/явные флаги пропуск без вопроса), меняются
  `set-ports`. Это ЕДИНСТВЕННЫЕ порты, которые могут конфликтовать — остальные живут в bridge-сети.
  **⚠️ Let's Encrypt http-01 валидируется по ПУБЛИЧНОМУ :80** → при `HTTP_PORT != 80` выдача невозможна:
  `warn_http01_port` предупреждает и ПРОПУСКАЕТ выдачу (не падает).
- **⚠️ Нестандартный HTTPS-порт ломал бы редирект:** `return 301 https://$host$request_uri` увёл бы браузер на
  :443, где никого нет. Поэтому шаблон получил `${REDIRECT_HOST}`, а entrypoint подставляет `$host` или
  `$host:8443` (`PROXY_HTTPS_PORT` из compose). `$host` остаётся литералом — envsubst не ресканирует подставленное
  значение. **Проверено контейнерами:** 443 → `https://127.0.0.1/`, 8443 → `https://127.0.0.1:8443/`.
- **Идемпотентность:** `ENCRYPTION_KEY`/`AGG_TOKEN` НЕ перегенерируются (иначе инвалидация сессий и Fernet-волтов);
  домен/порты/e-mail читаются обратно из `.env`, так что `sudo node-assistant` без флагов = ремонт/обновление
  конфигурации.
- **⚠️ `.gitattributes` (`*.sh text eol=lf`, `.env.example text eol=lf`)** — репо пишется на Windows
  (`core.autocrlf=true`), исполняется на Linux: CRLF в `entrypoint.sh` → `/entrypoint.sh: not found`,
  в `install.sh` → `bad interpreter: /bin/sh^M`, в `.env` → `
` в КАЖДОМ значении.
- **`README.md`** (корень) — публичная точка входа: команда установки одной строкой первым делом, затем
  управление через `node-assistant`, возможности, требования, порты (+ оговорка про http-01 на :80), схема
  стека, безопасность (`ENCRYPTION_KEY`, первый аккаунт наследует данные) и **лицензии вендоренного**
  (`nodeflow/` и `frontend/public/mihomo/` — без лицензии в апстриме; `mcp/` — MIT по package.json; у самого
  проекта файла лицензии нет). ⚠️ raw-ссылка на `install.sh` проверена вживую: HTTP 200, 0 CR-байт, и
  `curl … | bash -s -- --help` реально исполняется в debian-контейнере. Том данных зовётся
  `node-installer_node-data` (project-prefixed), НЕ `node-data` — в доках писать полное имя.
- **Проверка:** `bash -n` + `shellcheck` чисто; `docker compose config` с `HTTP_PORT=8080 HTTPS_PORT=8443` даёт
  правильные published-порты и `PROXY_HTTPS_PORT`; оба режима прокси и оба варианта редиректа — реальными
  контейнерами; шорткат и пайп-режим — в debian-контейнере. ⚠️ Полный install на чистом VPS не гонялся (нет
  тестового сервера) — проверены синтаксис, линт, диспетчер, резолв путей и все компоненты по отдельности.

### 14d. Что нашёл adversarial-ревью (12 агентов, 4 линзы) — НЕ регрессировать
- **⚠️ `/healthz` обязан отвечать на :80 И в TLS-режиме** (`snippets/healthz.conf`, включён в port-80 блок ОБОИХ
  шаблонов + в :443). Иначе healthcheck `wget http://127.0.0.1/healthz` ловит `return 301` → идёт на
  `https://127.0.0.1` → не может проверить серт для IP → контейнер **навсегда `unhealthy`** (воспроизведено на
  реальном образе). Тот же 301 делал no-op'ом readiness-гейты `install.sh` (у `curl -f` 3xx не ошибка).
  Поэтому `/healthz` НЕ в `app.conf` — иначе дубль `location` в http-шаблоне.
- **⚠️ `Connection` для WS — через `map`, не хардкод** (`http.d/00-upgrade-map.conf`, копируется в `conf.d/` =
  http-контекст; `map` внутри `server{}` невозможен). Иначе обычный GET на `/ws/` уходит в backend с
  бессмысленным `Connection: upgrade`.
- **⚠️ `listen [::]` роняет nginx на хостах с `ipv6.disable=1`** (`socket() [::]:80 failed (97)`). `entrypoint.sh`
  снимает v6-строки `sed`'ом, если нет `/proc/net/if_inet6` (вместо двух лишних шаблонов). Проверено: с IPv6
  строки остаются.
- **⚠️ `depends_on: condition: service_started`, НЕ `service_healthy`** — иначе битый backend не даёт стартовать
  точке входа и **блокирует renewal серта**, превращая починимый сбой в просроченный сертификат. Ленивый резолв
  upstream'ов (переменные + resolver) делает health-гейт ненужным.
- **⚠️ `Settings.Config.extra = "ignore"`** (`backend/app/config.py`): pydantic-settings по умолчанию
  **forbid** и кормит моделью ВСЕ ключи `.env`, а `.env` общий (AGG_TOKEN/TASK_STORE/PROXY_DOMAIN/ACME_EMAIL для
  compose и прокси) → `extra_forbidden`. Контейнеров не касалось (compose отдаёт env-переменные, `.env` в образ не
  копируется — `COPY app/ ./app/`), но запуск бэкенда из корня репо падал. Ловушка была и ДО этой волны (AGG_TOKEN).
- **⚠️ Exec-бит не выражается через `.gitattributes`** и `core.fileMode=false` в этом репо → новый файл
  коммитится как `100644`, и документированный `sudo ./install.sh` падает с `Permission denied` на свежем клоне.
  Лечение: `git update-index --chmod=+x install.sh` (проверять `git ls-files -s` → `100755`).
- **⚠️ Staging-серт на диске не отличим от настоящего по имени файла** → после отладочного прогона с `--staging`
  обычный ре-ран молча переиспользовал недоверенный серт. `install.sh::cert_is_staging` смотрит issuer через
  `openssl` (есть в образе certbot) и форсит перевыдачу.
- **⚠️ CRLF в `.env`** сажает `\r` в КАЖДОЕ значение (в `server_name`, в путь к серту) → `.gitattributes`
  пиннит `.env.example` на LF, а `install.sh` дополнительно копирует через `tr -d '\r'`.
- **Отклонено ревью (не баг):** «open redirect через `$host`» — редирект host-СОХРАНЯЮЩИЙ (жертвы нет);
  «depends_on ломает ребут» — `depends_on` это конструкт CLI, демон при ребуте его не читает.
- **⚠️ Покрытие ревью НЕполное:** 3 из 12 агентов упали с API-ошибками (линзы **security** и **installer**
  целиком, + верификатор uppercase-домена). Линза security не отработала — при следующем заходе прогнать её
  отдельно.

## 15. Волна 9 — «Хранилище» секретов · SSH по ключу · группа Cloudflare · адаптеры хостингов
> Планы `docs/superpowers/plans/2026-07-27-wave9-{a,b,c}-*.md` + зонтичный индекс. Решения из 2 раундов Q&A:
> волт = менеджер **с автоподстановкой**; CF = **зеркало биллинга + Домены**; покупка домена — **настоящая**;
> адаптеры — **баланс + список услуг**; креды провайдеров — **в едином Хранилище**; обновление балансов —
> по открытию + кнопка + **фоновый луп**.

### 15a. Хранилище (`Справка → Хранилище`)
- **`services/vault_store.py` — JSON, НЕ SQLite:** `accounts/<id>/vault.json` (`{"entries":[…]}`), атомарная
  запись + `threading.Lock` (идиома `hostings_store`). Причина выбора JSON: `export_service` умеет только
  JSON-сторы, объём — десятки записей, схемы/миграций нет.
- Запись: `{id,name,kind,resource,username,note,tags[],fields_enc,created_at,updated_at,revealed_at}`.
  `kind ∈ api_key|ssh_password|ssh_key|login|provider_creds|note`. **Секрет — ОДИН Fernet-блоб над JSON-объектом
  полей** (не строка): кредам провайдеров нужно 2-5 полей (Oracle — 5, OpenStack — 5, Beget — 2), и запись на
  поле была бы мусором. Ключ Fernet — общий `sha256(encryption_key)`.
- Лимиты: 500 записей, секрет ≤64 KiB, name ≤80, resource ≤200, ≤10 тегов по ≤24 симв.
- **`_decode_fields` различает три состояния:** `{}` = секрета нет, `None` = не расшифровывается (сменили
  `ENCRYPTION_KEY`) → `list_entries` помечает `broken:true` и НЕ бросает (один битый секрет не должен ломать
  страницу).
- **`api/vault.py`:** `GET/POST /api/vault`, `PUT/DELETE /{id}`, **`POST /{id}/reveal`** (именно POST: id в URL
  уходит в access-логи nginx и историю браузера, а GET подвержен префетчу), `GET /{id}/download` (только
  `ssh_key`, `octet-stream`+`attachment`+`nosniff`), `GET /schemas` (объявлен ДО `/{entry_id}`, иначе перехват).
  **Reveal записи без секрета → 404 с текстом «введите его заново»**, а не пустая панель (так выглядит запись,
  вернувшаяся из импорта).
- **Правило размещения секретов в проекте:** один секрет на модуль → `AppSettings.<модуль>.<имя>_enc`
  (mcp/ai/haproxy/cloudflare/auto_backup); много однотипных пользовательских секретов → **Хранилище**.
- ⚠️ **Смена посыла:** до этой волны SSH-креды на сервере не хранились НИКОГДА. Теперь — хранятся, **если
  пользователь сам их туда положил** (осознанный выбор, module-scoped override как §4c).

### 15b. Экспорт секретов — исправленный дефект (важно)
`export_service._strip_secrets` понимал **только `settings.json`**, и внутри — только секции `mcp`/`ai`. Поэтому
добавление `vault.json` в экспорт отправило бы наружу шифротексты всех паролей/ключей, а токены
`haproxy.admin_token_enc` / `auto_backup.bot_token_enc` / `cloudflare.api_token_enc` **не стрипались ещё с
Волны 8**. Теперь: (а) `*_enc` вычищается **сквозным проходом по всем секциям** (перечисление секций молча
отставало от новых модулей), (б) у `vault.json` зануляется `fields_enc` у каждой записи — **инвентарь
сохраняется** (видно, какие секреты завести заново), шифротекст не уезжает. Регрессия:
`test_export_io.py::test_vault_ciphertext_never_leaves_in_an_export` + `test_settings_enc_sections_are_all_swept`.

### 15c. SSH-авторизация по приватному ключу
- До волны проект умел **только пароль** (`asyncssh.connect(password=…)`, `ssh_password` обязателен).
- `SSHSession.__init__(host, port, username, password="", private_key="", key_passphrase="")` — `password`
  остался **4-м позиционным** (его так передают ~20 call-site'ов). Ключ импортируется в `__init__`
  (`_import_key`), чтобы негодный ключ падал ДО сети; текст ошибки — фиксированная русская фраза **без
  материала ключа**.
- **`models/ssh_creds.py::SshCreds`** — миксин (`ip/ssh_user/ssh_password/ssh_key_ref`) + `model_validator`:
  ни пароля, ни ключа → 422 с внятным текстом (раньше это давал `Field(..., min_length=1)`).
- **`services/ssh_auth.py::resolve(req, account_id=None)`** → `{"password":…}` либо
  `{"private_key":…, "key_passphrase":…}`. Duck-typing (`getattr`), а не общий базовый класс: ~20 моделей
  объявлены инлайн в своих роутерах. Ключ читается из волта **на каждый resolve** (отзыв записи действует сразу).
  Любая проблема с ref → один и тот же 400 (какая именно — не подсказываем: это зондировало бы чужие id).
- **Переведены (Ф3):** `services/pipeline.py` (резолв ОДИН раз, дальше `creds` передаётся вниз — в т.ч. в
  `step_ssh_dualport_verify`/`_try_ssh_connect`, чтобы реконнекты после перезагрузки не дёргали волт),
  `api/node_ops.py`, `api/stats.py`. **НЕ переведены (Ф4/Ф5, осознанно отложено):** `panel_deploy`,
  `panel_pipeline`, `panel_metrics`, `panel_sync`, `backup`, `subpages`, `replace_domain`, `certs`, `certwarden`,
  `netbird`, `migrate`, `speedtest`, `testservers`, `xray_checker` — там по-прежнему только пароль.
- ⚠️ **Приватный ключ НЕ подставляется значением в форму** (отклонение от «автоподстановки» с обоснованием):
  `savedForm` карточки деплоя целиком персистится в localStorage → ключ лёг бы туда навсегда. В форму едет
  только `ssh_key_ref`. Пароли/API-ключи подставляются значением. Гарантия покрыта тестом
  `VaultPicker.test.tsx` («picking an SSH KEY … never calls reveal»).

### 15d. Группа разделов «Cloudflare»
- `CloudflareConfig{enabled, account_id, api_token_enc(Fernet), default_contact}` на `AppSettings`.
  ⚠️ `deploy_defaults.cloudflare_api_key` — **другой** токен (DNS-edit для деплоя), не пересекается.
- `services/cf_client.py` — конверт `{result,success,errors}`, `CfError`, `_redact` токена во всех сообщениях;
  хост фиксированный → SSRF-гард не нужен (в отличие от `nodeflow_client`).
- `api/cloudflare.py`: `GET/POST /config`, `POST /test`, `GET /accounts`, `GET /billing/summary` (**упавшая
  под-ручка не роняет ответ — её имя уходит в `degraded`**, это частичные права токена), `/subscriptions`,
  `/usage`, `/zones`; кэш `_CACHE[(account,key)]` TTL 15 мин + `?refresh=1`.
- **Домены:** `GET /domains`, `POST /domains/{search,check}`, `PATCH /domains/{name}`,
  **`POST /domains/register`** — гейты по порядку: `confirm` → наличие способа оплаты (`billing/profile`) →
  свежий `domain_check` → **сверка `expected_price/expected_currency` (расхождение >0.01 → 409)** → цена
  неизвестна → **отказ (fail closed)**; `auto_renew` по умолчанию false (CF трактует true как разрешение
  списывать при продлении). Валидация FQDN `_DOMAIN_RE.fullmatch`.
- ⚠️ **Истории платежей у CF в публичном API НЕТ** (user-level `/user/billing/history` отсутствует, account-level
  аналога не заявлено). Раздел «Платежи» показывает расход `paygo-usage` + предстоящие списания подписок и
  честную плашку со ссылкой в dash.cloudflare.com. Не выдумывать ledger.
- ⚠️ Формы ответов `billing/profile`, `paygo-usage`, `domain-check` **на живом аккаунте не снимались** — парсеры
  защитные (`_profile_view`, `_norm_check`, `normalizeUsage` читают несколько вариантов написания ключей и
  показывают сырой JSON, если формат неизвестен). Ф0-разведка (`scripts/probe_cloudflare.py`) не проводилась.
- Фронт: группа «Cloudflare» в сайдбаре (Обзор/Подписки/Использование/Платежи/Домены), подключение — вкладка
  **Настройки → «Cloudflare»** (как HAProxy), гейт `cloudflare/gate.tsx`.

### 15e. Адаптеры API хостинг-провайдеров
- `services/hosting_providers/`: `base.py` (`ProviderAdapter` + `CredField/Balance/ServiceItem` + `redact`
  (маскирует и percent-encoded форму — Beget шлёт креды в query!) + `map_http_error`; **ни один метод не
  бросает**), `registry.py` (**защищённые импорты** — битый/отсутствующий адаптер пропускается с warning, не
  роняет реестр; отдаёт инстансы; дубликат `KIND` → первый выигрывает), 8 kind:

| kind | авторизация | баланс | услуги | платежи |
|---|---|---|---|---|
| `ruvds` | Bearer | ✅ `/v2/balance` | ✅ `/v2/servers` | ✅ `/v2/payments` (direction 1=приход/2=списание) |
| `beget` | login+password (в query!) | ✅ `getAccountInfo.user_balance` (двойной конверт) | — | — |
| `veesp` | Basic | ✅ `/balance` | — (ручка не задокументирована) | ✅ `/invoice` |
| `regru_cloudvps` | Bearer | ❌ нет в API | ✅ `/v1/reglets` | — |
| `regru_account` | username+password, **только POST form-data** | ✅ `user/get_balance` | — | — |
| `yandex` | SA-ключ → JWT **PS256** → IAM-токен (кэш 55 мин) | ✅ `billingAccounts` (balance — СТРОКА) | ✅ Compute | ✅ |
| `openstack` (VK/Procloud) | Keystone v3 → `X-Subject-Token`, эндпоинты из service catalog | ❌ публичного API нет | ✅ Nova | — |
| `oracle` | подпись draft-cavage RSA-SHA256, keyId `tenancy/user/fingerprint` | ❌ у OCI нет «баланса» | ✅ Compute | ✅ usageapi |

  Новых pip-зависимостей нет: JWT PS256 и подпись OCI собраны на `cryptography` (уже в проекте).
  ⚠️ `openstack.auth_url` **пользовательский** → `net_guard.is_safe_url` и при verify, и перед каждым запросом.
  ⚠️ Oracle: расхождение часов >5 минут → 401.
- **Проводка в инфра-биллинг:** `provider_meta` получила **идемпотентными `ALTER TABLE`** колонки
  `adapter_kind`, `vault_entry_id`, `balance_synced_at`, `last_error` (приём из `metrics_store`/`server_monitor`).
  `GET /api/infra-billing/adapters`, `POST /providers/{uuid}/sync`, `POST /providers/{uuid}/import-services`
  (импорт услуг — **только по кнопке**: живой список у провайдера, локальные `services` — учёт пользователя).
  Синхронизация пишет в **существующее поле `provider_meta.balance`** → total/burn-rate/days-left и
  уведомление о низком балансе заработали без изменений в дешборде.
- **`services/provider_sync.py`:** `sync_one()` (ручка) + `loop()` (гейт `worker_lease.MONITORING`, per-account
  explicit `account_id`, интервал из существующей billing-настройки `refresh_interval` ≥300 c). Провайдер с
  `auth_error` уходит на **экспоненциальный backoff** (900 c → 6 ч): неверные креды сами не починятся, а долбить
  auth чужого API — путь к бану. **Тумблер `auto_sync` в billing_settings, по умолчанию ВЫКЛ** — фоновые
  обращения чужими кредами должны включаться осознанно.
- Фронт `infra/InfraProviders.tsx`: селектор адаптера + выбор записи Хранилища через `VaultPicker` с
  **`pickRefOnly`** (значение секрета браузеру не нужно — креды читает бэкенд), кнопки «Синхронизировать» и
  «Импортировать услуги», строка «API: kind · обновлён N мин назад» / «баланс вручную» / `lastError`.
  ⚠️ `/sync` отвечает **200 даже при сбое** (адаптеры не бросают) — UI решает по флагу `ok`, не по статусу.

### 15f. Верификация Волны 9
- Backend: **`python -m pytest` → 955 passed, 1 failed** — единственный фейл `test_haproxy.py::
  test_deploy_reports_images_not_built` **пре-существующий и environment-sensitive** (на этой машине образы
  NodeFlow собраны → `started=True`); в Волне 8 он падал так же. База была 869 → +86 тестов
  (`test_vault` 11, `test_hosting_providers` 16, `test_hosting_providers_heavy`, `test_cloudflare` 21,
  `test_ssh_auth` 6, `test_export_io` +2).
- Frontend: `tsc --noEmit` чисто (Docker `ni-frontend-test`); vitest `contrast.test.ts` (гейт хардкод-цветов)
  + `DeployForm.test.tsx` + `Settings.test.tsx` = 23 зелёных, `VaultPicker.test.tsx` = 4 зелёных.
- **Не проверено вживую:** ни один адаптер хостинга (нет кредов), покупка домена (реальные деньги), read-ручки
  CF (нет аккаунта с Registrar). В этих местах парсеры защитные, а тесты — на записанных фикстурах.

## 16. Волна 10 — общее хранилище медиа · Библиотека в стиле Obsidian · экран API-токенов на адаптеры
> Запрос (2026-07-27): медиа в формах хостинга (клик + drag-n-drop + превью с раскрытием); Библиотека —
> «скопируй Obsidian вместе с логикой вставки изображений посреди текста»; жалоба: в «API токенах» видны
> только изначальные хостинги, новых адаптеров Волны 9 там нет. Решения Q&A: **общее** хранилище файлов на
> всю панель; Obsidian-уровень = **заметки + папки + wiki-ссылки**; markdown через **marked + DOMPurify**;
> экран токенов **переводится на адаптеры**.

### 16a. `services/media_store.py` — один стор медиа на все разделы
- Per-account `accounts/<id>/media/` (`index.json` + `files/`), атомарная запись + `threading.Lock`
  (идиома `library_store`/`hostings_store`). Лимиты: 2000 файлов, 15 МБ на файл.
- **Mime-allow-list разделён НАДВОЕ, и это главное решение модуля:**
  - `INLINE_MIME` — только растровые (png/jpeg/gif/webp/avif). Отдаются со своим типом → рендерятся в
    карточке хостинга и внутри заметки.
  - `ATTACH_MIME` — svg/pdf/mp4/webm/txt. Отдаются **`application/octet-stream` + `attachment`**.
  ⚠️ **SVG намеренно НЕ inline**: это XML-документ, который может нести `<script>`, то есть inline-выдача
  SVG = хранимая XSS против панели. Тест `test_svg_is_never_served_inline` это фиксирует.
- **Расширение файла на диске берётся из РАЗРЕШЁННОГО mime, а не из имени загрузки** — иначе `shot.png.html`
  лёг бы на диск как `.html` (тест `test_stored_extension_comes_from_the_mime_not_the_name`).
- `api/media.py`: `GET /api/media`, `POST /api/media/upload`, `GET|DELETE /api/media/{id}`.
  У inline-ответа `X-Content-Type-Options: nosniff` + `Content-Security-Policy: **sandbox**`.
  ⚠️ Только `sandbox`, БЕЗ `default-src 'none'`: CSP не применяется к подресурсу `<img>`, зато применилась бы
  к документу при открытии картинки в новой вкладке — и `default-src 'none'` заблокировал бы саму картинку.
- Потребители хранят **только id**: `HostingBody.media: list[str]`, в заметке — `![[media-id]]`.
  Битый id не ошибка: фронт (`fetchMediaMeta`) молча отбрасывает то, чего нет. Удаление хостинга файл НЕ
  удаляет — он общий и может быть нужен заметке.
- **В экспорт аккаунта медиа не входят** (как и файлы Библиотеки): `export_service` работает с JSON-сторами,
  бинарники — отдельная задача.

### 16b. Библиотека — папки и wiki-ссылки (`library_store`)
- Заметка получила `folder` («Инфра/Провайдеры») и `updated_at`. **Папка не сущность, а строка на заметке**
  — та же модель, что в Obsidian; поэтому есть `norm_folder` (схлопывает `//`, режет `.`/`..`, глубина ≤8),
  а не таблица папок. Обратные слэши **отвергаются**, а не переводятся в `/`: значение не путь ФС, а
  молчаливый приём обоих разделителей развёл бы `A\B` и `A/B` в две разные папки дерева.
- Синтаксис: `[[Имя]]` / `[[Имя|алиас]]` / `[[Имя#якорь]]` — ссылка; `![[media-id]]` — вставка медиа.
  ⚠️ Регексп ссылки несёт **negative lookbehind `(?<!!)`**, иначе `![[id]]` разбирался бы и как встраивание,
  и как ссылка, и каждая вставленная картинка светилась бы «битой ссылкой» (тест
  `test_embed_syntax_is_not_mistaken_for_a_link`).
- `graph()` резолвит ссылки **по имени на чтение** (пользователь пишет имя, не id) и отдаёт
  `{id: {out, in, unresolved}}`. Несуществующая цель не выбрасывается, а попадает в `unresolved` — так
  находят опечатку, и так же делает Obsidian.
- **Переименование заметки чинит ссылки на неё** (`_retarget_links`, сохраняет `|алиас` и `#якорь`) — поведение
  Obsidian по умолчанию; без этого любое переименование тихо осиротило бы все `[[…]]`.
- `rename_folder(src, dst)` двигает поддерево (`dst=""` → в корень). Роуты: `GET /api/library/graph`,
  `POST /api/library/folders/rename`; `NoteBody` получил `folder`.
- `list_items` подставляет `folder: ""`/`updated_at` заметкам, созданным до этой волны, чтобы дерево не
  разбиралось с `undefined`.

### 16c. Фронтенд: общий компонент медиа
- **`components/common/MediaDrop.tsx`** — область «клик + drag-n-drop», превью-сетка, лайтбокс, открепление.
  Экспортирует `MediaItem`, `uploadMedia`, `fetchMediaMeta`, `fmtSize`, `<Lightbox/>`, `<MediaDrop/>` и —
  главное — **`useMediaObjectUrl` / `<MediaImg/>` / `downloadMedia`**.
- ⚠️ **Медиа НЕЛЬЗЯ грузить через `src`/`href`.** Панель авторизуется Bearer-токеном, который навешивает
  глобальный перехват `window.fetch` (`auth/apiClient.ts`); запрос картинки браузером через `fetch` НЕ идёт,
  заголовка не несёт и получает **401** — все превью были бы битыми иконками. Поэтому байты берутся
  авторизованным `fetch` и отдаются в DOM как object-URL (с `revokeObjectURL` в cleanup). Это же правило —
  причина, по которой вложения скачиваются функцией, а не ссылкой. Тот же приём в `library/markdown.ts`:
  встраивание отдаёт `<img data-media="id">` **без `src`**, а `MarkdownView` доставляет blob.
  Ловушка общая для всего проекта: любой новый бинарный эндпоинт под `require_account` требует того же.
- Открепление медиа снимает id с записи, но **файл в общем сторе не удаляет** — на него может ссылаться
  заметка или другой хостинг.

### 16d. Хостинги: вложения
`hostings/HostingsCatalog.tsx` — `<MediaDrop/>` в модалке редактора; `MediaStrip` на карточке (56px, максимум 4
+ «+N») и в полном просмотре (72px), клик → `<Lightbox/>`. Метаданные всех карточек резолвятся **одним**
запросом на странице и передаются в детали пропом `meta` (открытие карточки не стоит дополнительных запросов).
Клики по превью — со `stopPropagation`, иначе открывалась бы карточка под ними. Секция в деталях гейтится на
`(h.media||[]).some(id => meta.has(id))`: удалённый из стора файл не оставляет пустой заголовок.
⚠️ Поиск по имени файла НЕ работает (`search.ts` не трогали): у хостинга лежат только id, имена — в индексе медиа.

### 16e. Библиотека в стиле Obsidian
- `components/library/`: `api.ts`, `markdown.ts`, `MarkdownView.tsx`, `NoteEditor.tsx`, `NoteTree.tsx`,
  `Backlinks.tsx` + `markdown.test.ts` (13 тестов). `Library.tsx` — раскладка: дерево слева, редактор справа,
  обратные ссылки под ним, файлы библиотеки — отдельной секцией (загрузка/скачивание/удаление сохранены).
- **Конвейер рендера:** вики-синтаксис → HTML → `marked` → **`DOMPurify.sanitize`**. Санитайз обязателен:
  markdown пропускает сырой HTML, без него заметка = XSS. `ADD_ATTR` сохраняет `data-note`/`data-media`,
  по которым работают клики. Замена вики-синтаксиса идёт ДО marked и **пропускает содержимое code-fence и
  inline-code** (иначе пример `[[…]]` в документации превращался бы в ссылку).
- **Вставка медиа посреди текста — три пути, все в позицию курсора** (с заменой выделения, фокус и каретка
  после вставленного): drag-n-drop на textarea, вставка из буфера (`clipboardData.files`), кнопка тулбара.
- Автосохранение с дебаунсом 800 мс + **флаш при размонтировании** (переключение заметки не теряет правки),
  индикатор состояния. Клик по `[[ссылке]]` открывает заметку; несуществующая — предлагает создать.
- ⚠️ В `MarkdownView.tsx` агент записал в исходник **настоящие управляющие байты** `\x00`/`\x01` (хотел escape
  для разделителя memo-ключа) — файл становился «бинарным» для git/grep/diff. Заменено на escape-форму.
  Проверять при правках новых файлов: `grep -c $'\\x00'` или «Binary file … matches» в выводе grep.

### 16f. «API токены» → доступы к API хостингов
`infra/InfraApiTokens.tsx` переписан: основной список — записи Хранилища с `kind="provider_creds"`
(`resource` = kind адаптера), форма строится **динамически из `GET /api/infra-billing/adapters`**, поэтому все 8
адаптеров Волны 9 заводятся именно там, где пользователь их искал. Секретные поля — общий `vault/SecretField`;
при редактировании пустые поля = не менять (PUT без `fields`), а патч несёт только `name`+`resource`, чтобы не
затереть `username/note/tags`. Старые записи `api_tokens` показаны отдельной секцией «устаревший формат»
(только удаление) — переносить их некуда: там одно поле-строка, а адаптерам нужно 2-5. Проверки соединения на
экране нет намеренно: она живёт в «Провайдерах» («Синхронизировать»). Загрузка через `Promise.allSettled` —
сбой `/adapters` не прячет список кредов.

### 16g. Верификация Волны 10
- Backend: `python -m pytest` → **965 passed, 1 failed** (тот же пре-существующий `test_haproxy::
  test_deploy_reports_images_not_built`). База 955 → +10 (`test_media` 9, `test_hostings` +1).
- Frontend: `tsc --noEmit` чисто; vitest 5 файлов / 40 тестов (contrast-гейт, DeployForm, Settings,
  VaultPicker, library/markdown) — все зелёные, число файлов сверено (§11g).
- Новые npm-зависимости: **marked ^18** и **dompurify ^3** (ставились в контейнере — Node на хосте нет).
- **Не проверено вживую:** реальная загрузка файлов и рендер заметок в браузере (нет запущенного стенда) —
  покрыто типами и юнит-тестами рендерера; drag-n-drop/вставка из буфера тестами не покрыты.

## 17. Волна 11 — метрики и фильтр хостингов · дерево заметок · возврат старых хостингов в токены
> Четыре пункта пользователя одним заходом. Два из них — исправление регресса Волны 10.

### 17a. Метрики хостинга
- `models/hostings.py::HostingMetrics{price, quality, loyalty, fairuse, panel, ru_access, fairuse_hidden}` +
  `HostingBody.metrics`. Каждая оценка — `Optional[float]` в **[1.0, 100.0]**, округление до 1 знака.
  `None` = «не оценено» и **не равно нулю** (зафиксировано тестом).
- Валидатор — цепочка `if not 1.0 <= v <= 100.0`: так же отсекаются `NaN`/`±inf` (NaN сравнивается False со
  всем), иначе они попали бы в `hostings.json` невалидным JSON-литералом. Диапазон проверяется по ВХОДЯЩЕМУ
  значению, округление после — поэтому 100.04 отвергается, а не «спасается» округлением.
- ⚠️ **`GET /api/hostings` отдаёт СЫРОЙ JSON из стора** (`list_hostings` не прогоняет его через модель) →
  у карточек, лежащих на диске с прошлых волн, ключа `metrics` в ответе НЕТ вовсе. Фронт обязан читать
  `(h.metrics || {})`; ключ появляется после первого POST/PUT этой карточки. То же правило уже действует
  для `tags`/`asns`/`media`.
- ⚠️ PUT заменяет тело целиком → фронт обязан слать **весь** объект `metrics`, иначе не присланный
  `fairuse_hidden` вернётся в `false`.
- Фронт: `hostings/metrics.ts` (`METRIC_DEFS`, `metricColor`, `avgScore`) — цвет цифр считается
  `hsl(hue …)`, где `hue = (v-1)/99*120` (красный → зелёный). Это **data-ink цвет инлайн-стилем**, гейт
  `theme/contrast.test.ts` его не запрещает (запрещены именованные оттенки Tailwind). Панель «Фильтр и
  сортировка» — клиентская, поверх существующих поиска и фильтра по тегу.

### 17b. Дерево заметок: пустые папки, порядок, перетаскивание
- **Пустая папка — отдельная строка индекса** `{id, kind:"folder", path, created_at}`. Иначе её негде было
  бы держать: папка выводилась из `note.folder` и свежесозданная исчезала при перезагрузке.
- ⚠️ **У folder-строки ключ пути называется `path`**, и это заставило поменять `list_items`: раньше он
  ВСЕГДА вырезал `path` (у file это имя файла на диске). Теперь folder-строка отдаётся целиком, а у
  note/file `path`/`text` по-прежнему вырезаются. Заметкам без `folder`/`order` (созданным раньше)
  подставляются дефолты на чтении.
- `_move_subtree(items, src, dst)` — общий приём для переименования И удаления папки: удаление = перенос
  поддерева в родителя (`path.rsplit('/',1)[0]`) + снятие самой folder-строки. **Заметки внутри не
  удаляются никогда** — молча стирать чужой текст недопустимо.
- `_prune_folders` чистит folder-строки, которые перенос оставил без пути (папку увели в корень) или
  продублировал (поддерево легло поверх существующей папки, напр. rename A→B при живом B). Без этого в
  дереве появились бы два узла с одним путём.
- `reorder(items)` — ОДНА ручка и для перетаскивания в папку, и для смены порядка: `folder=None` означает
  «папку не трогать». Неизвестный id молча пропускается. `updated_at` при перестановке НЕ бампится — это
  не правка содержимого.
- Роуты `POST /api/library/folders` и `POST /api/library/reorder` объявлены **до** путей с `{item_id}`.
- Фронт: перетаскивание — **нативный HTML5 DnD**, без новых библиотек. После дропа уходит ОДИН `/reorder`
  со всем пересчитанным порядком затронутой папки (надёжнее дельт). Поле «Папка» из формы заметки убрано;
  `folder` едет в PUT из `folderRef`, который обновляется на каждый рендер — иначе захваченное значение
  устаревало бы при переносе заметки и возвращало её назад.

### 17c. Возврат старых хостингов в «API токены» (регресс Волны 10)
Жалоба: «я просил добавить новые api хостингов, но теперь не вижу hetzner и остальных». Причина — экран был
**переписан** на список адаптеров вместо того, чтобы дополнить прежний. Теперь селектор — два `optgroup`:
«С API-синхронизацией» (kind-ы из `/api/infra-billing/adapters`) и «Без API (только хранение)»
(`LEGACY_KINDS`: selectel/hetzner/digitalocean/cloudflare/datacheap/generic + любые kind-ы из уже
сохранённых записей, чтобы список не терял используемый вариант). У провайдера без адаптера — одно поле
«Токен/ключ», запись всё равно уходит в Хранилище (`kind=provider_creds`), в строке — пометка «без
API-синхронизации».

### 17d. Верификация
`python -m pytest` → **980 passed** (было 965; +5 метрик, +10 дерева) при снятом известном
пре-существующем `test_haproxy::test_deploy_reports_images_not_built`. `tsc --noEmit` чисто; vitest 4 файла /
45 тестов (metrics 13, search 13, markdown 13, гейт цветов 6). **Не проверено вживую** (нет стенда):
перетаскивание в дереве и загрузка медиа — покрыты типами и юнит-тестами чистых модулей.

## 18. Волна 12 — канал тарифа полосками, расширенный фильтр каталога, произвольные заметки
> Запрос по приложенному эталону `hosting_catalog_1.html` (22 МБ на 444 строки — почти всё в двух гигантских
> строках со встроенными данными; разбирать его надо выборочно, по коротким строкам).

### 18a. Шкала сетевого канала (`hostings/channel.ts` + `ChannelBar.tsx`)
- Из эталона взяты ровно две вещи: **логарифмическая шкала 100 Мбит → 25 Гбит**
  (`pct = clamp(6..100, (log10(max(m,100)) − log10(100)) / (log10(25000) − log10(100)) × 100)`) и **четыре
  ступени цвета** по порогам 150 / 1000 / 10000 Мбит/с. Нижний зажим 6% — чтобы самый узкий канал остался
  полоской, а не точкой.
- ⚠️ **Главное расхождение с эталоном:** там канал — готовое число `t.ch`, а у нас `Tariff.bandwidth` —
  СВОБОДНЫЙ ТЕКСТ (решение Волны 7: провайдер пишет порт, гарантию и лимит трафика одной строкой — «1 Гбит/с,
  20 ТБ», «10G unmetered»). Менять модель нельзя, данные уже введены → написан парсер `parseChannel(text)`.
- Парсер: транслитерация кириллицы целиком (не только буквенных единиц), десятичная запятая чинится **только
  между цифрами** (иначе «10 Гбит/с, 100 ТБ» склеилось бы в одно число), затем скан пар «число + единица».
  ⚠️ **Объём трафика — не скорость:** «20 ТБ», «100 GB» пропускаются и поиск идёт дальше по строке, поэтому
  «20 ТБ, 1 Гбит/с» даёт 1000. Голое число без единицы → `null` (одинаково похоже на мегабиты и на гигабайты;
  показать исходный текст честнее, чем угадать). «Гб/с» считается гигабитами — но только при явном «в
  секунду», именно оно отличает запись от объёма «20 ГБ».
- Цвет — токены `var(--viz-N)` (их подкручивает неон-скин), не свои оттенки. Тест `channel.test.ts` (29):
  все форматы, ловушка «трафик ≠ скорость», монотонность и зажимы шкалы, границы ступеней.

### 18b. Произвольные заметки и признак API
- `NoteField{topic, text}` + `HostingBody.note_fields` (≤30) — заметки хостинга с темой; `Tariff.note` (≤2000)
  — заметка тарифа **без темы** (тариф сам по себе и есть тема); `HostingBody.has_api: Optional[bool]`
  (True/False/None — «неизвестно» обязано отличаться от «нет»).
- ⚠️ **Нормализация topic и text намеренно разная:** `topic` — однострочный ярлык, схлопывается как тег
  (`" ".join(split())`); `text` — многострочная заметка, только `strip()` по краям, **внутренние переводы
  строк сохраняются**. Зафиксировано тестом.
- Пустая пара topic+text отбрасывается валидатором **на списке** (`_prune_note_fields` на `HostingBody`), а не
  внутри `NoteField`: запись не может выкинуть саму себя. Порядок валидации pydantic (сначала элементы, потом
  список) означает, что строка из пробелов приходит в список уже пустой и корректно отсеивается.
- Старое поле `HostingBody.notes` (одна строка) НЕ мигрировали — старые карточки продолжают его показывать
  отдельным абзацем, фильтр «с заметками» смотрит только на `note_fields`.
- ⚠️ `has_api` — справочная пометка каталога, она **не** связана с `provider_meta.adapter_kind` из
  инфра-биллинга (§15e) и привязки к адаптеру не создаёт.

### 18c. Расширенный фильтр каталога
- Панель Волны 11 дополнена (не переписана): мультивыбор тегов, порог по каналу, «с вложениями», «есть API»,
  «с тарифами», «с заметками». Пороги по метрикам не тронуты.
- Семантика мультивыбора тегов — **И** (карточка должна иметь ВСЕ выбранные); клик по тегу на карточке
  тоглит его в наборе, поэтому прежний сценарий «кликнул тег → увидел его хостинги» сохранён.
- Фильтр по каналу берёт **максимум среди тарифов** хостинга; карточка с нераспознанной строкой канала под
  порог > 0 не проходит. Ступени 100/500/1000/2500/10000 — в тон логарифмической шкале.
- ⚠️ **Отклонение от формулировки задания:** списка тарифов на карточке нет и не было (карточка показывает
  «N тарифов» и минимальную цену), поэтому на карточке рисуется ОДНА полоска — по самому широкому
  распознанному каналу, и это ровно тот канал, по которому отбирает фильтр. Полный по-тарифный набор полосок —
  в деталях. Разворачивать 5-10 полосок на карточке значило бы сделать её нечитаемой.

### 18d. Верификация
`python -m pytest` → **988 passed** (было 980; +8 тестов заметок/has_api) при снятом известном
пре-существующем `test_haproxy::test_deploy_reports_images_not_built`. `tsc --noEmit` чисто; vitest 4 файла /
61 тест (channel 29, metrics 13, search 13, гейт цветов 6). **Не проверено вживую** (нет стенда): вид полосок
и работа фильтров в браузере — покрыты типами и юнит-тестами чистых модулей.

## 19. Настройки: вкладка «AI» и вход в провайдеров через CLIProxyAPI (OAuth)
- **Вкладки «MCP» и «Ассистент» объединены в одну «AI»** (`Settings.tsx`, `SubTab "ai"`): внутри стопкой
  `AiSettingsTab` → `CliProxyAuth` → `McpTab`. Это части одной подсистемы, и раньше настройка одного
  требовала прыгать в другую вкладку. Регрессия — `Settings.test.tsx` (список вкладок + проверка, что три
  блока живут на одной странице).
- **`settings/CliProxyAuth.tsx` (NEW)** — интерфейс к УЖЕ существовавшему бэкенду `/api/cliproxy`
  (Волна 7, План F: `config/status/start/stop`, `accounts` + PATCH/DELETE, `oauth/{start,callback,status}`).
  До этого в UI был только переключатель шлюза, а войти в провайдера было нечем.
- **Headless-флоу, как он работает:** `POST /oauth/start {provider}` → ссылка + `state` (шлюз держит
  ожидание колбэка **5 минут**) → человек входит, его редиректит на несуществующий loopback → он копирует
  URL целиком → `POST /oauth/callback {state, redirect_url}` → поллинг `GET /oauth/status?state=` каждые 2 с
  до `ok|error`. ⚠️ **Kimi — device-flow:** там ссылка сама и есть подтверждение, вставлять нечего, поэтому
  для него поллинг стартует сразу после открытия ссылки и поле URL не показывается.
- ⚠️ **У Gemini OAuth-логина НЕТ** (только API-ключ/Vertex) — это написано в UI прямым текстом, а не
  спрятано отсутствием кнопки; модели Gemini даёт вход Google-аккаунтом через **Antigravity**.
- ⚠️ **Шлюз — общая инфраструктура на инсталляцию** (как xray-checker): владелец = тот, кто включил;
  остальным бэкенд отдаёт 403 на изменения, а UI показывает плашку «пул аккаунтов общий, лимиты
  расходуются совместно». Мастер-ключ клиента отдаётся ТОЛЬКО владельцу, ключ Management API — никому.
- ⚠️ **5 неудачных авторизаций Management API с одного IP → бан IP на 30 минут**, поэтому клиент бэкенда
  делает ровно один запрос и не ретраит 401. В UI это значит: ошибку показываем, автоповтор не заводим.
- Проверка: `tsc` чисто; vitest `Settings.test.tsx` 8 (+1 новый), `AiSettingsTab.test.tsx`, гейт цветов —
  зелёные. **Не проверено вживую:** сам OAuth-вход (нужен реальный контейнер шлюза и аккаунт провайдера).

### 19a. Хостинги: общая оценка на карточке и сортировка по набору тегов
- **Общая оценка** — бейдж рядом с названием на карточке (`ScoreBadge`), значение — существующий
  `avgScore` (среднее по ЗАПОЛНЕННЫМ метрикам, скрытый fair use не учитывается). ⚠️ При отсутствии оценок
  бейдж не рисуется вовсе: «0.0» читалось бы как плохая оценка, а не как «не оценено».
- **Сортировка «Совпадение тегов ↓»** — ранжирует по числу выбранных тегов у карточки. Считается в
  `sortValue` (это свойство пары «карточка + текущий фильтр», а не самой карточки), пустой набор → `null`
  у всех → порядок по имени, а не случайный.
- ⚠️ **Зачем появился режим «все / любой» у тегов:** фильтр по тегам был И (карточка обязана иметь ВСЕ
  выбранные), и при нём сортировка по совпадению вырождается — у всех прошедших совпадение одинаковое.
  Режим «любой» (ИЛИ) делает набор тегов предпочтением, а не жёстким отбором. Дефолт остался «все» —
  прежнее поведение не менялось; переключатель показывается только при 2+ выбранных тегах.
- `sortHostings` экспортирована ради регрессии `hostings/sort.test.ts` (4): ранжирование по совпадению,
  пустой набор, неизменность входного массива, «неоценённые всегда внизу в обе стороны».

### 19b. Хостинги: таблица «БС подсети»
- `models/hostings.py::BsSubnet{network, asn, org, checked_at, response}` + `HostingBody.bs_subnets` (≤200).
- ⚠️ **Все пять ячеек — свободный текст, включая дату и ASN.** Сюда переносят выписки из чужих источников
  («AS12345», «~май 2026», «отвечает, 20 ms»); `type=date` и `int` отрезали бы ровно то, что человек
  записал. Ячейка нормализуется как тег (схлопывание пробелов и переводов строк, ≤120) — это строка
  таблицы, а не заметка.
- Пустая строка, оставшаяся от нетронутого «+ Строка», отсеивается валидатором списка (как `note_fields`).
- UI: редактор-таблица в модалке хостинга (горизонтальный скролл на узких экранах) и read-only таблица в
  деталях; пустая ячейка рисуется прочерком. Тесты `test_hostings.py` (+2): round-trip и отсев, дефолт у
  карточек без ключа.
- Не сделано (не просили): фильтр «есть БС» в панели каталога — поле есть, фасет добавить недолго.

### 19c. Хостинги: полоска канала на карточке — сегменты по тарифам
- `ChannelStrip` (в `ChannelBar.tsx`): одна лента, сегментов столько, сколько у хостинга тарифов; цвет
  сегмента — ступень канала ЭТОГО тарифа. **Полоска сверху, подписи под ней** (порядок задан
  пользователем): сначала читается цветовая картина провайдера целиком, цифры — по необходимости.
- ⚠️ **Сегменты РАВНОЙ ширины, а не пропорциональные скорости.** Лента отвечает на вопрос «сколько тарифов
  и какие у них каналы»; при пропорциональной раскладке 100-мегабитный тариф рядом с 10G съёжился бы в
  невидимую полоску. Скорость передана цветом и подписью, а логарифмическая ширина (`channelPct`) осталась
  там, где сравнивается ОДИН канал — в таблице тарифов в деталях (`ChannelBar`).
- `fmtChannelShort` — компактная подпись («100М», «1Г», «2.5Г»): под сегментом бывает 30-40 px, полная
  форма «1 Гбит/с» там обрезается многоточием и читается хуже.
- Тариф без распознанного канала занимает своё место приглушённой дорожкой с прочерком — цветом ступени
  он не притворяется. Если не распознан НИ ОДИН канал, лента не рисуется вовсе.
- Фильтр «минимум Мбит/с» по-прежнему смотрит на самый широкий канал (`widestTariff`) — это самый яркий
  сегмент ленты, так что фильтр и картинка согласованы.

### 19d. Ассистент через CLIProxyAPI: ключ провайдера больше не требуется
**Симптом:** OAuth-вход в шлюз проходил, но чат отвечал «API-ключ провайдера не задан».
**Причина:** `ai_agent.run_agent` брал ключ ТОЛЬКО из `api_key_enc` независимо от режима, а `list_models`
и вовсе выходил раньше сети. Через CLIProxyAPI провайдерский ключ не нужен: доступ к моделям даёт
OAuth-аккаунт ВНУТРИ шлюза, а нас самих шлюз пускает по своему клиентскому **мастер-ключу**
(`cliproxy_master_key_enc`, генерится в `cliproxy_server.ensure_keys`).
- **`ai_agent.effective_target(config) -> (config, key)`** — одна точка резолва, используется чатом,
  `list_models` и роутом `/api/ai/models`. Через шлюз: ключ = мастер-ключ, `base_url` = внутренний адрес
  контейнера **+ `/v1`** (тёрны собирают `{base_url}/chat/completions`, а `internal_base_url()` отдаёт
  корень). Без шлюза — прежнее поведение байт-в-байт.
- ⚠️ **`_gateway_is_ours` смотрит на `cliproxy_enabled` И на `gateway_internal`.** Включение шлюза в UI
  ставит первый флаг, а второй исторический оставался выключенным — из-за этого SSRF-гард не пускал
  внутренний хост, а base_url не подменялся. Тот же предикат теперь и в `_check_base_url`.
- Внешний (не наш) шлюз: `base_url` НЕ подменяем, ключ берём из поля API-ключа — им такой шлюз и пускает.
- `/api/ai/config` отдаёт новое поле **`auth_ready`** («есть чем авторизоваться»), `AiChat.tsx` гейтит
  предупреждение по нему с откатом на `has_key`. Смысл `has_key` не меняли — он про поле ключа в форме.
- Тесты `test_ai_gateway.py` (+3): мастер-ключ вместо провайдерского и `/v1` в адресе; без шлюза ничего не
  меняется; внешний шлюз использует пользовательский ключ и свой адрес.

### 19e. Ассистент: вложения в чате
- Прикрепление кнопкой-скрепкой, **перетаскиванием** на композер и **вставкой из буфера** (скриншот —
  самый частый случай). Чипы с именами, удаление по крестику. Лимиты: 5 файлов, 40 000 символов текста
  на файл, 4 МБ на картинку; они продублированы на клиенте и на сервере (`api/ai.py::Attachment`).
- ⚠️ **Вложения НЕ персистятся** и не идут в общее хранилище медиа (§16a): они относятся к одному вопросу,
  а не к аккаунту — складывать их в библиотеку значило бы засорять её. Файл читается в браузере и едет
  в теле запроса `/api/ai/chat`.
- **`ai_agent.build_user_content(prompt, attachments, provider)`** — первое сообщение пользователя:
  - текстовые файлы **вклеиваются в текст промпта** («--- Вложение: имя ---»), поэтому работают у любой
    модели, включая те за шлюзом, что не умеют vision;
  - картинки уходят блоками контента, и вот их форма у провайдеров РАЗНАЯ (`image_url` c data-URI у
    OpenAI-совместимых, `image.source.base64` у Anthropic) — поэтому сообщение собирается здесь, а не в
    тёрне. `_openai_turn`/`_anthropic_turn` не менялись: они и так прокидывают `messages` как есть.
  - без картинок возвращается обычная СТРОКА — старый путь не меняется байт-в-байт.
- Тесты `test_ai.py` (+4): вклейка текста, разная форма блока картинки у двух провайдеров, отсутствие
  вложений не меняет тип сообщения, лимиты (лишние файлы и длинный текст срезаются).

### 19f. Экспорт/импорт: выбор конкретных данных
- **Экспорт**: `POST /api/export` уже принимал `stores`, но интерфейс его не показывал. Теперь в карточке
  переключатель «Всё / Выбрать данные» и чеклист по группам разделов панели.
- **Секции настроек — отдельные пункты выбора.** «Только HAProxy» или «только Remnawave» — это секции
  ОДНОГО `settings.json`, поэтому введён виртуальный стор `settings:<секция>` (`SECTION_PREFIX`). Выбор
  хотя бы одной секции включает settings.json в архив, но **урезанный** (`_slice_settings`) — иначе
  «только HAProxy» утащил бы всю конфигурацию аккаунта.
- **Импорт стал двухшаговым:** `POST /api/import/peek` (новый) читает архив БЕЗ записи на диск и отдаёт
  список сторов/секций внутри → пользователь отмечает нужное → `POST /api/import` с полем `stores`
  (пусто = всё, как раньше).
- ⚠️ **`_merge_settings` получил два правила, и оба про «не потерять невыбранное»:** при заданных секциях
  накладываем ТОЛЬКО их поверх существующих (выбор «только HAProxy» не трогает Remnawave); архив без
  секретов по-прежнему НИКОГДА не перезаписывает учётные секции — в нём они обнулены, и импорт стёр бы
  рабочие токены цели. Признак «архив с секретами» берётся из манифеста.
- Названия сторов человеку показывает `settings/storePicker.tsx` (бэкенд оперирует именами файлов).
  ⚠️ Стор, о котором раскладка ещё не знает, попадает в группу «Не разложено» под техническим именем —
  молча спрятать его значило бы потерять данные при экспорте «выбранного».
- В UI явно сказано, чего в архиве НЕТ: история мониторинга и статистики (SQLite), файлы Библиотеки и
  медиа, карточки деплоя (они в localStorage). Иначе выбор «только статистика» выглядел бы сломанным.
- Тесты: `test_export_io.py` (+4 — выборочный экспорт, одна секция настроек, выборочный импорт, невыбранная
  секция не тронута), `settings/storePicker.test.ts` (4).

### 19g. Биллинг: новые адаптеры провайдеров (частично отгружено)
Пользователь прислал сводку по 22 провайдерам с готовыми вердиктами. Разложено так:
- **Отгружено (4):** `aeza` (X-API-Key, баланс/услуги/платежи), `timeweb` (Bearer,
  `/api/v1/account/finances`), `vdsina` (токен, баланс+услуги), `netangels` (баланс).
  ⚠️ **NetAngels — двухшаговая авторизация:** `POST panel.netangels.ru/api/gateway/token/` c `api_key` →
  Bearer-токен, живущий **24 часа с последнего использования**; адаптер кэширует его в памяти (приём из
  `yandex.py`), иначе каждый вызов баланса дёргал бы авторизацию. Покрыто тестом.
  ⚠️ Документация Aeza заархивирована (2023) → парсер намеренно терпимый: ищет сумму и валюту в нескольких
  написаниях и снимает конверт `{"data": …}`.
  Реестр `_MODULES` — теперь 12 kind.
- **Не сделано (лимит сессии оборвал агентов):** ionos, ovhcloud, infomaniak, latitude, aws, alibaba,
  cloudru, ishosting, hostkey, billmanager (FirstVDS/AdminVPS одним адаптером), servers_com.
  Заготовленные требования — в промпте workflow `billing-adapters-wave` (подписи AWS SigV4 / Alibaba RPC /
  OVH sha1 собирать на hmac+hashlib, SDK не тянуть; у is*hosting и HOSTKEY ручки ОПЛАТЫ не реализовывать —
  адаптер read-only).
- **Осознанно НЕ делаем:** netcup, contabo, lightnode, 3hcloud (биллинг только в панели — по сводке
  пользователя), edgecenter, mws (billing-эндпоинты в публичной документации не подтверждены). Адаптер,
  который молча не работает, хуже его отсутствия.
- ⚠️ Поле кредов у каждого адаптера своё (`aeza`/`netangels` — `api_key`, `timeweb`/`vdsina` — `token`);
  форма в «API токенах» строится из `FIELDS`, поэтому руками их перечислять не нужно.

### 19h. Старые провайдеры каталога получили синхронизацию
Три из шести «без API» стали полноценными адаптерами (`_MODULES` — 15 kind):
- **`digitalocean`** — самый полный биллинг из старых: `/customers/my/balance`, `/droplets`,
  `/customers/my/billing_history`. ⚠️ Суммы приходят СТРОКАМИ («25.50») — парсим через float.
  ⚠️ В истории положительная сумма = начисление, отрицательная = платёж; приводим к своему
  `type: charge|topup` явно, иначе знак путал бы дешборд. У дроплета берём ПУБЛИЧНЫЙ ipv4, не приватный.
- **`hetzner`** — `GET /v1/servers` (токен **project-scoped**: сервера другого проекта по нему не видны).
  ⚠️ **Баланса в Cloud API нет** — счета живут в Console/Robot, поэтому `CAPS` не заявляет `balance`,
  а `balance()` возвращает None. В карточке остаётся ручной ввод; выдумывать ручку хуже.
- **`selectel`** — `GET https://api.selectel.ru/v3/balances`, заголовок `X-Auth-Token`. Ответ вложенный:
  суммируем `billings[].final_sum` либо значения `balances[]`, потому что в карточке нужен ОДИН остаток.
  ⚠️ **Единицы не задокументированы**, исторически Selectel отдаёт копейки. Значение берётся как есть и
  НЕ делится: молча уменьшить баланс в сто раз хуже сырого числа, которое человек сверит с панелью.
  Если на живом аккаунте окажутся копейки — делитель добавляется здесь, в одном месте.
- **Остались без адаптера:** `datacheap` (публичного API не нашёл), `generic` (это не провайдер, а
  «прочее»), `cloudflare` (у него отдельный модуль §15d со своим подключением — мост «CF как провайдер»
  описан в Плане C Ф7 и не сделан).
- Фронт: `InfraApiTokens.LEGACY_KINDS` теперь **само** отфильтровывает kind, у которого появился адаптер,
  иначе провайдер двоился бы в селекторе — и в группе «с API-синхронизацией», и в «без API».
- Тесты `test_providers_simple.py` (12): маппинг всех трёх, публичный ip у DO, знак суммы в истории,
  суммирование вложенных балансов Selectel и нормализация валюты, отсутствие `balance` у Hetzner.

### 19i. Биллинг: 11 адаптеров дочищены + покупка ресурсов из «Услуг»
**Адаптеры (реестр — 26 kind).** Добавлены `ionos`, `ovhcloud`, `infomaniak`, `latitude`, `aws`, `alibaba`,
`cloudru`, `ishosting`, `hostkey`, `billmanager`, `servers_com`. Что важно помнить:
- ⚠️ **OVHcloud**: подпись `"$1$" + sha1(AS+"+"+CK+"+"+METHOD+"+"+URL+"+"+BODY+"+"+TS)`, а `TS` берётся из
  `GET /auth/time` вендора и кэшируется как дельта — расхождение локальных часов даёт 401, неотличимый от
  неверных ключей. Тест пересобирает подпись руками и сверяет заголовок.
- ⚠️ **AWS**: SigV4 собран вручную (boto3 не тянем), Cost Explorer `GetCostAndUsage`; баланса у AWS нет →
  `payments` (расход за месяц), `services` не реализованы осознанно.
- ⚠️ **Alibaba**: RPC-подпись HMAC-SHA1 с percent-encoding по их правилам (`~` не кодируется, `*`→`%2A`);
  в тесте nonce и timestamp зафиксированы monkeypatch-ем, иначе проверка недетерминирована.
- ⚠️ **BILLmanager** — ОДИН адаптер на FirstVDS/AdminVPS и любых других на ISPsystem. `base_url`
  пользовательский → `net_guard.is_safe_url` стоит **внутри транспортной функции**, а не в вызывающих: так
  гард нельзя обойти новым методом. Квирки ISPsystem: ошибка приезжает с HTTP 200 в `doc.error`, скаляр —
  это `{"$": "значение"}`, одна запись приходит объектом, а не списком.
- ⚠️ **is\*hosting и HOSTKEY — структурно read-only**: ручки оплаты счетов и пополнения НЕ реализованы,
  в тестах на них стоит ловушка. Фоновый синк не должен уметь тратить деньги.
- `servers_com` — только `services`: публичный API счетов не отдаёт (счета в портале), ручки не выдуманы.

**Покупка ресурса (Услуги и тарифы → «Купить»).** Контракт заказа в `base.py`: `OrderPlan`/`OrderOptions`,
`order_options()`, `create_order()` — НЕабстрактные, с дефолтами, поэтому адаптеры без заказа не тронуты.
Умеют заказ: `ruvds` (конструктор), `digitalocean`, `hetzner`; у `selectel` — честный отказ (публичного API
заказа не подтвердилось).
- Гейты `POST /providers/{uuid}/order` — как у покупки домена: `confirm` → адаптер умеет `order` и креды
  читаются → **цена перечитывается сервером** и сверяется с `expected_price` (дрейф > 0.01 → 409) →
  неизвестная цена = отказ. После успеха создаётся локальная услуга (требование «купленное попадает в биллинг»).
- ⚠️ **`quote_order` — почему появился.** У RuVDS «тариф» это ПРАЙС-ЛИСТ (цена за ядро/ГБ), у плана
  `price=None`, и fail-closed гейт заблокировал бы любой заказ. Выдумать цену в адаптере нельзя — это обошло
  бы подтверждение. Поэтому в контракт добавлен `quote_order(creds, spec)` (дефолт None), RuVDS реализует его
  через штатный `POST /v2/servers?get_price_only=true` — вендор считает сумму, ничего не создавая. Маршрут
  берёт цену у плана, а при её отсутствии — у расчёта; `POST /providers/{uuid}/order-quote` отдаёт сумму
  форме до подтверждения. Регрессия — `test_infra_ordering.py::test_price_comes_from_a_quote_when_the_plan_has_none`.
- Пароли из ответов вендора (RuVDS `password`, Hetzner `root_password`) наружу НЕ отдаются: в контракте
  заказа поля для секрета нет, а карточка заказа персистится на клиенте.
- Верификация: `pytest` — **1116 passed** (было 1010); `tsc` чисто. Вживую не проверено ничего из этого —
  нужны реальные ключи, а заказ ещё и тратит деньги.

### 19j. Заказ услуг: 10 провайдеров и ослабленный гейт цены
- Заказ реализован у: `ruvds` (конструктор), `digitalocean`, `hetzner`, `openstack` (VK Cloud, Procloud),
  `regru_cloudvps`, `timeweb` (конструктор), `latitude`, `aeza`, `hostkey`, `servers_com` (облачные
  инстансы). Отказывают осознанно: `selectel` (заказ идёт через OpenStack/Nova с ПРОЕКТНЫМИ кредами,
  которых у аккаунтского адаптера нет) и `billmanager`.
- ⚠️ **Почему у BILLmanager заказа нет** (а он покрыл бы FirstVDS, AdminVPS и всех на ISPsystem):
  конфигурация задаётся параметрами `addon_<id>=<id значения>`, где ОБА числа — записи БД конкретного
  провайдера (у FirstVDS `addon_56329=93`, в примерах ISPsystem `addon_10`), имя функции плавает между
  сборками (`vds.order.param` / `v2.vds.order.param` + `force_use_new_cart=on`), а `skipbasket=on`
  списывает деньги СРАЗУ. Цена ошибки в угадывании — оплаченный сервер не той конфигурации, поэтому
  `create_order` отказывает БЕЗ сетевого запроса, а `order` в CAPS не заявлен.
- ⚠️ **Гейт цены ослаблен осознанно.** Прежний «цена неизвестна → отказ» закрывал покупку целому классу
  вендоров: у OpenStack/Nova у flavor'а стоимости нет вовсе и никакой расчёт её не даст. Теперь порядок:
  цена из плана → `quote_order` у вендора (RuVDS: `get_price_only`, ничего не создаёт) → если цены нет
  всё равно, покупка возможна ТОЛЬКО с отдельным полем `acknowledge_unknown_price`. Галочка НЕ отключает
  сверку там, где цена есть (регрессия `test_acknowledgement_does_not_disable_the_price_check`).
- **Пре-существующий баг, найденный агентом:** `hostkey.py` заявлял `order` в CAPS, не имея ни
  `order_options`, ни `create_order`, а `_form` звал несуществующий `_api_error` (NameError при первом
  обращении). Проверка согласованности «CAPS ↔ методы» прогнана по всем адаптерам: расходятся только
  `selectel` и `billmanager` — у них есть `create_order` с отказом и намеренно нет `order` в CAPS.
- ⚠️ Пароли из ответов вендоров (`adminPass` у Nova, `root_password` у Hetzner, RuVDS) наружу НЕ отдаются:
  в контракте заказа поля для секрета нет, а карточка заказа персистится на клиенте.
- Тесты `test_ordering_{a,b,c}.py` (+ ordering в общих файлах). В каждом тесте про отказ стоит счётчик
  создающих POST: повтор здесь означает второй оплаченный сервер. ⚠️ Считать надо POST **на путь
  создания**: у OpenStack `/auth/tokens` — тоже POST, и наивный счётчик даёт 2.
- ⚠️ Тесты, фиксировавшие `CAPS == {"services"}`, сломались на появлении `order`. Утверждать надо СМЫСЛ
  («баланса у вендора нет» → `"balance" not in CAPS`), а не набор целиком.

### 19k. Заказ ресурсов: 18 из 26 адаптеров
- Умеют заказ: `ruvds`, `digitalocean`, `hetzner`, `openstack` (VK Cloud, Procloud, Infomaniak Public Cloud),
  `regru_cloudvps`, `aeza`, `timeweb` (конструктор), `latitude`, `hostkey`, `servers_com`, `vdsina`,
  `beget`, `ishosting`, `aws` (EC2), `alibaba` (ECS), `oracle` (OCI), `ionos`, `yandex` (Compute).
- **Осознанные отказы (create_order отвечает словами, БЕЗ сетевого запроса, `order` не в CAPS):**
  `selectel` (конструктор создаётся через OpenStack проектными кредами — путь уже покрыт адаптером
  `openstack`), `billmanager` (конфигурация задаётся `addon_<id>=<id>`, где оба числа — записи БД
  конкретного провайдера, а `skipbasket=on` списывает деньги сразу), `ovhcloud` (корзина: cart→assign→
  item→configuration→checkout, набор параметров свой у каждого предложения), `veesp` (документация
  клиентского API закрыта JS-проверкой), `infomaniak` (публичной ручки нет; их Public Cloud — это
  OpenStack, есть отдельный адаптер), `regru_account` (это API доменов, а не серверов), `cloudru`,
  `netangels`.
- ⚠️ **Обязательные идентификаторы вендора выводятся из каталога, а не спрашиваются у пользователя:**
  подсеть Yandex — из `vpc/v1/subnets` по выбранной зоне; `SecurityGroupId`/`VSwitchId` Alibaba — из
  `DescribeVSwitches`/`DescribeSecurityGroups` с обязательным совпадением `VpcId`; подсеть Oracle — из
  `ListSubnets` домена доступности. Не удалось вывести — отказ ДО создающего запроса с текстом, что
  именно создать в консоли.
- ⚠️ **Beget: memory и disk в МЕГАБАЙТАХ** (и в каталоге, и в конструкторе, и в теле создания), а
  контракт — в гигабайтах; конверсия под отдельными тестами: ошибка здесь = сервер в 1024 раза не того
  размера. Там же закрыт настоящий дефект: кэш JWT ключевался логином (не секрет) — чужой аккаунт
  панели получил бы готовую сессию, введя верный логин и любой пароль; ключ стал `sha256(логин+пароль)`.
- ⚠️ **is\*hosting: заказ ≠ оплата.** `POST /billing/order` выставляет счёт, сервер появляется после
  оплаты в панели; полей оплаты в тело не кладём вовсе, иначе прежний запрет «не тратить деньги по
  расписанию» обходился бы одним параметром. В `res["id"]` — номер счёта.
- ⚠️ **Тесты утверждают СМЫСЛ, а не снимок CAPS.** Три теста падали при каждом расширении
  (`caps == ["balance"]`, `CAPS == {"payments"}`, «Beget как пример адаптера без заказа»). Переписаны на
  `"balance" in caps` / `"balance" not in CAPS` / тривиальный наследник `ProviderAdapter` — база и есть
  то, что проверялось.
- Верификация: `python -m pytest` → **1253 passed** при снятом известном пре-существующем
  `test_haproxy::test_deploy_reports_images_not_built`. **Вживую не проверено ничего** — нужны реальные
  ключи, а заказ ещё и тратит деньги; всё сверено с документацией и клиентами вендоров, тесты на фикстурах.

## 20. Ассистент: контекст, веб-поиск и доступ ко всем разделам панели
> Запрос (2026-07-29): «Добавь контекст и веб поиск в ассистента. Обнови возможности ассистента, пусть
> он будет уметь взаимодействовать со всеми аспектами node-assistant.» До этой волны у встроенного агента
> было ЧЕТЫРЕ инструмента (`list_rules`/`list_subscriptions`/`node_health`/`list_nodes`), не было ни
> истории диалога, ни интернета — на «сколько я плачу за хостинги» он разводил руками.

### 20a. Мост в собственный REST вместо рукописных инструментов
- **`services/ai_tools/bridge.py`** — `panel_endpoints` (каталог ручек), `panel_get`, `panel_write`.
  Транспорт — **`httpx.ASGITransport(app=app)` прямо в наше же приложение**: без сокета, с настоящей
  валидацией pydantic и настоящим `require_account`; токен — обычный `accounts.issue_token` (как у MCP).
- ⚠️ **Почему мост, а не полсотни инструментов.** Вся панель уже опубликована как REST, и таблица
  маршрутов FastAPI — единственный источник правды, который не отстаёт от кода. Рукописный набор
  пришлось бы дописывать при каждой новой ручке, и он молча устаревал бы. `endpoints()` читает
  `app.routes`, поэтому «все аспекты» включает и то, что появится завтра.
- ⚠️ **`endpoints()` не показывает запрещённые пути**, а в режиме чтения ещё и схлопывает методы до
  `GET`: реклама того, на что мы всё равно ответим отказом, тратит шаг агента и провоцирует искать обход.

### 20b. Три границы (и почему они проверяются при вызове, а не при выдаче списка)
1. **`DENY`** — жёсткий список, не зависящий от режима: секреты (`/vault/*/reveal`, `/certs/download`,
   `/panel/env`, `/backup`, `/export`), деньги (`/providers/*/order`, `/domains/register`),
   инфраструктура (`/deploy`, `/node/`, `/panel/`, `/updates/apply`, `/replace-domain`, `/certwarden`,
   `/netbird`, SSH-замеры), самоконфигурация агента (`/api/ai`, `/api/mcp`, `/api/cliproxy`,
   `/api/api-tokens`, `/api/auth`) и сырой прокси `/haproxy/proxy/` (он шёл бы мимо этих же правил).
   Настройки: GET можно, запись нет.
2. **`readonly`** (по умолчанию включён) — всё, кроме `GET`, отклоняется до вызова.
3. **`DELETE` запрещён всегда** — удаление необратимо, а модель ошибается в идентификаторах.
- ⚠️ Проверка стоит в `ai_tools.run`/`bridge.call`, а не только в `available()`: **витрина инструментов —
  не граница авторизации**, модель вправе назвать инструмент и путь, которых ей не показывали (в т.ч.
  подхватив их из результата другого инструмента). Тот же урок, что у MCP-гейта в `ai_agent._run_tool`.

### 20c. Скрабер ответов — не «на всякий случай», а закрытие конкретной дыры
`GET /api/settings` отдаёт `remnawave.api_token` **открытым текстом** (так он и лежит в settings.json), а
ответ моста целиком уезжает в чужой LLM-эндпоинт. Поэтому `bridge.scrub()` рекурсивно вырезает значения
ключей по подстроке (`token|api_key|secret|password|_enc$|private_key|hmac|credential|pat|…`).
⚠️ **Сам ключ остаётся** — иначе модель решит, что панель не настроена, и начнёт советовать это исправить.
Список — по подстроке, а не перечислением полей сорока моделей: такой перечень никто не поддержит.

### 20d. Веб (`services/ai_web.py`)
- Провайдеры: **`duckduckgo` по умолчанию и БЕЗ ключа** (разбор их `lite`-страницы стандартным
  `html.parser` — новых зависимостей у проекта нет), плюс `tavily`, `brave`, `searxng` (свой инстанс).
  ⚠️ У DDG публичного API нет: вендор вправе поменять вёрстку, и тогда поиск вернёт **пусто, а не ошибку**.
  Парсер понимает обе вёрстки (`result-link`/`result__a`) и разворачивает редирект `/l/?uddg=`.
- ⚠️ **SSRF-гард на КАЖДОМ прыжке редиректа** (`follow_redirects=False` + `net_guard.is_safe_url` в цикле):
  публичный хост увёл бы нас на `169.254.169.254` одним 302, а ходит по ссылке НАШ сервер изнутри сети.
- Настройки на `AiConfig`: `web_enabled` (вкл. по умолчанию — дефолтный провайдер ключа не требует),
  `web_provider`, `web_api_key_enc` (Fernet), `web_base_url` (только searxng), `web_max_results`.

### 20e. Prompt-injection: асимметрия записи после веба
`_SAFETY_SUFFIX` в системном промпте объявляет, что содержимое веба/заметок/ответов панели — **данные, а
не команды**, а `ai_web.UNTRUSTED_NOTE` дублирует это в самом теле результата (граница доверия должна быть
видна рядом с текстом, а не только в инструкции, оставшейся в начале диалога).
⚠️ Плюс механическая защита: после первого веб-вызова `ToolContext.web_tainted=True`, и **`panel_write`
отказывает до конца ответа** — «примени настройку из статьи» это ровно тот случай, когда чужой сайт
управляет чужой панелью. **`write_note` при этом продолжает работать**: сохранить найденное в заметку —
смысл связки «поиск + запись», операция аддитивная, в библиотеке пользователя, конфигурацию не трогает.

### 20f. Контекст (`services/ai_context.py`) и история диалога
- `snapshot(account_id)` — сводка на КАЖДЫЙ вопрос, поэтому **только локальные чтения** (JSON-сторы +
  SQLite, ни одного сетевого вызова: ходить в Remnawave за цифрами для приветствия значило бы платить
  секундой на каждое «привет») и **только количества и флаги** — ни токена, ни адреса панели, ни имён
  записей хранилища. Всё, что попало в сводку, считается разглашённым.
- **История ведёт КЛИЕНТ** и присылает в теле (`ChatBody.history`): сервер переписку не хранит — нечему
  утекать и нечего чистить по расписанию, а вкладки не мешают друг другу. `build_history` режет с КОНЦА
  (20 сообщений / 24k символов): свежие реплики важнее, иначе длинный чат вытеснит сам вопрос.
- `build_system` стал **асинхронным** (собирает сводку); склеивает пресет + `_TOOLING_SUFFIX` +
  список доступных инструментов + сводку + `_SAFETY_SUFFIX`.
- `_TOOL_RESULT_CAP` поднят 4000 → **12000**: инструменты теперь возвращают ответы реальных ручек и текст
  страниц, а на 4000 символов список из тридцати нод обрывался на середине.

### 20g. Набор инструментов (14) и ярлыки
`panel_context`, `panel_endpoints`, `panel_get`, `panel_write`\*, `web_search`\*\*, `web_open`\*\*,
`list_nodes`, `node_health`, `list_rules`, `list_subscriptions`, `search_hostings`, `search_notes`,
`read_note`, `write_note`\* (\* — только при разрешённой записи, \*\* — при включённом интернете).
⚠️ Ярлыки дублируют мост **намеренно**: «сколько нод онлайн» через `panel_get` стоит лишнего шага на
разведку пути и возвращает сырой ответ ручки. Платим длиной списка инструментов, поэтому ярлыков мало и
они покрывают только частые вопросы. `library_store.search_notes` добавлен туда же, а не к вызывающему:
искать по телу заметки можно только имея текст, который `list_items` намеренно вырезает.

### 20h. Что нашло состязательное ревью (9 агентов, 3 линзы) — НЕ регрессировать
- **⚠️ CRITICAL, денилист смотрел на неканонический путь.** `httpx` перед отправкой в ASGI сам схлопывает
  `.`/`..` и раскрывает `%XX`; проверялась одна строка, маршрутизировалась другая. `/api/vault/id/./reveal`
  отдавал секрет, `/api/clipro%78y/config` — мастер-ключ шлюза, причём **в режиме чтения**. Лечение:
  `normalize_path` канонизирует ТЕМ ЖЕ `httpx.URL`, дальше проверяется и отправляется ровно она, плюс
  сверка финального пути вплотную к отправке. Регрессия — `test_denylist_survives_path_canonicalization`
  (утверждает инвариант «решение по любой записи пути = решение по канонической», а не список случаев).
- **⚠️ CRITICAL, скрабер не видел реальных имён полей проекта.** Сравнение шло по имени как есть, поэтому
  `privateKey`, `master_key`, `X-API-Key`, `passphrase`, `access_key_id`, `consumer_key` проходили мимо.
  Теперь имя НОРМАЛИЗУЕТСЯ (нижний регистр, только буквы и цифры) + суффикс `enc` + **второй слой:
  `redact_text` ищет секреты в самих СТРОКАХ** (JWT, `sk-…`, `Bearer …`, PEM, `"privateKey":"…"` внутри
  вложенного JSON, креды в query). Короткие имена (`pat`, `pin`, `psk`) сверяются целиком — как подстрока
  они ловили бы `path` и `ping`.
- **⚠️ Отложенное выполнение мимо всех гейтов — целый класс, а не два случая.** `POST /api/updates/config`
  взводит `auto_update`, и фоновый луп сам вызовет `apply()` через ~6 часов; `POST /api/rules` создаёт
  правило, которое `rules_loop` выполнит вне денилиста, вне `readonly` и после того, как «загрязнение
  вебом» сбросится вместе с ответом (а среди действий — `node_disable`, `hide_hosts` и telegram, то есть
  ровно то сочетание, из-за которого закрыт автобэкап). Оба закрыты на запись, чтение осталось.
  **Правило при добавлении новой ручки: спрашивать не «что она делает», а «что она заставит сделать потом».**
- **HIGH:** `/api/certs/deploy` не было в списке вовсе (выпуск серта по SSH); `/api/config-templates` отдаёт
  `realitySettings.privateKey` JSON-строкой внутри JSON — раздел закрыт целиком, маскировать такое по именам
  полей ненадёжно; `/api/subscriptions` отдаёт URL-капабилити (закрыт, есть ярлык `list_subscriptions`,
  который отдаёт только хост).
- **HIGH, веб:** `search()` создавал клиент с `follow_redirects=True` — SSRF-гард searxng проверял только
  базовый адрес, а 302 уводил куда угодно. Теперь переходы делает `_get_guarded` (проверка каждого прыжка),
  как в `fetch()`. `web_base_url` не валидировался при сохранении — добавлен `net_guard` в `AiConfigBody`.
- **MEDIUM:** `MAX_FETCH_BYTES` лимитом не был (`r.content` читал тело целиком, резалось уже прочитанное) —
  теперь потоковое чтение с обрывом; таймаут httpx **пооперационный**, общий дедлайн даёт только
  `asyncio.wait_for` (`_DEADLINE`); добавлен потолок сетевых вызовов на ответ (`MAX_WEB_CALLS`).
- **Остаточный риск, принят осознанно:** (а) `net_guard` резолвит хост в гарде, а httpx резолвит повторно —
  DNS-rebinding внутри одного вызова возможен, но это свойство ВСЕГО проекта, не только ассистента;
  (б) флаг «загрязнения вебом» живёт один ответ. Второе сообщение стартует чистым — но содержимое страницы
  в историю НЕ попадает (клиент шлёт только текст реплик, результаты инструментов остаются на сервере),
  поэтому инъекция может пережить границу только в виде пересказа самой модели.
- ⚠️ **Фронтенд:** таблица `WEB_PROVIDERS` объявляла `needsKey?: boolean` и ни разу его не выставляла —
  поле ключа не появлялось никогда, при том что комментарий рядом объяснял, зачем оно считается локально.
  Поймано тестом самого агента; при правке таблицы провайдеров проверять, что флаг проставлен.

### 20i. Верификация Волны «ассистент»
- Backend: `python -m pytest` → **1329 passed** при снятом известном пре-существующем
  `test_haproxy::test_deploy_reports_images_not_built`. База 1253 → +76 (`test_ai_tools` 27,
  `test_ai_web` 30, `test_ai_context` 13, правки в `test_ai_prompts`/`test_mcp_client`).
- Frontend: `tsc --noEmit` чисто **кроме двух пре-существующих** ошибок (`marked`/`dompurify` — образ
  `ni-frontend-test` собран до Волны 10 и этих зависимостей не содержит, к правкам отношения не имеет);
  vitest `AiChat.test.tsx` + `AiSettingsTab.test.tsx` + гейт цветов = 29 зелёных.
- ⚠️ **Два теста упали правильно и оба указали на настоящий баг:** `builtin == len(TOOLS)` в
  `test_mcp_client` (эндпоинт теперь отдаёт РЕАЛЬНО доступное, а не размер каталога — переписан на
  утверждение свойства) и `fields_enc` в скрабере (нормализация имени съела прежнее правило `_enc$` —
  правило возвращено явным суффиксом).
- **Вживую не проверено:** ни один реальный ответ модели (нужен ключ провайдера или вход через
  CLIProxyAPI) и ни один реальный веб-поиск — DDG-парсер проверен на зафиксированных фикстурах обеих
  вёрсток, сеть в тестах не трогается вовсе.
