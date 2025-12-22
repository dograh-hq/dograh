export enum NodeType {
    START_CALL = 'startCall',
    AGENT_NODE = 'agentNode',
    END_CALL = 'endCall',
    GLOBAL_NODE = 'globalNode',
    TRIGGER = 'trigger',
    WEBHOOK = 'webhook'
}

export type FlowNodeData = {
    prompt: string;
    name: string;
    is_start?: boolean;
    is_static?: boolean;
    is_end?: boolean;
    invalid?: boolean;
    validationMessage?: string | null;
    selected_through_edge?: boolean;
    hovered_through_edge?: boolean;
    allow_interrupt?: boolean;
    extraction_enabled?: boolean;
    extraction_prompt?: string;
    extraction_variables?: ExtractionVariable[];
    add_global_prompt?: boolean;
    wait_for_user_greeting?: boolean;
    detect_voicemail?: boolean;
    delayed_start?: boolean;
    delayed_start_duration?: number;
    // Trigger node specific
    trigger_path?: string;
    // Webhook node specific
    enabled?: boolean;
    http_method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    endpoint_url?: string;
    credential_uuid?: string;
    custom_headers?: Array<{ key: string; value: string }>;
    payload_template?: Record<string, unknown>;
    retry_config?: {
        enabled: boolean;
        max_retries: number;
        retry_delay_seconds: number;
    };
}

export type FlowNode = {
    id: string;
    type: string;
    position: { x: number; y: number };
    data: FlowNodeData;
    measured?: {
        width: number;
        height: number;
    };
    selected?: boolean;
    dragging?: boolean;
};

export type FlowEdgeData = {
    condition: string;
    label: string;
    invalid?: boolean;
    validationMessage?: string | null;
}

export type FlowEdge = {
    id: string;
    source: string;
    target: string;
    type?: string;
    data: FlowEdgeData;
    animated?: boolean;
    invalid?: boolean;
};

export interface WorkflowDefinition {
    nodes: FlowNode[];
    edges: FlowEdge[];
    viewport: {
        x: number;
        y: number;
        zoom: number;
    };
}

export interface WorkflowData {
    name: string;
    workflow_definition: WorkflowDefinition;
}

export type WorkflowValidationError = {
    kind: 'node' | 'edge' | 'workflow';
    id: string;
    field: string;
    message: string;
}

export type ExtractionVariable = {
    name: string;
    type: 'string' | 'number' | 'boolean';
    prompt?: string;
};

// Credential types for webhook authentication
export type CredentialType = 'none' | 'api_key' | 'bearer_token' | 'basic_auth' | 'custom_header';

export interface Credential {
    uuid: string;
    name: string;
    description?: string;
    credential_type: CredentialType;
    created_at: string;
    updated_at: string;
}

