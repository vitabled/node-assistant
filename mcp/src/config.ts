export interface Config {
    baseUrl: string;
    apiToken: string;
    apiKey?: string;
    readonly: boolean;
    // node-assistant panel (our own backend, JWT Bearer = require_account).
    nodeAssistant?: {
        baseUrl: string;
        token: string;
    };
    // HTTP/SSE transport (in addition to stdio) for external clients.
    http?: {
        port: number;
        authToken: string;
    };
}

export function loadConfig(): Config {
    const baseUrl = process.env.REMNAWAVE_BASE_URL;
    const apiToken = process.env.REMNAWAVE_API_TOKEN;
    const apiKey = process.env.REMNAWAVE_API_KEY;
    const readonly = process.env.REMNAWAVE_READONLY === 'true';

    if (!baseUrl) {
        throw new Error('REMNAWAVE_BASE_URL environment variable is required');
    }
    if (!apiToken) {
        throw new Error('REMNAWAVE_API_TOKEN environment variable is required');
    }

    const naBase = process.env.NODE_ASSISTANT_BASE_URL;
    const naToken = process.env.NODE_ASSISTANT_TOKEN;
    const nodeAssistant =
        naBase && naToken
            ? { baseUrl: naBase.replace(/\/+$/, ''), token: naToken }
            : undefined;

    const httpPort = process.env.MCP_HTTP_PORT;
    const httpToken = process.env.MCP_AUTH_TOKEN;
    const http =
        httpPort && httpToken
            ? { port: parseInt(httpPort, 10), authToken: httpToken }
            : undefined;

    return {
        baseUrl: baseUrl.replace(/\/+$/, ''),
        apiToken,
        apiKey,
        readonly,
        nodeAssistant,
        http,
    };
}
