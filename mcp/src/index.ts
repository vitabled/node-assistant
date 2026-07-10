import { randomUUID } from 'node:crypto';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import express from 'express';
import { loadConfig } from './config.js';
import { createServer } from './server.js';

const config = loadConfig();

if (config.http) {
    // Session-based Streamable HTTP transport (canonical SDK pattern): an
    // `initialize` request mints a session; subsequent requests carry the
    // `mcp-session-id` header. Gated by a Bearer token. This is what external MCP
    // clients and the built-in agent (Ф4) connect to.
    const { port, authToken } = config.http;
    const app = express();
    app.use(express.json({ limit: '4mb' }));

    const transports: Record<string, StreamableHTTPServerTransport> = {};

    // Unauthenticated liveness probe (used by the backend orchestrator).
    app.get('/health', (_req, res) => {
        res.json({ status: 'ok', transport: 'http' });
    });

    const authed = (req: express.Request): boolean => {
        const h = req.header('authorization') || '';
        const token = h.startsWith('Bearer ') ? h.slice(7) : '';
        return !!token && token === authToken;
    };
    const forbidden = (res: express.Response) =>
        res.status(403).json({
            jsonrpc: '2.0',
            error: { code: -32001, message: 'Forbidden: invalid or missing MCP auth token' },
            id: null,
        });

    app.post('/mcp', async (req, res) => {
        if (!authed(req)) return void forbidden(res);
        const sessionId = req.header('mcp-session-id');
        let transport: StreamableHTTPServerTransport | undefined =
            sessionId ? transports[sessionId] : undefined;

        if (!transport) {
            if (sessionId || !isInitializeRequest(req.body)) {
                res.status(400).json({
                    jsonrpc: '2.0',
                    error: { code: -32000, message: 'Bad Request: no valid session (send initialize first)' },
                    id: null,
                });
                return;
            }
            // New session on initialize.
            transport = new StreamableHTTPServerTransport({
                sessionIdGenerator: () => randomUUID(),
                onsessioninitialized: (sid) => {
                    transports[sid] = transport as StreamableHTTPServerTransport;
                },
            });
            transport.onclose = () => {
                if (transport && transport.sessionId) delete transports[transport.sessionId];
            };
            const server = createServer(config);
            await server.connect(transport);
        }
        await transport.handleRequest(req, res, req.body);
    });

    // GET = server→client SSE stream; DELETE = terminate session. Both by id.
    const bySession = async (req: express.Request, res: express.Response) => {
        if (!authed(req)) return void forbidden(res);
        const sessionId = req.header('mcp-session-id');
        const transport = sessionId ? transports[sessionId] : undefined;
        if (!transport) {
            res.status(400).send('Invalid or missing session ID');
            return;
        }
        await transport.handleRequest(req, res);
    };
    app.get('/mcp', bySession);
    app.delete('/mcp', bySession);

    app.listen(port, () => {
        // stderr so it never corrupts a stdio JSON-RPC stream elsewhere.
        console.error(`node-installer-mcp HTTP transport listening on :${port}`);
    });
} else {
    // Default: stdio transport (local CLI / desktop MCP client).
    const server = createServer(config);
    const transport = new StdioServerTransport();
    await server.connect(transport);
}
