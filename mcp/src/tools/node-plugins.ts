import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { RemnawaveClient } from '../client/index.js';
import { toolResult, toolError } from './helpers.js';

export function registerNodePluginTools(server: McpServer, client: RemnawaveClient, readonly: boolean) {
    server.tool('node_plugins_list', 'List all node plugins', {}, async () => {
        try { return toolResult(await client.getNodePlugins()); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_get', 'Get a node plugin by UUID', {
        uuid: z.string().describe('Plugin UUID'),
    }, async ({ uuid }) => {
        try { return toolResult(await client.getNodePlugin(uuid)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_torrent_reports', 'Get torrent blocker reports', {}, async () => {
        try { return toolResult(await client.getTorrentBlockerReports()); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_torrent_stats', 'Get torrent blocker statistics', {}, async () => {
        try { return toolResult(await client.getTorrentBlockerStats()); } catch (e) { return toolError(e); }
    });

    if (readonly) return;

    server.tool('node_plugins_create', 'Create a new node plugin', {
        name: z.string().describe('Plugin name'),
    }, async (params) => {
        try { return toolResult(await client.createNodePlugin(params)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_update', 'Update a node plugin', {
        uuid: z.string().describe('Plugin UUID'),
        name: z.string().optional().describe('New name'),
    }, async (params) => {
        try { return toolResult(await client.updateNodePlugin(params)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_delete', 'Delete a node plugin', {
        uuid: z.string().describe('Plugin UUID'),
    }, async ({ uuid }) => {
        try { await client.deleteNodePlugin(uuid); return toolResult({ success: true, message: `Plugin ${uuid} deleted` }); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_reorder', 'Reorder node plugins', {
        uuids: z.array(z.string()).describe('Ordered array of plugin UUIDs'),
    }, async (params) => {
        try { return toolResult(await client.reorderNodePlugins(params)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_clone', 'Clone a node plugin', {
        uuid: z.string().describe('Plugin UUID to clone'),
    }, async (params) => {
        try { return toolResult(await client.cloneNodePlugin(params)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_execute', 'Execute a node plugin', {
        uuid: z.string().describe('Plugin UUID to execute'),
    }, async (params) => {
        try { return toolResult(await client.executeNodePlugin(params)); } catch (e) { return toolError(e); }
    });

    server.tool('node_plugins_torrent_truncate', 'Truncate all torrent blocker reports', {}, async () => {
        try { return toolResult(await client.truncateTorrentBlockerReports()); } catch (e) { return toolError(e); }
    });
}
