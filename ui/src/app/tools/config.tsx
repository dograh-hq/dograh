"use client";

import { Cog, Globe, type LucideIcon, PhoneForwarded, PhoneOff, Puzzle } from "lucide-react";
import { type ReactNode } from "react";

export type ToolCategory = "http_api" | "end_call" | "transfer_call" | "native" | "integration";

export type EndCallMessageType = "none" | "custom";

export interface ToolCategoryConfig {
    value: ToolCategory;
    label: string;
    description: string;
    icon: LucideIcon;
    iconName: string; // String name for storing in database
    iconColor: string;
    disabled?: boolean;
    autoFill?: {
        name: string;
        description: string;
    };
}

export const TOOL_CATEGORIES: ToolCategoryConfig[] = [
    {
        value: "http_api",
        label: "External HTTP API",
        description: "Make HTTP requests to external APIs",
        icon: Globe,
        iconName: "globe",
        iconColor: "#3B82F6",
    },
    {
        value: "end_call",
        label: "End Call",
        description: "End the call when conditions are met",
        icon: PhoneOff,
        iconName: "phone-off",
        iconColor: "#EF4444",
        autoFill: {
            name: "End Call",
            description: "End the call when either user asks to disconnect the call, or when you believe its time to end the conversation",
        },
    },
    {
        value: "transfer_call",
        label: "Transfer Call",
        description: "Transfer the call to another phone number (Twilio only)",
        icon: PhoneForwarded,
        iconName: "phone-forwarded",
        iconColor: "#10B981",
        autoFill: {
            name: "Transfer Call",
            description: "Transfer the caller to another phone number when requested",
        },
    },
    {
        value: "native",
        label: "Native (Coming Soon)",
        description: "Built-in tools like call transfer, DTMF input",
        icon: Cog,
        iconName: "cog",
        iconColor: "#6B7280",
        disabled: true,
    },
    {
        value: "integration",
        label: "Integration (Coming Soon)",
        description: "Third-party integrations like Google Calendar",
        icon: Puzzle,
        iconName: "puzzle",
        iconColor: "#8B5CF6",
        disabled: true,
    },
];

export function getCategoryConfig(category: ToolCategory): ToolCategoryConfig | undefined {
    return TOOL_CATEGORIES.find(c => c.value === category);
}

export function getToolIcon(category: string): LucideIcon {
    const config = TOOL_CATEGORIES.find(c => c.value === category);
    return config?.icon ?? Globe;
}

export function getToolIconColor(category: string, fallbackColor?: string): string {
    const config = TOOL_CATEGORIES.find(c => c.value === category);
    return config?.iconColor ?? fallbackColor ?? "#3B82F6";
}

export function renderToolIcon(category: string, className: string = "w-5 h-5 text-white"): ReactNode {
    const Icon = getToolIcon(category);
    return <Icon className={className} />;
}

export function getToolTypeLabel(category: string): string {
    switch (category) {
        case "end_call":
            return "End Call Tool";
        case "transfer_call":
            return "Transfer Call Tool";
        case "http_api":
            return "HTTP API Tool";
        case "native":
            return "Native Tool";
        case "integration":
            return "Integration Tool";
        default:
            return "Tool";
    }
}

// End Call tool specific configuration
export interface EndCallConfig {
    messageType: EndCallMessageType;
    customMessage?: string;
}

export const DEFAULT_END_CALL_CONFIG: EndCallConfig = {
    messageType: "none",
    customMessage: "",
};

// Transfer Call tool specific configuration
export interface TransferCallConfig {
    destination: string;
    messageType: EndCallMessageType; // Reuse the same type
    customMessage?: string;
    timeout: number;
}

export const DEFAULT_TRANSFER_CALL_CONFIG: TransferCallConfig = {
    destination: "",
    messageType: "none",
    customMessage: "",
    timeout: 30,
};

// Tool definition types for different categories
export interface HttpApiToolDefinition {
    schema_version: number;
    type: "http_api";
    config: {
        method: string;
        url: string;
        headers?: Record<string, string>;
        credential_uuid?: string;
        parameters?: Array<{
            name: string;
            type: string;
            description: string;
            required: boolean;
        }>;
        timeout_ms?: number;
    };
}

export interface EndCallToolDefinition {
    schema_version: number;
    type: "end_call";
    config: EndCallConfig;
}

export interface TransferCallToolDefinition {
    schema_version: number;
    type: "transfer_call";
    config: TransferCallConfig;
}

export type ToolDefinition = HttpApiToolDefinition | EndCallToolDefinition | TransferCallToolDefinition;

export function createEndCallDefinition(config: EndCallConfig): EndCallToolDefinition {
    return {
        schema_version: 1,
        type: "end_call",
        config,
    };
}

export function createTransferCallDefinition(config: TransferCallConfig): TransferCallToolDefinition {
    return {
        schema_version: 1,
        type: "transfer_call",
        config,
    };
}

export function createHttpApiDefinition(): HttpApiToolDefinition {
    return {
        schema_version: 1,
        type: "http_api",
        config: {
            method: "POST",
            url: "",
        },
    };
}

export function createToolDefinition(category: ToolCategory): ToolDefinition {
    switch (category) {
        case "end_call":
            return createEndCallDefinition(DEFAULT_END_CALL_CONFIG);
        case "transfer_call":
            return createTransferCallDefinition(DEFAULT_TRANSFER_CALL_CONFIG);
        case "http_api":
        default:
            return createHttpApiDefinition();
    }
}
