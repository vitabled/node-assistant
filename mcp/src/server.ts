import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { RemnawaveClient } from './client/index.js';
import { Config } from './config.js';
import { registerAllTools } from './tools/index.js';
import { registerAllResources } from './resources/index.js';
import { registerAllPrompts } from './prompts/index.js';
import {
    NodeAssistantClient,
    registerNodeAssistantTools,
} from './tools/node-assistant.js';

export function createServer(config: Config): McpServer {
    const server = new McpServer({
        name: 'node-installer-mcp',
        version: '2.0.0',
    });

    const client = new RemnawaveClient(config);

    registerAllTools(server, client, config.readonly);
    registerAllResources(server, client);
    registerAllPrompts(server);

    // node-assistant panel tools (read-only) — only when the panel is configured.
    if (config.nodeAssistant) {
        const na = new NodeAssistantClient(
            config.nodeAssistant.baseUrl,
            config.nodeAssistant.token,
        );
        registerNodeAssistantTools(server, na);
    }

    return server;
}
