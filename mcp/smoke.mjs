// Executable smoke test for the node-installer MCP HTTP transport.
// Spawns the built server, runs an MCP initialize + tools/list over Streamable
// HTTP (with the Bearer token), asserts both Remnawave and node-assistant tools
// are present, and that an unauthenticated request is rejected with 403.
//
// Run:  node smoke.mjs   (after `npm run build`)
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const PORT = 39217;
const TOKEN = 'smoke-token-abc';
const BASE = `http://127.0.0.1:${PORT}`;

const env = {
    ...process.env,
    REMNAWAVE_BASE_URL: 'http://127.0.0.1:9', // never actually called by tools/list
    REMNAWAVE_API_TOKEN: 'dummy',
    NODE_ASSISTANT_BASE_URL: 'http://127.0.0.1:9',
    NODE_ASSISTANT_TOKEN: 'dummy',
    MCP_HTTP_PORT: String(PORT),
    MCP_AUTH_TOKEN: TOKEN,
};

const child = spawn('node', ['dist/index.js'], { env, stdio: ['ignore', 'inherit', 'inherit'] });
let failed = false;
const fail = (m) => { console.error('FAIL:', m); failed = true; };

async function waitForHealth(tries = 50) {
    for (let i = 0; i < tries; i++) {
        try {
            const r = await fetch(`${BASE}/health`);
            if (r.ok) return true;
        } catch { /* not up yet */ }
        await sleep(100);
    }
    return false;
}

try {
    if (!(await waitForHealth())) throw new Error('server did not become healthy');

    // 1) Unauthenticated POST → 403.
    const noAuth = await fetch(`${BASE}/mcp`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }),
    });
    if (noAuth.status !== 403) fail(`expected 403 without token, got ${noAuth.status}`);
    else console.log('OK: unauthenticated request rejected with 403');

    // 2) Authenticated MCP session: initialize + tools/list via the SDK client.
    const client = new Client({ name: 'smoke', version: '1.0.0' });
    const transport = new StreamableHTTPClientTransport(new URL(`${BASE}/mcp`), {
        requestInit: { headers: { Authorization: `Bearer ${TOKEN}` } },
    });
    await client.connect(transport); // performs initialize
    console.log('OK: MCP initialize handshake succeeded');

    const { tools } = await client.listTools();
    const names = new Set(tools.map((t) => t.name));
    console.log(`OK: tools/list returned ${tools.length} tools`);

    if (!names.has('nodes_list')) fail('missing Remnawave tool nodes_list');
    else console.log('OK: Remnawave tool nodes_list present');

    if (!names.has('na_rules_list')) fail('missing node-assistant tool na_rules_list');
    else console.log('OK: node-assistant tool na_rules_list present');

    // Removed-on-bump tools must be gone.
    if (names.has('users_get_by_telegram_id')) fail('dropped tool users_get_by_telegram_id still registered');
    else console.log('OK: contract-dropped tool users_get_by_telegram_id removed');

    await client.close();
} catch (e) {
    fail(e?.message || String(e));
} finally {
    child.kill('SIGKILL');
}

await sleep(150);
if (failed) { console.error('\nSMOKE FAILED'); process.exit(1); }
console.log('\nSMOKE PASSED');
process.exit(0);
