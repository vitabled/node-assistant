# HAPROXY nav group — NodeFlow deploy + proxy integration

> User request (2026-07-25): «Интегрируй функции прикреплённой панели NodeFlow в новую
> группу разделов "HAPROXY"». Chosen architecture (AskUserQuestion): **Deploy + proxy** —
> node-installer registers/points at a NodeFlow panel instance per-account and surfaces its
> functions as native React sections under a new sidebar group **HAPROXY**, calling NodeFlow's
> `/api/v1/*` through an auth-injecting, per-account-isolated, SSRF-guarded backend proxy.
> The real NodeFlow agent + HAProxy engine is reused — nothing is reimplemented.

## What NodeFlow is (from the install kit)
Standalone Go panel + Postgres + mTLS PKI + a compiled node-agent binary + signed agent
releases. Nodes run a Go agent (over mTLS) that manages **HAProxy** TCP-relay routes. Panel
API surface (`/api/v1/*`, all behind admin auth):
- `GET /overview` (dashboard totals + top routes + traffic history)
- `GET/PATCH /settings` (panel settings)
- `POST /bootstrap`, `POST /bootstrap/host-key`, `GET /bootstrap/{job}` (SSH-install a node)
- `GET/POST /nodes`, `POST /nodes/order`
- per-node `{id}`: `GET/PATCH/DELETE`, `/operational`, `/audit`, `/traffic`(+`/history`),
  `/firewall`, `/haproxy` (enable/disable control), `/agent-update`(+`/rollback`),
  `/render-config`, `/routes`(GET/POST)+`/routes/order`, `/routes/{rid}`(GET/PATCH/DELETE)+
  `/routes/{rid}/traffic/history`, `/enrollment-tokens`, `/rotate-credentials`, `/reinstall`,
  `/config-revisions`(+`/from-routes`,`/{rev}`), `/config-state`, `/desired-revision`
- `GET/POST /agent-releases`, `/agent-releases/signing-key`, `GET/DELETE /agent-releases/{id}`

**Auth (critical):** `/api/v1/*` accepts `Authorization: Bearer <PANEL_ADMIN_TOKEN>` and the
bearer path SKIPS the same-origin / cookie / CSRF checks (browser sessions need those; a
server-side bearer does not). So a backend proxy that injects the admin token forwards every
function cleanly — no session dance. (Verified in `internal/panel/http.go::admin()`.)

## Architecture
- **Per-account config** `HaproxyConfig` on `AppSettings`: `enabled`, `base_url` (NodeFlow URL),
  `admin_token_enc` (Fernet, like MCP/cliproxy — an infra-control secret, never returned raw).
  Isolation = each account registers its own NodeFlow instance; the proxy injects that
  account's token server-side and never exposes it.
- **`services/nodeflow_client.py`** — httpx client: `request(method, subpath, ...)` →
  `{base_url}/api/v1/{subpath}` with `Authorization: Bearer <token>`, `follow_redirects=False`,
  SSRF-guarded (`net_guard.is_safe_url`) at register-time AND per call. `health()` → `/healthz`.
- **`api/haproxy.py`** (under `require_account`): `GET/POST /api/haproxy/config`,
  `POST /api/haproxy/test`, and a **generic proxy** `ANY /api/haproxy/proxy/{path:path}` that
  forwards method+query+body to NodeFlow `/api/v1/{path}` (raw-body passthrough → JSON *and*
  multipart agent-release upload both work). Only the `/api/v1/` prefix is reachable.
- **Frontend** `components/haproxy/*` — new sidebar group **HAPROXY** with sections
  Обзор · Ноды · Маршруты · Трафик · Файрвол · Релизы · Настройки, native pages styled with the
  project's CSS-var tokens, calling `/api/haproxy/proxy/*` through the global fetch interceptor
  (account bearer added automatically). Contract types ported from NodeFlow's `contracts.ts`.

## Phases
- **Ф1 (backend):** `HaproxyConfig` + Fernet helpers + `nodeflow_client.py` + `api/haproxy.py`
  (config/test/generic-proxy) + wire `main.py` + tests. Verify `pytest`.
- **Ф2 (frontend shell + connect):** Sidebar HAPROXY group, `Tab` union, `App.tsx` routes+CRUMB,
  `haproxy/api.ts` + `haproxy/contracts.ts`, «Настройки/Подключение» page (base_url+admin token,
  test connection, enable). Verify `tsc`.
- **Ф3 (Overview + Nodes):** Обзор dashboard, Ноды table + Add-node (bootstrap host-key→install)
  dialog, node detail (heartbeat KPIs, HAProxy enable/disable control, routes list).
- **Ф4 (Routes + Traffic + Firewall + Releases):** route create/edit editor (listener/match/
  target/health/proxy-proto/quota/custom-fragment), traffic page, firewall policy, agent releases.

## Deviations / notes
- **Register-by-URL is the MVP** for "deploy"; the account stands up NodeFlow with the install
  kit (its own postgres+PKI+signing key), then registers URL+admin-token here. A future phase can
  add an SSH-deploy pipeline that runs `scripts/install-panel.sh` on a target — deferred (heavy:
  uploads the 300 KB source + 7.7 MB agent binary + generates PKI). The proxy delivers all the
  functions regardless of how NodeFlow was stood up.
- Admin token is Fernet-encrypted (infra-control secret), consistent with MCP/cliproxy vaults.
- The generic proxy avoids enumerating ~30 endpoints and auto-covers new NodeFlow versions.
