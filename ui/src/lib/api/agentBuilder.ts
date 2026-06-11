/**
 * Hand-written client for the agent-builder endpoints.
 *
 * The generated SDK (`src/client/`) is produced from the backend OpenAPI spec
 * via `npm run generate-client`, which needs a running api. Until the client
 * is regenerated, these thin wrappers call the same configured hey-api client
 * instance the SDK uses (base URL + interceptors), so behavior is identical.
 */

import { client } from '@/client/client.gen';

export interface AgentTemplate {
    id: string;
    name: string;
    description: string;
    fields: string[];
}

export interface BusinessInfo {
    name: string;
    industry?: string;
    details?: string;
    language?: string;
}

export interface CreateAgentRequest {
    mode: 'describe' | 'template';
    description?: string;
    template_id?: string;
    business: BusinessInfo;
}

export interface CreateAgentResponse {
    workflow_id: number;
    name: string;
}

function authHeaders(accessToken: string): Record<string, string> {
    return { Authorization: `Bearer ${accessToken}` };
}

export async function listAgentTemplates(accessToken: string): Promise<AgentTemplate[]> {
    const result = await client.get({
        url: '/api/v1/agent-builder/templates',
        headers: authHeaders(accessToken),
    });
    if (result.error || !result.data) {
        throw new Error('Failed to load agent templates');
    }
    return result.data as AgentTemplate[];
}

export async function createAgent(
    request: CreateAgentRequest,
    accessToken: string,
): Promise<CreateAgentResponse> {
    const result = await client.post({
        url: '/api/v1/agent-builder/create',
        body: request,
        headers: {
            'Content-Type': 'application/json',
            ...authHeaders(accessToken),
        },
    });
    if (result.error || !result.data) {
        const detail = (result.error as { detail?: unknown } | undefined)?.detail;
        throw new Error(
            typeof detail === 'string' ? detail : 'Failed to create the agent',
        );
    }
    return result.data as CreateAgentResponse;
}
