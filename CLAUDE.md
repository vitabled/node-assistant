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
  - `models/`: `deploy.py` (`DeployRequest`, `RenewCertsRequest`), `settings.py` (`AppSettings`/`RemnavaveConfig`/`DeployDefaults`/`OptimizationSettings`/`Template`), `traffic_rules.py`.
  - `services/`: `pipeline.py` (the 11-step deploy), `ssh_manager.py` (`SSHSession`), `remnawave_client.py`, `storage.py`, `task_store.py` (`Task`, `STEP_LABELS`), `cloudflare.py`, `backend_ip.py`.
  - `api/`: `settings.py`, `traffic_rules.py`, deploy/certs routers; wired in `main.py`.
- **Frontend:** React 18 + TS + Vite + Tailwind. `frontend/src/components/`: `DeployDashboard.tsx`, `DeployForm.tsx`, `DeployCard.tsx`, `Settings.tsx`, `CountrySelect.tsx`, `MultiSelect.tsx`, `TrafficRules.tsx`, `StepProgress.tsx`, `Sidebar.tsx`, `Dashboard.tsx`, `CertsForm.tsx`.
- **Persistence:** JSON files under `DATA_DIR` (default `/app/data`): `settings.json`, `templates.json`, `traffic_rules.json`. **Deploy job cards live in browser `localStorage`** (key `deploy_jobs`) — there is NO server-side task-list DB.
- **Live logs:** `SSHSession.run_script` pipes scripts to `bash -s 2>&1` and streams stdout line-by-line into the task; the frontend `DeployCard` consumes a per-task stream via `useTaskStream`.
- **Verify before "done":** backend `python -m py_compile` on changed files; frontend `npx --no-install tsc --noEmit`. Then update this file (§5/§6).

## 2. Frontend Behavior
- **Deploy modes (tabs):** the add-server modal has horizontal tabs **Remnanode** (default) / **HAProxy** (`FormData.mode`). Remnanode tab shows the Domain/SSL + Remnanode + Remnawave sections; HAProxy tab hides all of those and shows the HAProxy relay fields. Shared sections (Сервер, Сеть, SSH-порт, UFW, Оптимизация) render in both. Validation is mode-gated (`validateForm`): domain/email/Cloudflare/token/country only in remnanode mode; `haproxy_dest_ip` required in haproxy mode.
- **Settings → Deploy defaults** (`DeployDefaults`): `ssh_user`, `email`, `cloudflare_api_key`, `current_ssh_port` (22), `new_ssh_port` (2222), `open_ports`, `change_ssh_port` (true), `remnanode_port` (2222), `xhttp_path` (""), plus **HAProxy defaults** `haproxy_source_port` (443), `haproxy_dest_port` (443), `haproxy_maxconn` (200000), `haproxy_log` (global), `haproxy_mode` (tcp), `haproxy_timeout_{connect=5s,client=50s,server=50s,tunnel=1h}`. These auto-prefill the deploy form.
- **New-deploy modal MUST pass NO `initial`** so `DeployForm`'s settings-overlay effect (`if (!initial)`) runs; passing `FORM_DEFAULT` suppressed it and left email/Cloudflare/XHTTP empty (fixed). Edit/retry modal passes `editJob.savedForm` (keeps saved values).
- **Form fields:** `change_ssh_port` toggle (disables/skips new-port input when off; `useRef intendedNewPort` restores 2222 on re-enable), required `remnanode_port`, optional `xhttp_path`, required `CountrySelect` (single-select w/ search → `country_code`), `behind_cdn` checkbox, and **single-select** `plugin_uuid` `<select>` ("Не использовать плагин" = `""` → sent as `null`). Plugins load from `GET /api/remnawave/node-plugins`; squad/plugin selectors are disabled unless Remnawave is configured.
- **Traffic Rules** tab: table of quotas (ALL = node-level monthly/no-reset; SQUAD = user bulk, all periods) in GB.
- **Per-node stats (deploy cards only, NOT the status page):** a SUCCESS `DeployCard` polls `POST /api/stats/node` **every 5 min** with its own SSH creds from `savedForm` (creds per-request, never persisted → client-driven poll, not a stored-cred background worker, which the no-secrets-at-rest rule forbids). SSH port = `change_ssh_port ? new_ssh_port : current_ssh_port`. One SSH session runs 3 parallel probes; response has **`securityStats`** + **`trafficStats`**:
  - `securityStats {fail2banActive, fail2banTotal, trafficGuardActive}` — `fail2ban-client status sshd` parsed via `Currently banned:\s*(\d+)` / `Total banned:\s*(\d+)`; `iptables -L -n | grep -c 'na-ctguard'`. Card «Безопасность сервера» block, amber highlight when active bans > 0.
  - `trafficStats {today, week, month}` each `{rx, tx, total}` in **bytes** — parsed from **`vnstat --json`** (v2.x native JSON, bytes): aggregate across all `interfaces[].traffic`; today=latest `day`, week=last 7 `day` entries, month=latest `month` (also accepts legacy `days`/`months` keys). Card «Сетевой трафик» block with period selector (За сегодня/неделю/месяц), rows RX (↓)/TX (↑)/Total, bytes→ГБ/МБ (2 dp). **5-min cache** because vnstat updates its DB discretely.
- **vnstat** is installed in pipeline Step 2 (`apt install vnstat` + `systemctl enable --now vnstat`, non-fatal) — required software on every deployed node for the traffic block.
- **Dashboard instant render:** `DeployDashboard` keeps jobs in local state + localStorage. The "card not appearing until F5" bug was fixed with **functional `setState`** (`setJobs(prev => …)`) in `addJob`/`retryJob`/`removeJob` — a stale async closure had been writing a list computed from an outdated snapshot. NOTE: there is **no** WebSocket `TASK_CREATED` and no list-refetch — the mechanism is local state + per-card SSE stream.

## 3. Deploy Pipeline (`run_pipeline`, 11 steps — `STEP_LABELS` index = `_begin_step` N)
Any exception → `task.finish(FAILED)` and re-raise → node card shows FAILED + retry.

**Mode branch (after Step 6):** `if req.mode == "haproxy"` → `step_haproxy_deploy` (reuses step slot 7 via `_begin_step(task, 7, "Установка HAProxy-реле")`, **skips Steps 7–11**): `apt install haproxy`, backup cfg, write `/etc/haproxy/haproxy.cfg` from `_haproxy_cfg(req)` (TCP relay `bind *:$source` → `server $destip:$destport`), `haproxy -c -f` validate (`exit 1`→FAILED), `systemctl restart haproxy`, verify `is-active == active`. Else (remnanode) → normal Steps 7–11. `_effective_open_ports(req)` appends `haproxy_source_port` to the node-accelerator TCP/UDP ports in haproxy mode so the host firewall passes transit traffic.

1. **Подключение к серверу** — connect `SSHSession(ip, current_ssh_port)`; `get_backend_ip()` for whitelists.
2. **Обновление системы** — always `apt-get update`; `apt-get upgrade` only if `update_system`.
3. **Node Accelerator** (`step_node_accelerator`, gated on `optimize`) — `install.sh -s all`, then `git clone` + `protect.sh` with `SSH_PORT`(new or current per toggle)/`TCP_PORTS`/`UDP_PORTS`(=open_ports)/`NODE_PORT`(=remnanode_port)/`REMNAWAVE_URL`/`REMNAWAVE_TOKEN`/`REMNAWAVE_NONINTERACTIVE=1`. If `behind_cdn`: `na-ctguard` enable → `journalctl -t na-ctguard` → `NA_CTG_ENFORCE=1` → `systemctl stop na-fw-safety.timer`.
4. **TrafficGuard** (`step_traffic_guard`) — clone `DonMatteoVPN/TrafficGuard-auto`, run `install.sh` or iptables fallback; whitelist backend IP.
5. **Оптимизация + dual-port SSH + reboot** (`step_system_optimize(ssh, task, backend_ip, req)`) — Reshala kernel hardening, **fail2ban** (backend IP in `ignoreip`), ZRAM 40% + 4 GB swap. THEN if `change_ssh_port` → **Dual-Port strategy**: `_ssh_dualport_config_script(old,new)` opens BOTH ports in UFW (`ufw --force enable`), makes sshd listen on **both** (`Port old` + `Port new`, strips `sshd_config.d` overrides), `sshd -t` (`exit 1` → abort BEFORE reboot), fail2ban `port = old,new`. Then `_reboot_script` (`systemctl reboot --no-block`, nohup fallback) **cold-reboots** the box and closes the session. Rationale: prove the config survives an OS restart rather than trusting a single live session through a port swap.
6. **Проверка SSH после перезагрузки** (`step_ssh_dualport_verify(...) -> SSHSession`) — polls for the rebooted server (initial 20s wait, then up to **90s** via `_tcp_reachable` on either port), then branches:
   - **Scenario А** (new port SSH-connects) → finalize via `_ssh_cleanup_newport_script` (drop old `Port`, fail2ban→new, `ufw delete allow old/tcp`, restart sshd), whitelist, **return the new-port session**.
   - **Scenario Б** (new port dead, old port SSH-connects) → `_ssh_rollback_to_old_script` over the old-port session (drop new `Port`, fail2ban→old, `ufw delete allow new/tcp`, restart sshd), raise RuntimeError → FAILED («Смена порта не удалась после перезагрузки… откатана на порт N, доступ сохранён»).
   - **Scenario В** (neither port answers in 90s, or no SSH) → raise RuntimeError (critical network lockout) → FAILED.
   - `change_ssh_port` off → no reboot; whitelist current session, return it.
   - Helpers: `_tcp_reachable(host,port,timeout)` (asyncio.open_connection), `_try_ssh_connect(req,port,timeout)`.
7. **Cloudflare DNS + SSL** (`step_ssl`) — `upsert_a_record` (CF API), then **acme.sh DNS-01 per-FQDN** cert. `domaincert == domain` (FQDN) — NOT a root wildcard. Install to `/etc/ssl/certs/{domain}_fullchain.pem` + `/etc/ssl/private/{domain}.key`. Issuance gated on actual `_ecc/*.cer` files; `--force` only when missing.
8. **Remnanode** (`step_remnanode`) — write `/opt/remnanode/docker-compose.yml` (`remnawave-nginx:1.28` + `remnanode:latest`) and `nginx.conf` from templates; `docker compose up -d`; verify `remnanode` running.
   - `SECRET_KEY` = the long base64/JWT token (NOT the node UUID).
   - XHTTP `location $path { … grpc_pass unix:/dev/shm/xrxh.socket; }` block included only if `xhttp_path` set, else removed.
   - Cert bridge: symlink `/etc/ssl/...` certs into `/etc/letsencrypt/live/{domain}/`.
9. **WARP Native** (`step_warp`, gated on `install_warp`, **non-fatal** try/except) — uses **wgcf (WireGuard)**, NOT `warp-cli`. Register + generate, then patch `warp.conf`: `Table = off` (prevents default-route hijack that would kill SSH), remove `DNS`, `AllowedIPs = 0.0.0.0/0`, `PersistentKeepalive = 25`, `wg-quick up warp`. (Spec said `warp-cli mode proxy`; distillium/warp-native actually uses wgcf — implemented accordingly.)
10. **SSL Certbot (standalone)** (`step_certbot_ssl`) — write `/opt/certbot/docker-compose.yml`; open + free port 80 (`fuser -k 80/tcp`); `docker run --rm … certbot/certbot certonly --standalone --non-interactive --agree-tos --email $email -d $domain` (check=True → cert failure aborts). Then **awk-REPLACE** the remnanode service's `/etc/letsencrypt` mount with `/opt/certbot/certs` (append would duplicate the mount target → error), `docker compose down && up -d`, cron renew `0 0 28 * *`.
11. **Уникализация маскировочного сайта** (`step_sni_masking`) — `set -euo pipefail`, `curl -fL` the `distillium/sni-templates` zip into `/opt/`, random template via `$RANDOM`/`mapfile`, additive obfuscation (`openssl rand -hex 4` + `sed`: inject meta/comment into `<head>`, hidden marker before `</body>`, CSS comment) to change the page fingerprint without breaking markup, deploy to `/var/www/html`, clean up.

## 4. Remnawave API Integration (`remnawave_client.py`)
> **Audited against api-1.json = OpenAPI 3.0.0 "Remnawave API v2.8.0" (2026-07-01).** The integration layer was compared field-by-field (request required/optional fields, response envelopes, enums, pagination params) and is **fully in sync — no breaking changes** in any path we use. Rigorous checks confirmed: every request we send has all required fields and no unknown fields; node name 3–30, config-profile name 2–30 `^[A-Za-z0-9_\s-]+$`; every response still uses the `{ "response": … }` envelope. New v2.8.0 OPTIONAL node request fields exist but are intentionally unused: `proxyUrl`, `nodeConsumptionMultiplier`, `note`.
- `create_node(...)` → `POST /api/nodes` with `port=remnanode_port`, `countryCode`, `configProfile{activeConfigProfileUuid, activeInbounds}`, `activePluginUuid` (single, only when set). Response `uuid` is for **routing only — NOT the SECRET_KEY**.
- `get_node_secret_key()` → **`GET /api/keygen`** → `response.pubKey` = the node SECRET_KEY (the `eyJ…` token). The `POST /api/nodes` response has **no** token field — this was the "bricked node" bug.
- **Squad access fix:** after node creation, `add_inbounds_to_internal_squad(uuid, inbounds)` → `GET /api/internal-squads/{uuid}` then `PATCH /api/internal-squads` with the squad's current inbounds **unioned** with the node's `activeInbounds`. Without this, squad users have no access to the new node.
- Others: `list_node_plugins` (`GET /api/node-plugins` → `response.nodePlugins`), `list_internal_squads`/`list_external_squads`, `add_all_users_to_internal/external_squad`, `list_nodes`, `update_node_traffic`, `get_users_in_squad`, `bulk_update_users_traffic`, `create_config_profile`. All responses are unwrapped from the `{ "response": … }` envelope.

## 4b. Xray-Checker Analytics (Main Dashboard)
> Integration of `kutovoys/xray-checker` for the main dashboard (replaces the old SSH node-monitor). **Reality-check vs the original spec:** xray-checker is a pure Go 1.26 service (`CGO_ENABLED=0`) — **there is NO Fyne/GUI to disable**; it doesn't store history itself (exposes current state via a JSON API + Prometheus). **Chosen strategy (user decisions):** run it as the **official Docker image** (not compile-from-source), feed it a **Remnawave SUBSCRIPTION_URL**, store history in **SQLite**.

- **Docker-out-of-Docker (DooD):** the backend runs in a container, so it manages the sibling `xray-checker` container via the **host** daemon. Setup (all wired): backend `Dockerfile` installs `docker-ce-cli` (client only); `docker-compose.yml` mounts `/var/run/docker.sock` into the backend and gives the network an explicit `name: node-assistant-net`; `XRAY_CHECKER_NETWORK=node-assistant-net` env tells the orchestrator to run the checker with `--network node-assistant-net` and reach it by **container name** (`http://xray-checker:2112`) instead of `127.0.0.1:{port}` (the published host port would not be on the backend's own loopback). `_docker()` catches a missing binary → `_NO_DOCKER`/`container_state()="no-docker"`; the settings-save endpoint returns `{ok, warning}` (200, not 502) when the container can't start.
- **Orchestrator** `services/xray_checker.py` — manages container `xray-checker` via the `docker` CLI: `start` (=`docker run -d --restart unless-stopped -p {metrics_port}:2112 -e SUBSCRIPTION_URL/PROXY_CHECK_INTERVAL/PROXY_CHECK_METHOD/METRICS_PORT=2112 kutovoys/xray-checker:latest`), `stop`/`restart`/`get_logs`/`container_state`, and **`update`** (=`docker pull` → recreate; old container kept if pull fails). HTTP bridge to the checker's JSON API: `GET /api/v1/status` `{total,online,offline,avgLatencyMs}`, `GET /api/v1/proxies` `[{stableId,name,groupName,online,latencyMs,lastCheck}]`, `GET /api/v1/system/info`; responses unwrapped from `{success,data,error}`. **Deep check** = concurrently hit `/config/{stableId}` per proxy (live probe) then re-scrape.
- **Metrics store** `services/metrics_store.py` — stdlib `sqlite3` (no new pip dep) at `DATA_DIR/xray_checker_metrics.db`, table `proxy_samples(ts, stable_id, name, group_name, online, latency_ms)` (idx on ts and (stable_id,ts)); schema auto-created on import (the "migration"), **35-day retention** (for 30-day uptime). **In-memory ring buffer** `_RING` (deque maxlen 90 per node) holds the last ticks so the status-page uptime bars are served from RAM, not disk on every poll; warmed from SQLite on startup (`_warm_ring`, needs sqlite≥3.25 window fn — degrades gracefully). Tick status: `up` / `slow` (latency ≥ `SLOW_MS`=800) / `down`. Queries: `get_bars(n)` (ring), `get_uptime_30d()` (per-node + global), `get_incidents(days)` (down-runs → {start,end,durationSec,reason,ongoing}). `get_history(hours)` returns ~120 time-buckets of avg latency + availability; `get_node_uptime(hours)` returns per-node `{uptime_pct, checks, last_seen}` (`AVG(online)*100`). `/api/checker/status` merges `uptimePct`/`lastSeen` into each proxy → dashboard node table shows an "Аптайм 24ч" column (green ≥99 / yellow ≥95 / red). Async via `asyncio.to_thread`.
- **Poller** `api/xray_checker.poller_loop` — started in `main.py` **lifespan**; samples `/api/v1/proxies` every `xray_checker.poll_interval` (≥15s) into SQLite when the checker is enabled + running.
- **Settings model** `XrayCheckerConfig` on `AppSettings`: `enabled`, `subscription_url`, `check_interval` (300), `check_method` (ip), `metrics_port` (2112), `image`, `poll_interval` (60). Saved via `POST /api/settings/xray-checker` (also (re)starts the container when enabled).
- **Frontend** `Dashboard.tsx` is a **status-page UI** (original impl of the Uptime-Kuma/Stripe pattern): global health banner (ok green «Все узлы работают стабильно» / partial yellow / down red), 30-day uptime + protocol count + «Перепроверить все ноды» button, **country groups** (collapsible, grouped by `groupName`, flag via `flagFor()` reusing `COUNTRIES` from `CountrySelect`), compact **node rows** (flag+name, protocol badge, **uptime bar grid** of 30/60/90 thin bars green=up/amber=slow/red=down, live ping, 30d uptime%), and an **incident log** («История доступности за 7 дней»). Polls `/api/checker/statuspage?ticks=N` + `/api/checker/incidents` (10s). Xray-Checker config + «Обновить Xray-Checker»/Остановить live in Settings → Deploy (`XrayCheckerSettings`). NOTE: we render our own status page in the SPA (node-assistant is the single aggregating backend) — we do NOT build/proxy the go-build statuspage binary; the checker is the official Docker image.

## 4c. Инфра-биллинг (Infra-billing) — full 8-tab subsystem
> **Reality:** Remnawave's `InfraBillingController` (v2.8.0) exposes ONLY `/providers`, `/nodes`, `/history` (minimal fields: provider `{name,faviconLink,loginUrl}`; node `{nodeUuid,name,providerUuid,nextBillingAt}`; history `{amount,billedAt,providerUuid}`; `PATCH` takes uuid in body). The other 6 tabs (dashboard/projects/services/payments/settings/api-tokens/sign-in) have **NO Remnawave endpoint** — they're a **local node-assistant subsystem** (user chose "full local subsystem").
- **⚠️ SECURITY OVERRIDE (user-approved, module-scoped):** the API-tokens vault **persists hosting secrets encrypted** (Fernet, key = SHA-256 of `settings.encryption_key`). This intentionally overrides the project rule "no third-party secrets at rest" — ONLY for this module. Secrets are never returned to the client (DTOs expose a masked hint only, e.g. `sel-api****`).
- **Store** `services/infra_billing_store.py` — stdlib SQLite `DATA_DIR/infra_billing.db`, tables: `provider_meta(provider_uuid, balance, currency, low_balance_threshold, api_token_id, status)`, `node_meta`, `projects(id,name,description,node_uuids json,created_at)`, `services(id,name,kind,node_uuid,provider_uuid,project_id,billing_type[fixed|hourly],cost,next_billing_at,created_at)`, `payments(id,ts,provider_uuid,project_id,type[charge|topup|adjustment],amount,currency,status[success|pending|error],note)`, `api_tokens(id,name,provider_kind,secret_enc BLOB,created_at)`, `billing_settings(k,v)`. FX = RUB-anchored rates in settings; `_convert()` for base-currency aggregation.
- **Routes** `api/infra_billing.py` (`/api/infra-billing`): `GET /dashboard/summary` (total balance in base currency, burn-rate hourly/daily/monthly + daysLeft/critical<7, spend pie + monthly line); providers `GET/POST /providers`,`PATCH/DELETE /providers/{uuid}` (`?force=` cascade guard); projects/services CRUD; **payments** `GET/POST/DELETE` + **api-tokens** `GET/POST/DELETE` + `POST /api-tokens/{id}/verify` — both **session-gated**; `GET/PUT /settings`; `POST /auth/verify-session` (issues in-memory token; sent as `X-Billing-Session` header; gate active only when a PIN is set in settings). Notify hook `services/infra_notify.py` fired from dashboard.
- **Frontend** `components/infra/`: shared `api.ts` (typed fetch + `session` token in sessionStorage), `ui.tsx` (Page/PageHeader/Field/SelectField/Modal/fmt/`loadDeployNodes` from localStorage `deploy_jobs`), `Toast.tsx` (`<Toaster/>` in `App.tsx`), and 8 pages: `InfraDashboard` (inline-SVG donut+line, balance+burn widgets), `InfraProviders`, `InfraProjects` (card grid + node MultiSelect), `InfraServices` (table+modal), `InfraPayments` (ledger, filter, session-locked banner), `InfraSettings` (base currency, FX rates, threshold, refresh interval, PIN), `InfraSignIn` (PIN→token), `InfraApiTokens` (masked vault + «Проверить соединение»). Sidebar `Tab` union: `infra-dashboard|providers|projects|services|payments|settings|signin|tokens`; `InfraGroup` accordion.
- **Not implemented (stubs, documented):** live hosting-API balance verification & real FX feed & bot Anton — provider `status` and token `verify` are best-effort (secret decrypts; no per-hosting adapter); FX rates are manual; balances are entered manually.

## 5. Backend Routes
- **Xray-Checker:** `GET /api/checker/status|history|statuspage?ticks=N|incidents?days=N|logs`, `POST /api/checker/check|update|start|stop`; `POST /api/settings/xray-checker`.
- Settings: `GET /api/settings`, `POST /api/settings/{remnawave,optimization,deploy-defaults}`, `POST /api/settings/remnawave/check`.
- Remnawave proxies: `GET /api/remnawave/squads/internal`, `…/squads/external`, `GET /api/remnawave/node-plugins`, `GET /api/remnawave/nodes`.
- Templates CRUD: `/api/templates`. Traffic rules: `/api/traffic-rules` (+ `/{id}/sync`).
- Deploy: `POST /api/deploy`, `POST /api/deploy/stop`. Certs: `POST /api/certs/renew`.

## 6. Troubleshooting & Quirks (read before touching the pipeline)
- **Let's Encrypt rate limit:** issue **per-FQDN** certs, never the root wildcard (`root` + `*.root` is the SAME identifier set for every node → 5 certs/168h `429 rateLimited`). `$domaincert` is the FQDN now; the old `_root_domain()` helper was removed.
- **acme.sh `--list` is unreliable:** a stale/partial (or prior RSA) registry entry can show "issued" while `_ecc/*.cer` is missing → `--install-cert --ecc` fails (exit 2). Gate on the real files; `--force` only when absent; verify installed files are non-empty.
- **SECRET_KEY source:** `GET /api/keygen` `pubKey`, NOT `POST /api/nodes`. Manual token from the form is passed through unchanged.
- **Certbot ↔ remnanode mount:** remnanode already mounts `/etc/letsencrypt`; Docker rejects two mounts on one target. Step 10 **awk-replaces** the remnanode block's mount (scoped to the `remnanode:` service only; nginx mounts untouched) — idempotent.
- **WARP kills SSH if naive:** plain WARP/`wg-quick` injects a default route and drops the panel's SSH. Use wgcf with `Table = off`. `warp-cli` CLI changed in 2024 (`registration new`, `mode`, global `--accept-tos`) — we avoid it by using wgcf.
- **SSH port change = Dual-Port + reboot (current strategy):** Step 5 makes sshd listen on BOTH old+new ports, validates (`sshd -t`), then **cold-reboots** the server; Step 6 polls (20s + up to 90s) and decides Scenario А (new works → cleanup, keep new) / Б (only old works → rollback, FAILED) / В (lockout → FAILED). This survives the case where the new port fails to bind *after an OS restart* — which a same-session swap could not catch. (Superseded the earlier "parallel Session #2 test, never close Session #1" approach; an established TCP session survives a plain `sshd restart`, but NOT a reboot, hence the poll-and-reconnect design.) `reboot` is issued detached (`systemctl reboot --no-block`) so the run returns before the connection drops.
- **DeployForm prefill:** pass NO `initial` for new deploys, else the settings-overlay (`if (!initial)`) is skipped and email/Cloudflare/XHTTP stay empty.
- **Template substitution:** replace `$domaincert` BEFORE `$domain` (`$domain` is a prefix → would corrupt). Only system vars (`$domain`/`$domaincert`/`$path`/`$nodeport`/`$token`) are replaced; native nginx vars (`$http_upgrade`, `$proxy_add_x_forwarded_for`, …) must pass through untouched.
- **Bash via `bash -s`:** `$RANDOM`, `mapfile`, arrays work. In Python f-string scripts, literal braces need `{{}}`; keep `awk` programs in **non-f** strings to avoid brace clashes.
- **`apt-get update` always runs** at step 2 (fresh servers had stale package lists → "Unable to locate package").
- **HAProxy mode reuses step slot 7:** the frontend `DEPLOY_STEPS`/backend `STEP_LABELS` arrays are fixed (11); haproxy runs one step at index 7 (backend log label overridden via `_begin_step(task, 7, label=…)`, but the frontend card still derives its tiny step label from `DEPLOY_STEPS[6]` = "Cloudflare DNS + SSL" during the ~1 min install). Cosmetic only — on success the card shows 100%/SUCCESS. `mode == "remnanode"` requires domain/email/Cloudflare (they're now optional model fields defaulted to "" and gated in `DeployRequest.validate_by_mode`).

