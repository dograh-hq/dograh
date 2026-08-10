import { Check, Copy, ExternalLink, Loader2, MessageCircle, Mic, Plus, Rocket, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
    createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost,
    deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete,
    getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet,
} from "@/client/sdk.gen";
import type { TextChatInactivityTimeoutConstraints } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { WIDGET_CONTEXT_DOC_URL, WIDGET_MODE_DOCUMENTATION_URLS } from "@/constants/documentation";
import { HEADLESS_CHAT_EXAMPLE } from "@/constants/embedExamples";
import { detailFromError } from "@/lib/apiError";
import { copyTextToClipboard } from "@/lib/clipboard";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

interface EmbedDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    workflowName: string;
    workflowConfigurations: WorkflowConfigurations;
    textChatInactivityTimeoutConstraints: TextChatInactivityTimeoutConstraints | null;
    onSaveWorkflowConfigurations: (
        configurations: WorkflowConfigurations,
        workflowName: string,
    ) => Promise<void>;
}

type WidgetType = "voice" | "chat";

// Per-type defaults, swapped on toggle only when the user hasn't customized
// the text (i.e. it still equals the other type's default).
const WIDGET_TYPE_DEFAULTS: Record<WidgetType, { buttonText: string; callToActionText: string }> = {
    voice: {
        buttonText: "Talk to Agent",
        callToActionText: "Click to start voice conversation",
    },
    chat: {
        buttonText: "Chat with Agent",
        callToActionText: "Click to start chatting",
    },
};

const SECONDS_PER_MINUTE = 60;

interface EmbedToken {
    id: number;
    token: string;
    allowed_domains: string[] | null;
    settings: Record<string, unknown> | null;
    is_active: boolean;
    usage_count: number;
    usage_limit: number | null;
    expires_at: string | null;
    created_at: string;
    embed_script: string;
}

export function EmbedDialog({
    open,
    onOpenChange,
    workflowId,
    workflowName,
    workflowConfigurations,
    textChatInactivityTimeoutConstraints,
    onSaveWorkflowConfigurations,
}: EmbedDialogProps) {
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [embedToken, setEmbedToken] = useState<EmbedToken | null>(null);
    const [copied, setCopied] = useState(false);

    // Form state
    const [isEnabled, setIsEnabled] = useState(false);
    const [domains, setDomains] = useState<string[]>([]);
    const [newDomain, setNewDomain] = useState("");
    const [widgetType, setWidgetType] = useState<WidgetType>("voice");
    const [embedMode, setEmbedMode] = useState<"floating" | "inline" | "headless">("floating");
    const [position, setPosition] = useState("bottom-right");
    const [buttonText, setButtonText] = useState("Talk to Agent");
    const [buttonColor, setButtonColor] = useState("#10b981");
    const [callToActionText, setCallToActionText] = useState("Click to start voice conversation");
    const configuredTextChatInactivitySeconds =
        workflowConfigurations.text_chat_inactivity_timeout_seconds
        ?? textChatInactivityTimeoutConstraints?.default_seconds;
    const [textChatInactivityMinutes, setTextChatInactivityMinutes] = useState(() =>
        configuredTextChatInactivitySeconds === undefined
            ? ""
            : String(configuredTextChatInactivitySeconds / SECONDS_PER_MINUTE),
    );

    const parsedTextChatInactivityMinutes = Number(textChatInactivityMinutes);
    const parsedTextChatInactivitySeconds =
        parsedTextChatInactivityMinutes * SECONDS_PER_MINUTE;
    const minimumTextChatInactivitySeconds =
        textChatInactivityTimeoutConstraints?.minimum_seconds;
    const maximumTextChatInactivitySeconds =
        textChatInactivityTimeoutConstraints?.maximum_seconds;
    const minimumTextChatInactivityMinutes = minimumTextChatInactivitySeconds !== undefined
        ? minimumTextChatInactivitySeconds / SECONDS_PER_MINUTE
        : undefined;
    const maximumTextChatInactivityMinutes = maximumTextChatInactivitySeconds !== undefined
        ? maximumTextChatInactivitySeconds / SECONDS_PER_MINUTE
        : undefined;
    const hasTextChatInactivityBounds =
        minimumTextChatInactivitySeconds !== undefined &&
        maximumTextChatInactivitySeconds !== undefined;
    const textChatInactivityIsValid =
        textChatInactivityMinutes.trim() !== "" &&
        Number.isInteger(parsedTextChatInactivityMinutes) &&
        (!hasTextChatInactivityBounds ||
            (parsedTextChatInactivitySeconds >=
                minimumTextChatInactivitySeconds &&
                parsedTextChatInactivitySeconds <=
                    maximumTextChatInactivitySeconds));
    const textChatInactivityValidationMessage = hasTextChatInactivityBounds
        ? `Chat inactivity timeout must be a whole number between ${minimumTextChatInactivityMinutes} and ${maximumTextChatInactivityMinutes} minutes`
        : "Chat inactivity timeout must be a whole number of minutes";

    const handleWidgetTypeChange = (type: WidgetType) => {
        if (type === widgetType) return;
        const from = WIDGET_TYPE_DEFAULTS[widgetType];
        const to = WIDGET_TYPE_DEFAULTS[type];
        if (buttonText === from.buttonText) setButtonText(to.buttonText);
        if (callToActionText === from.callToActionText) setCallToActionText(to.callToActionText);
        setWidgetType(type);
    };

    const loadEmbedToken = useCallback(async () => {
        setLoading(true);
        try {
            const response = await getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet({
                path: { workflow_id: workflowId },
            });

            if (response.data) {
                setEmbedToken(response.data as EmbedToken);
                setIsEnabled(response.data.is_active);

                // Load settings
                if (response.data.settings) {
                    const settings = response.data.settings as Record<string, string>;
                    const loadedType: WidgetType = settings.widgetType === "chat" ? "chat" : "voice";
                    setWidgetType(loadedType);
                    setEmbedMode((settings.embedMode as "floating" | "inline" | "headless") || "floating");
                    setPosition(settings.position || "bottom-right");
                    setButtonText(settings.buttonText || WIDGET_TYPE_DEFAULTS[loadedType].buttonText);
                    setButtonColor(settings.buttonColor || "#10b981");
                    setCallToActionText(settings.callToActionText || WIDGET_TYPE_DEFAULTS[loadedType].callToActionText);
                }

                // Load domains
                if (response.data.allowed_domains) {
                    setDomains(response.data.allowed_domains);
                }
            }
        } catch (error) {
            console.error("Failed to load embed token:", error);
        } finally {
            setLoading(false);
        }
    }, [workflowId]);

    useEffect(() => {
        if (open) {
            loadEmbedToken();
            setTextChatInactivityMinutes(
                configuredTextChatInactivitySeconds === undefined
                    ? ""
                    : String(configuredTextChatInactivitySeconds / SECONDS_PER_MINUTE),
            );
        }
    }, [open, loadEmbedToken, configuredTextChatInactivitySeconds]);

    const handleSave = async () => {
        if (isEnabled && widgetType === "chat" && !textChatInactivityIsValid) {
            toast.error(textChatInactivityValidationMessage);
            return;
        }

        setSaving(true);
        try {
            if (isEnabled && widgetType === "chat") {
                await onSaveWorkflowConfigurations(
                    {
                        ...workflowConfigurations,
                        text_chat_inactivity_timeout_seconds: parsedTextChatInactivitySeconds,
                    },
                    workflowName,
                );
            }

            if (!isEnabled && embedToken) {
                // Deactivate token
                const response = await deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete({
                    path: { workflow_id: workflowId },
                });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to disable embedding"),
                    );
                }
                setEmbedToken(null);
            } else if (isEnabled) {
                // Create or update token
                const response = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                    path: { workflow_id: workflowId },
                    body: {
                        allowed_domains: domains.length > 0 ? domains : null,
                        settings: {
                            widgetType,
                            embedMode,
                            position,
                            buttonText,
                            buttonColor,
                            callToActionText,
                            size: "medium",
                            autoStart: false,
                            containerId: embedMode === "inline" ? "dograh-inline-container" : undefined,
                        },
                        usage_limit: null,
                        expires_in_days: null,
                    },
                });

                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to save widget configuration"),
                    );
                }
                if (response.data) {
                    setEmbedToken(response.data as EmbedToken);
                }
            }

            toast.success(
                "Widget configuration saved. Publish the agent to apply the changes.",
            );
            // Don't close modal after saving - let user copy the embed code
        } catch (error) {
            console.error("Failed to save embed token:", error);
            toast.error(
                error instanceof Error ? error.message : "Failed to save widget configuration",
            );
        } finally {
            setSaving(false);
        }
    };

    const copyToClipboard = async (text: string) => {
        try {
            await copyTextToClipboard(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            toast.error("Failed to copy embed code");
        }
    };

    const addDomain = () => {
        if (newDomain.trim() && !domains.includes(newDomain.trim())) {
            setDomains([...domains, newDomain.trim()]);
            setNewDomain("");
        }
    };

    const removeDomain = (domain: string) => {
        setDomains(domains.filter(d => d !== domain));
    };

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addDomain();
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-4xl w-full max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <div className="flex items-center justify-between">
                        <DialogTitle className="flex items-center gap-2">
                            <Rocket className="h-5 w-5" />
                            Configure Widget
                        </DialogTitle>
                        <a
                            href={WIDGET_MODE_DOCUMENTATION_URLS[embedMode]}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors pr-6"
                        >
                            Docs
                            <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                    </div>
                    <DialogDescription>
                        Add &quot;{workflowName}&quot; to any website with a simple script tag.
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="space-y-6">
                        {/* Enable/Disable Toggle */}
                        <div className="flex items-center justify-between">
                            <div className="space-y-0.5">
                                <Label htmlFor="embed-enabled">Enable Embedding</Label>
                                <p className="text-sm text-muted-foreground">
                                    Allow this workflow to be embedded on external websites
                                </p>
                            </div>
                            <Switch
                                id="embed-enabled"
                                checked={isEnabled}
                                onCheckedChange={setIsEnabled}
                            />
                        </div>

                        {isEnabled && (
                            <>
                                <Separator />

                                {/* Allowed Domains */}
                                <div className="space-y-3">
                                    <Label>
                                        Allowed Domains
                                        <span className="text-xs text-muted-foreground ml-2">
                                            (leave empty to allow all domains)
                                        </span>
                                    </Label>

                                    {/* Domain Input */}
                                    <div className="flex gap-2">
                                        <Input
                                            placeholder="example.com or *.example.com"
                                            value={newDomain}
                                            onChange={(e) => setNewDomain(e.target.value)}
                                            onKeyPress={handleKeyPress}
                                        />
                                        <Button
                                            type="button"
                                            size="icon"
                                            variant="outline"
                                            onClick={addDomain}
                                            disabled={!newDomain.trim()}
                                        >
                                            <Plus className="h-4 w-4" />
                                        </Button>
                                    </div>

                                    {/* Domain List */}
                                    {domains.length > 0 && (
                                        <div className="space-y-2">
                                            {domains.map((domain, index) => (
                                                <div
                                                    key={index}
                                                    className="flex items-center justify-between bg-muted/50 rounded-lg px-3 py-2"
                                                >
                                                    <span className="text-sm font-mono">{domain}</span>
                                                    <Button
                                                        type="button"
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-6 w-6"
                                                        onClick={() => removeDomain(domain)}
                                                    >
                                                        <Trash2 className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* Widget Type Selection */}
                                <div className="space-y-4">
                                    <Label>Widget Type</Label>
                                    <div className="grid grid-cols-2 gap-4">
                                        <button
                                            type="button"
                                            onClick={() => handleWidgetTypeChange("voice")}
                                            className={`p-4 rounded-lg border-2 transition-all ${
                                                widgetType === "voice"
                                                    ? "border-primary bg-primary/5"
                                                    : "border-muted hover:border-muted-foreground/20"
                                            }`}
                                        >
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-center gap-2 font-medium">
                                                    <Mic className="h-4 w-4" />
                                                    Voice Agent
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    Visitors talk to your agent by voice
                                                </div>
                                            </div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => handleWidgetTypeChange("chat")}
                                            className={`p-4 rounded-lg border-2 transition-all ${
                                                widgetType === "chat"
                                                    ? "border-primary bg-primary/5"
                                                    : "border-muted hover:border-muted-foreground/20"
                                            }`}
                                        >
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-center gap-2 font-medium">
                                                    <MessageCircle className="h-4 w-4" />
                                                    Chat Agent
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    Visitors type messages to your agent
                                                </div>
                                            </div>
                                        </button>
                                    </div>
                                </div>

                                {widgetType === "chat" && (
                                    <div className="space-y-2 rounded-lg border bg-muted/20 p-4">
                                        <Label htmlFor="text-chat-inactivity-timeout">
                                            Chat Inactivity Timeout
                                        </Label>
                                        <div className="flex items-center gap-2">
                                            <Input
                                                id="text-chat-inactivity-timeout"
                                                type="number"
                                                min={minimumTextChatInactivityMinutes}
                                                max={maximumTextChatInactivityMinutes}
                                                step="1"
                                                value={textChatInactivityMinutes}
                                                onChange={(event) =>
                                                    setTextChatInactivityMinutes(event.target.value)
                                                }
                                                aria-invalid={!textChatInactivityIsValid}
                                                className="w-32"
                                            />
                                            <span className="text-sm text-muted-foreground">
                                                minutes
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground">
                                            End a text chat and trigger its completion webhook after this long without chat activity.
                                        </p>
                                        {!textChatInactivityIsValid && (
                                            <p className="text-xs text-destructive">
                                                {textChatInactivityValidationMessage}.
                                            </p>
                                        )}
                                    </div>
                                )}

                                {/* Embed Mode Selection */}
                                <div className="space-y-4">
                                    <Label>Embed Mode</Label>
                                    <div className="grid grid-cols-3 gap-4">
                                        <button
                                            type="button"
                                            onClick={() => setEmbedMode("floating")}
                                            className={`p-4 rounded-lg border-2 transition-all ${
                                                embedMode === "floating"
                                                    ? "border-primary bg-primary/5"
                                                    : "border-muted hover:border-muted-foreground/20"
                                            }`}
                                        >
                                            <div className="space-y-2">
                                                <div className="font-medium">Floating Widget</div>
                                                <div className="text-xs text-muted-foreground">
                                                    Shows as a button in corner of the page
                                                </div>
                                            </div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setEmbedMode("inline")}
                                            className={`p-4 rounded-lg border-2 transition-all ${
                                                embedMode === "inline"
                                                    ? "border-primary bg-primary/5"
                                                    : "border-muted hover:border-muted-foreground/20"
                                            }`}
                                        >
                                            <div className="space-y-2">
                                                <div className="font-medium">Inline Component</div>
                                                <div className="text-xs text-muted-foreground">
                                                    Embeds directly in your page content
                                                </div>
                                            </div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setEmbedMode("headless")}
                                            className={`p-4 rounded-lg border-2 transition-all ${
                                                embedMode === "headless"
                                                    ? "border-primary bg-primary/5"
                                                    : "border-muted hover:border-muted-foreground/20"
                                            }`}
                                        >
                                            <div className="space-y-2">
                                                <div className="font-medium">Headless (Bring Your Own UI)</div>
                                                <div className="text-xs text-muted-foreground">
                                                    No UI - drive calls from your own buttons via the JS API
                                                </div>
                                            </div>
                                        </button>
                                    </div>
                                </div>

                                {/* Configuration based on mode */}
                                <div className="space-y-4">
                                    <Label>Configuration</Label>

                                    {/* Shared: Button Text + Button Color (skipped in headless — host renders its own UI) */}
                                    {embedMode !== "headless" && (
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="button-text" className="text-sm">Button Text</Label>
                                                <Input
                                                    id="button-text"
                                                    value={buttonText}
                                                    onChange={(e) => setButtonText(e.target.value)}
                                                    placeholder={WIDGET_TYPE_DEFAULTS[widgetType].buttonText}
                                                    maxLength={40}
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="button-color" className="text-sm">Button Color</Label>
                                                <div className="flex gap-2">
                                                    <Input
                                                        id="button-color-picker"
                                                        type="color"
                                                        value={buttonColor}
                                                        onChange={(e) => setButtonColor(e.target.value)}
                                                        className="w-14 h-10 cursor-pointer"
                                                    />
                                                    <Input
                                                        id="button-color"
                                                        value={buttonColor}
                                                        onChange={(e) => setButtonColor(e.target.value)}
                                                        placeholder="#10b981"
                                                        className="flex-1"
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                    )}

                                    {/* Floating mode: Position */}
                                    {embedMode === "floating" && (
                                        <div className="space-y-2">
                                            <Label htmlFor="position" className="text-sm">Position</Label>
                                            <Select value={position} onValueChange={setPosition}>
                                                <SelectTrigger id="position">
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="bottom-right">Bottom Right</SelectItem>
                                                    <SelectItem value="bottom-left">Bottom Left</SelectItem>
                                                    <SelectItem value="top-right">Top Right</SelectItem>
                                                    <SelectItem value="top-left">Top Left</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}

                                    {/* Inline mode: Call to Action Text */}
                                    {embedMode === "inline" && (
                                        <div className="space-y-2">
                                            <Label htmlFor="cta-text" className="text-sm">Call to Action Text</Label>
                                            <Input
                                                id="cta-text"
                                                value={callToActionText}
                                                onChange={(e) => setCallToActionText(e.target.value)}
                                                placeholder={WIDGET_TYPE_DEFAULTS[widgetType].callToActionText}
                                            />
                                        </div>
                                    )}

                                    {/* Preview (skipped for headless — host renders its own UI) */}
                                    {embedMode === "headless" ? null : embedMode === "floating" ? (
                                        <div className="rounded-lg border bg-muted/30 p-6 flex items-center justify-center">
                                            <button
                                                className="inline-flex items-center gap-2 rounded-full px-5 py-3 font-medium text-white shadow-lg whitespace-nowrap"
                                                style={{ backgroundColor: buttonColor }}
                                            >
                                                {widgetType === "chat" ? (
                                                    <MessageCircle className="h-4 w-4" />
                                                ) : (
                                                    <Mic className="h-4 w-4" />
                                                )}
                                                {buttonText || WIDGET_TYPE_DEFAULTS[widgetType].buttonText}
                                            </button>
                                        </div>
                                    ) : widgetType === "chat" ? (
                                        <div className="rounded-lg border bg-background p-6 flex items-center justify-center">
                                            <div className="w-full max-w-sm rounded-lg border shadow-sm overflow-hidden">
                                                <div
                                                    className="px-4 py-3 text-sm font-semibold text-white"
                                                    style={{ backgroundColor: buttonColor }}
                                                >
                                                    {buttonText || "Chat with Agent"}
                                                </div>
                                                <div className="p-4 space-y-2 bg-muted/20">
                                                    <div className="max-w-[80%] rounded-lg rounded-bl-sm bg-muted px-3 py-2 text-sm">
                                                        Hi! How can I help you today?
                                                    </div>
                                                    <div
                                                        className="max-w-[80%] ml-auto rounded-lg rounded-br-sm px-3 py-2 text-sm text-white"
                                                        style={{ backgroundColor: buttonColor }}
                                                    >
                                                        I have a question…
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2 border-t px-3 py-2">
                                                    <div className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm text-muted-foreground">
                                                        Type a message…
                                                    </div>
                                                    <span
                                                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-white"
                                                        style={{ backgroundColor: buttonColor }}
                                                    >
                                                        <Send className="h-4 w-4" />
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="rounded-lg border bg-background p-6 flex items-center justify-center">
                                            <div className="text-center">
                                                <svg className="w-16 h-16 mx-auto mb-4 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                                    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
                                                </svg>
                                                <p className="text-lg font-medium text-foreground mb-1">Ready to Connect</p>
                                                <p className="text-sm text-muted-foreground mb-5">{callToActionText}</p>
                                                <button
                                                    className="px-8 py-3 rounded-lg font-semibold text-white shadow-md"
                                                    style={{ backgroundColor: buttonColor }}
                                                >
                                                    {buttonText}
                                                </button>
                                            </div>
                                        </div>
                                    )}

                                    {/* Headless mode: Integration Instructions (chat) */}
                                    {embedMode === "headless" && widgetType === "chat" && (
                                        <div className="space-y-3">
                                            <div className="rounded-lg bg-muted/50 p-4">
                                                <h4 className="font-medium mb-2">Integration Instructions</h4>
                                                <ul className="text-sm space-y-2 text-muted-foreground">
                                                    <li>• Add the embed script tag to your page (see below).</li>
                                                    <li>• The widget renders no UI - render your own chat interface.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.startChat()</code> to start a conversation (the agent greeting arrives via <code className="text-xs">onMessage</code>).</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.sendMessage(text)</code> to send a visitor message; it resolves with the updated transcript, or <code className="text-xs">null</code> if the message could not be delivered.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.endChat()</code> to end the active conversation and trigger its completion webhook.</li>
                                                    <li>• Use <code className="text-xs">getMessages()</code> to read the transcript at any time.</li>
                                                    <li>• Subscribe to <code className="text-xs">onMessage</code> and <code className="text-xs">onChatStateChange</code> to drive your UI. States are <code className="text-xs">idle</code>, <code className="text-xs">starting</code>, <code className="text-xs">ready</code>, <code className="text-xs">waiting</code>, <code className="text-xs">ended</code>, <code className="text-xs">expired</code>, <code className="text-xs">error</code>.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.setContext({"{ ... }"})</code> before <code className="text-xs">startChat()</code> to pass visitor details the page learned after load.</li>
                                                </ul>
                                            </div>

                                            <div className="rounded-lg bg-blue-50 dark:bg-blue-950/20 p-4 border border-blue-200 dark:border-blue-800">
                                                <h4 className="font-medium mb-2 text-blue-900 dark:text-blue-100">Example - drive your own chat UI</h4>
                                                <pre className="text-xs overflow-x-auto">
                                                    <code className="text-blue-800 dark:text-blue-200">{HEADLESS_CHAT_EXAMPLE}</code>
                                                </pre>
                                            </div>
                                        </div>
                                    )}

                                    {/* Headless mode: Integration Instructions (voice) */}
                                    {embedMode === "headless" && widgetType === "voice" && (
                                        <div className="space-y-3">
                                            <div className="rounded-lg bg-muted/50 p-4">
                                                <h4 className="font-medium mb-2">Integration Instructions</h4>
                                                <ul className="text-sm space-y-2 text-muted-foreground">
                                                    <li>• Add the embed script tag to your page (see below).</li>
                                                    <li>• The widget renders no UI - render your own buttons.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.start()</code> to begin a call.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.end()</code> to end it.</li>
                                                    <li>• Subscribe to <code className="text-xs">onCallStart</code>, <code className="text-xs">onCallEnd</code>, <code className="text-xs">onStatusChange</code>, <code className="text-xs">onError</code> to drive your UI.</li>
                                                    <li>• <code className="text-xs">start()</code> must run inside a user-gesture handler (click) so the browser grants microphone access.</li>
                                                    <li>• Call <code className="text-xs">window.DograhWidget.setContext({"{ ... }"})</code> before <code className="text-xs">start()</code> to pass visitor details the page learned after load.</li>
                                                </ul>
                                            </div>

                                            <div className="rounded-lg bg-blue-50 dark:bg-blue-950/20 p-4 border border-blue-200 dark:border-blue-800">
                                                <h4 className="font-medium mb-2 text-blue-900 dark:text-blue-100">Example - track status in your own state</h4>
                                                <p className="text-xs text-blue-900/80 dark:text-blue-100/80 mb-2">
                                                    Mirror the call status into a variable you control, then render whatever UI you like from it. The status values are <code className="text-xs">idle</code>, <code className="text-xs">connecting</code>, <code className="text-xs">connected</code>, <code className="text-xs">failed</code>.
                                                </p>
                                                <pre className="text-xs overflow-x-auto">
                                                    <code className="text-blue-800 dark:text-blue-200">{`// Vanilla JS - keep your own state, render however you want
let callStatus = 'idle';

window.DograhWidget?.onStatusChange((status) => {
  callStatus = status;
  // ...trigger your render here (re-paint DOM, dispatch event, etc.)
});

document.getElementById('talk-btn').addEventListener('click', () => {
  if (callStatus === 'connected' || callStatus === 'connecting') {
    window.DograhWidget.end();
  } else {
    window.DograhWidget.start();
  }
});`}</code>
                                                </pre>
                                                <p className="text-xs text-blue-900/80 dark:text-blue-100/80 mt-3 mb-2">React:</p>
                                                <pre className="text-xs overflow-x-auto">
                                                    <code className="text-blue-800 dark:text-blue-200">{`function TalkButton() {
  const [status, setStatus] = useState('idle');

  useEffect(() => {
    window.DograhWidget?.onStatusChange(setStatus);
  }, []);

  const isLive = status === 'connected' || status === 'connecting';
  return (
    <button onClick={() => isLive ? window.DograhWidget.end() : window.DograhWidget.start()}>
      {/* render anything you want from \`status\` */}
    </button>
  );
}`}</code>
                                                </pre>
                                            </div>
                                        </div>
                                    )}

                                    {/* Inline mode: Integration Instructions */}
                                    {embedMode === "inline" && (
                                        <div className="space-y-3">
                                            <div className="rounded-lg bg-muted/50 p-4">
                                                <h4 className="font-medium mb-2">Integration Instructions</h4>
                                                <ul className="text-sm space-y-2 text-muted-foreground">
                                                    <li>• Add a div with id=&quot;dograh-inline-container&quot; where you want the widget</li>
                                                    <li>• The widget will render inside this container</li>
                                                    <li>• You have full control over the container&apos;s styling</li>
                                                    {widgetType === "chat" ? (
                                                        <li>• The chat panel renders in the container; the conversation starts when the visitor clicks the button</li>
                                                    ) : (
                                                        <>
                                                            <li>• Call window.DograhWidget.start() to begin the call</li>
                                                            <li>• Call window.DograhWidget.end() to end the call</li>
                                                        </>
                                                    )}
                                                </ul>
                                            </div>

                                            {widgetType === "chat" ? (
                                                <div className="rounded-lg bg-blue-50 dark:bg-blue-950/20 p-4 border border-blue-200 dark:border-blue-800">
                                                    <h4 className="font-medium mb-2 text-blue-900 dark:text-blue-100">Example</h4>
                                                    <pre className="text-xs overflow-x-auto">
                                                        <code className="text-blue-800 dark:text-blue-200">{`<h2>Chat with Our Agent</h2>
<div id="dograh-inline-container" style="min-height: 480px">
  <!-- Chat panel renders here; no extra JS needed -->
</div>`}</code>
                                                    </pre>
                                                </div>
                                            ) : (
                                                <div className="rounded-lg bg-blue-50 dark:bg-blue-950/20 p-4 border border-blue-200 dark:border-blue-800">
                                                    <h4 className="font-medium mb-2 text-blue-900 dark:text-blue-100">Example React Component</h4>
                                                    <pre className="text-xs overflow-x-auto">
                                                        <code className="text-blue-800 dark:text-blue-200">{`export function DograhAgent() {
  const [isCallActive, setIsCallActive] = useState(false);

  useEffect(() => {
    // Widget will auto-initialize when script loads
    window.DograhWidget?.onCallStart(() => {
      setIsCallActive(true);
    });
    window.DograhWidget?.onCallEnd(() => {
      setIsCallActive(false);
    });
  }, []);

  return (
    <div className="my-8">
      <h2>Talk to Our Agent</h2>
      <div id="dograh-inline-container" className="min-h-[400px]">
        {/* Widget renders here */}
      </div>
      <button
        onClick={() => window.DograhWidget?.start()}
        disabled={isCallActive}
      >
        Start Call
      </button>
    </div>
  );
}`}</code>
                                                    </pre>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>

                                <Separator />

                                {/* Save Button */}
                                <div className="flex justify-end">
                                    <Button
                                        onClick={handleSave}
                                        disabled={
                                            saving ||
                                            (widgetType === "chat" &&
                                                !textChatInactivityIsValid)
                                        }
                                    >
                                        {saving ? (
                                            <>
                                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            "Save Configurations"
                                        )}
                                    </Button>
                                </div>

                                {/* Embed Script (shows after saving; placeholder before) */}
                                {embedToken && embedToken.is_active ? (
                                    <>
                                        <Separator />
                                        <div className="space-y-3">
                                            <div className="flex items-center justify-between">
                                                <Label>Embed Code</Label>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => copyToClipboard(embedToken.embed_script)}
                                                >
                                                    {copied ? (
                                                        <>
                                                            <Check className="h-4 w-4 mr-1" />
                                                            Copied!
                                                        </>
                                                    ) : (
                                                        <>
                                                            <Copy className="h-4 w-4 mr-1" />
                                                            Copy Code
                                                        </>
                                                    )}
                                                </Button>
                                            </div>
                                            <div className="relative">
                                                <pre className="bg-muted/50 rounded-lg p-4 text-xs overflow-x-auto whitespace-pre-wrap break-all">
                                                    <code>{embedToken.embed_script}</code>
                                                </pre>
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Add this script to your website&apos;s HTML to enable the widget.
                                                Configuration changes will apply automatically without re-embedding.
                                            </p>
                                            <p className="text-xs text-muted-foreground">
                                                To pass visitor details to the agent, edit the{" "}
                                                <code className="text-xs">data-dograh-context</code> values above — or call{" "}
                                                <code className="text-xs">{"window.DograhWidget.setContext({ ... })"}</code> for
                                                details your page learns later. Each one is available in your prompts as{" "}
                                                <code className="text-xs">{"{{initial_context.page_url}}"}</code>.{" "}
                                                <a
                                                    href={WIDGET_CONTEXT_DOC_URL}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="underline underline-offset-2 hover:text-foreground"
                                                >
                                                    Learn more
                                                </a>
                                            </p>
                                        </div>
                                    </>
                                ) : (
                                    <>
                                        <Separator />
                                        <div className="space-y-3">
                                            <Label className="text-muted-foreground">Embed Code</Label>
                                            <div className="rounded-lg border border-dashed bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                                                Click <span className="font-medium">Save Configurations</span> to generate your embed script.
                                            </div>
                                        </div>
                                    </>
                                )}
                            </>
                        )}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
