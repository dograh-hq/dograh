import { Check, Copy, Loader2, Plus, Rocket, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import {
    createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost,
    deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete,
    getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet,
} from "@/client/sdk.gen";
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

interface EmbedDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    workflowName: string;
    getAccessToken: () => Promise<string>;
}

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
    getAccessToken,
}: EmbedDialogProps) {
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [embedToken, setEmbedToken] = useState<EmbedToken | null>(null);
    const [copied, setCopied] = useState(false);

    // Form state
    const [isEnabled, setIsEnabled] = useState(false);
    const [domains, setDomains] = useState<string[]>([]);
    const [newDomain, setNewDomain] = useState("");
    const [position, setPosition] = useState("bottom-right");
    const [buttonText, setButtonText] = useState("Start Voice Call");
    const [buttonColor, setButtonColor] = useState("#3B82F6");

    const loadEmbedToken = useCallback(async () => {
        setLoading(true);
        try {
            const token = await getAccessToken();
            client.setConfig({
                baseUrl: window.location.origin.replace(/:\d+$/, ':8000'),
                headers: { Authorization: `Bearer ${token}` },
            });

            const response = await getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet({
                path: { workflow_id: workflowId },
            });

            if (response.data) {
                setEmbedToken(response.data as EmbedToken);
                setIsEnabled(response.data.is_active);

                // Load settings
                if (response.data.settings) {
                    const settings = response.data.settings as Record<string, string>;
                    setPosition(settings.position || "bottom-right");
                    setButtonText(settings.buttonText || "Start Voice Call");
                    setButtonColor(settings.buttonColor || "#3B82F6");
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
    }, [workflowId, getAccessToken]);

    useEffect(() => {
        if (open) {
            loadEmbedToken();
        }
    }, [open, loadEmbedToken]);

    const handleSave = async () => {
        setSaving(true);
        try {
            const token = await getAccessToken();
            client.setConfig({
                baseUrl: window.location.origin.replace(/:\d+$/, ':8000'),
                headers: { Authorization: `Bearer ${token}` },
            });

            if (!isEnabled && embedToken) {
                // Deactivate token
                await deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete({
                    path: { workflow_id: workflowId },
                });
                setEmbedToken(null);
            } else if (isEnabled) {
                // Create or update token
                const response = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                    path: { workflow_id: workflowId },
                    body: {
                        allowed_domains: domains.length > 0 ? domains : null,
                        settings: {
                            position,
                            buttonText,
                            buttonColor,
                            size: "medium",
                            autoStart: false,
                        },
                        usage_limit: null,
                        expires_in_days: null,
                    },
                });

                if (response.data) {
                    setEmbedToken(response.data as EmbedToken);
                }
            }

            // Don't close modal after saving - let user copy the embed code
        } catch (error) {
            console.error("Failed to save embed token:", error);
        } finally {
            setSaving(false);
        }
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
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
                    <DialogTitle className="flex items-center gap-2">
                        <Rocket className="h-5 w-5" />
                        Deploy Workflow
                    </DialogTitle>
                    <DialogDescription>
                        Embed &quot;{workflowName}&quot; on any website with a simple script tag
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-8 w-8 animate-spin text-gray-500" />
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

                                {/* Widget Appearance */}
                                <div className="space-y-4">
                                    <Label>Widget Appearance</Label>

                                    <div className="grid grid-cols-2 gap-4">
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
                                                    placeholder="#3B82F6"
                                                    className="flex-1"
                                                />
                                            </div>
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <Label htmlFor="button-text" className="text-sm">Button Text</Label>
                                        <Input
                                            id="button-text"
                                            value={buttonText}
                                            onChange={(e) => setButtonText(e.target.value)}
                                            placeholder="Start Voice Call"
                                        />
                                    </div>

                                    {/* Preview */}
                                    <div className="rounded-lg border bg-background p-4 flex items-center justify-center">
                                        <button
                                            className="px-5 py-2.5 rounded-full font-medium shadow-lg hover:shadow-xl transition-all flex items-center gap-2"
                                            style={{
                                                backgroundColor: buttonColor,
                                                color: "white",
                                            }}
                                        >
                                            <svg
                                                width="18"
                                                height="18"
                                                viewBox="0 0 24 24"
                                                fill="none"
                                                stroke="currentColor"
                                                strokeWidth="2"
                                            >
                                                <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
                                            </svg>
                                            {buttonText}
                                        </button>
                                    </div>
                                </div>

                                <Separator />

                                {/* Save Button */}
                                <div className="flex justify-end">
                                    <Button onClick={handleSave} disabled={saving}>
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

                                {/* Embed Script (shows after saving) */}
                                {embedToken && embedToken.is_active && (
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
                                                Add this script to your website&apos;s HTML to enable the voice widget.
                                                Configuration changes will apply automatically without re-embedding.
                                            </p>
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