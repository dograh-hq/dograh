"use client";

import { Calculator, Cog, Globe, type LucideIcon, PhoneForwarded, PhoneOff, Puzzle } from "lucide-react";
import { type ReactNode } from "react";

import type {
    CalculatorToolDefinition,
    EndCallConfig,
    EndCallToolDefinition,
    HttpApiToolDefinition,
    TransferCallConfig,
    TransferCallToolDefinition,
} from "@/client/types.gen";

export type ToolCategory = "http_api" | "end_call" | "transfer_call" | "calculator" | "native" | "integration";

export type EndCallMessageType = "none" | "custom" | "audio";

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
        value: "calculator",
        label: "Calculator",
        description: "Built-in calculator for arithmetic operations",
        icon: Calculator,
        iconName: "calculator",
        iconColor: "#F59E0B",
        autoFill: {
            name: "Calculator",
            description: "Perform arithmetic calculations (supports +, -, *, /, **, %, and parentheses)",
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
        case "calculator":
            return "Calculator Tool";
        case "native":
            return "Native Tool";
        case "integration":
            return "Integration Tool";
        default:
            return "Tool";
    }
}

export const DEFAULT_END_CALL_REASON_DESCRIPTION =
    "The reason for ending the call (e.g., 'voicemail_detected', 'issue_resolved', 'customer_requested')";

export const DEFAULT_END_CALL_CONFIG: EndCallConfig = {
    messageType: "none",
    customMessage: "",
    endCallReason: false,
};

export const DEFAULT_TRANSFER_CALL_CONFIG: TransferCallConfig = {
    destination: "",
    messageType: "none",
    customMessage: "",
    timeout: 30,
};

export type ToolDefinition = HttpApiToolDefinition | EndCallToolDefinition | TransferCallToolDefinition | CalculatorToolDefinition;

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

export function createCalculatorDefinition(): CalculatorToolDefinition {
    return {
        schema_version: 1,
        type: "calculator",
    };
}

export function createToolDefinition(category: ToolCategory): ToolDefinition {
    switch (category) {
        case "end_call":
            return createEndCallDefinition(DEFAULT_END_CALL_CONFIG);
        case "transfer_call":
            return createTransferCallDefinition(DEFAULT_TRANSFER_CALL_CONFIG);
        case "calculator":
            return createCalculatorDefinition();
        case "http_api":
        default:
            return createHttpApiDefinition();
    }
}
