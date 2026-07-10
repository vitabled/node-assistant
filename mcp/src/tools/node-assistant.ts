import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { toolResult, toolError } from './helpers.js';

// Thin JWT-Bearer client for our own node-installer backend (require_account).
// Only read-only endpoints are exposed — the MCP surface into the panel is
// observational (status, stats, catalogs), never mutating.
export class NodeAssistantClient {
    private baseUrl: string;
    private token: string;

    constructor(baseUrl: string, token: string) {
        this.baseUrl = baseUrl;
        this.token = token;
    }

    async get(path: string): Promise<unknown> {
        const res = await fetch(`${this.baseUrl}${path}`, {
            headers: { Authorization: `Bearer ${this.token}` },
        });
        if (!res.ok) {
            let msg: string;
            try {
                const body = (await res.json()) as { detail?: unknown };
                msg =
                    typeof body.detail === 'string'
                        ? body.detail
                        : JSON.stringify(body.detail ?? body);
            } catch {
                msg = `HTTP ${res.status} ${res.statusText}`;
            }
            throw new Error(`node-assistant API error: ${msg}`);
        }
        return res.json();
    }
}

const HOURS = z.number().int().min(1).max(720).optional().describe('Time window in hours (1–720)');

export function registerNodeAssistantTools(server: McpServer, client: NodeAssistantClient) {
    const read = (name: string, desc: string, path: () => string) =>
        server.tool(name, desc, {}, async () => {
            try {
                return toolResult(await client.get(path()));
            } catch (e) {
                return toolError(e);
            }
        });

    read('na_rules_list', 'List node-installer automation rules (Ф1/Ф2 rules engine)', () => '/api/rules');
    read('na_subscriptions_status', 'List tracked subscriptions with live per-sub status', () => '/api/subscriptions/status');
    read('na_domains_list', 'List managed SSL domains', () => '/api/domains');
    read('na_host_templates_list', 'List local Remnawave host templates', () => '/api/hosts');
    read('na_infra_summary', 'Infra-billing dashboard summary (balance / burn-rate)', () => '/api/infra-billing/dashboard/summary');

    server.tool(
        'na_checker_statuspage',
        'Node health status page (uptime bars, per-node online/latency) from xray-checker',
        { ticks: z.number().int().min(1).max(90).optional().describe('Uptime bars to return (1–90)') },
        async ({ ticks }) => {
            try {
                return toolResult(await client.get(`/api/checker/statuspage?ticks=${ticks ?? 30}`));
            } catch (e) {
                return toolError(e);
            }
        },
    );

    server.tool(
        'na_checker_incidents',
        'Recent node availability incidents',
        { days: z.number().int().min(1).max(30).optional().describe('Look-back window in days (1–30)') },
        async ({ days }) => {
            try {
                return toolResult(await client.get(`/api/checker/incidents?days=${days ?? 7}`));
            } catch (e) {
                return toolError(e);
            }
        },
    );

    server.tool(
        'na_node_load',
        'Per-node user-load history (usersOnline over time, busiest-first)',
        { hours: HOURS },
        async ({ hours }) => {
            try {
                return toolResult(await client.get(`/api/stats/users/node-load?hours=${hours ?? 24}`));
            } catch (e) {
                return toolError(e);
            }
        },
    );

    server.tool(
        'na_top_users',
        'Top users by traffic usage',
        { hours: HOURS },
        async ({ hours }) => {
            try {
                return toolResult(await client.get(`/api/stats/users/top-users?hours=${hours ?? 24}`));
            } catch (e) {
                return toolError(e);
            }
        },
    );
}
