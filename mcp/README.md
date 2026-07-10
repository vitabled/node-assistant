# node-installer MCP server

MCP (Model Context Protocol) server exposing **Remnawave** panel management plus
**node-installer** panel observability to MCP clients and the built-in AI agent.

Fork of [TrackLine/mcp-remnawave](https://github.com/TrackLine/mcp-remnawave) (MIT).
Changes in this fork:

- Bumped `@remnawave/backend-contract` `^2.6.27` → `^2.9.14` and fixed the
  resulting contract breakages (`USERS.GET_BY.{TELEGRAM_ID,EMAIL,TAG,SUBSCRIPTION_UUID}`
  and `HOSTS.BULK.{SET_INBOUND,SET_PORT}` were removed; the top-level `IP_CONTROL`
  routes were renamed to `CONNECTIONS`).
- Added **node-assistant tools** (`src/tools/node-assistant.ts`) — read-only calls
  into our own backend (`NODE_ASSISTANT_BASE_URL` + JWT `NODE_ASSISTANT_TOKEN`):
  automation rules, checker status/incidents, node load, top users, subscriptions,
  domains, host templates, infra-billing summary.
- Added a **Streamable HTTP transport** (`MCP_HTTP_PORT` + Bearer `MCP_AUTH_TOKEN`)
  alongside the original stdio transport, for external clients / the agent.

## Transports

- **stdio** (default): run with only the Remnawave env vars set.
- **HTTP** (`MCP_HTTP_PORT` + `MCP_AUTH_TOKEN` set): Streamable HTTP at `/mcp`
  (session-based, `Bearer` token required; `403` without it). `/health` is
  unauthenticated for orchestrator liveness checks.

## Read-only mode

`REMNAWAVE_READONLY=true` registers only read/list Remnawave tools. node-assistant
tools are always read-only.

## Develop

```
npm install
npm run build        # tsup → dist/
npm run typecheck    # tsc --noEmit
node smoke.mjs       # executable HTTP smoke (initialize + tools/list + 403)
```

See `.env.example` for all variables.
