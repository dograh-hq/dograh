import { Check, Copy, Globe, Loader2, Rocket } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import {
    createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost,
    deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete,
    getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

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
    const [testPageCopied, setTestPageCopied] = useState(false);

    // Form state
    const [isEnabled, setIsEnabled] = useState(false);
    const [domains, setDomains] = useState<string>("");
    const [position, setPosition] = useState("bottom-right");
    const [theme, setTheme] = useState("light");
    const [buttonText, setButtonText] = useState("Start Voice Call");
    const [buttonColor, setButtonColor] = useState("#3B82F6");
    const [usageLimit, setUsageLimit] = useState<string>("");
    const [expiresInDays, setExpiresInDays] = useState<string>("30");

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
                    setPosition(response.data.settings.position || "bottom-right");
                    setTheme(response.data.settings.theme || "light");
                    setButtonText(response.data.settings.buttonText || "Start Voice Call");
                    setButtonColor(response.data.settings.buttonColor || "#3B82F6");
                }

                // Load domains
                if (response.data.allowed_domains) {
                    setDomains(response.data.allowed_domains.join("\n"));
                }

                // Load limits
                if (response.data.usage_limit) {
                    setUsageLimit(response.data.usage_limit.toString());
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
                const domainList = domains
                    .split("\n")
                    .map(d => d.trim())
                    .filter(d => d.length > 0);

                const response = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                    path: { workflow_id: workflowId },
                    body: {
                        allowed_domains: domainList.length > 0 ? domainList : null,
                        settings: {
                            position,
                            theme,
                            buttonText,
                            buttonColor,
                        },
                        usage_limit: usageLimit ? parseInt(usageLimit) : null,
                        expires_in_days: expiresInDays ? parseInt(expiresInDays) : null,
                    },
                });

                if (response.data) {
                    setEmbedToken(response.data as EmbedToken);
                }
            }

            onOpenChange(false);
        } catch (error) {
            console.error("Failed to save embed token:", error);
        } finally {
            setSaving(false);
        }
    };

    const copyToClipboard = (text: string, setCopiedFn: (value: boolean) => void) => {
        navigator.clipboard.writeText(text);
        setCopiedFn(true);
        setTimeout(() => setCopiedFn(false), 2000);
    };

    const getTestPageUrl = () => {
        const baseUrl = window.location.origin;
        return `${baseUrl}/test-embed.html?token=${embedToken?.token}`;
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
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

                                <Tabs defaultValue="configuration" className="w-full">
                                    <TabsList className="grid w-full grid-cols-3">
                                        <TabsTrigger value="configuration">Configuration</TabsTrigger>
                                        <TabsTrigger value="appearance">Appearance</TabsTrigger>
                                        <TabsTrigger value="embed-code">Embed Code</TabsTrigger>
                                    </TabsList>

                                    <TabsContent value="configuration" className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="domains">
                                                Allowed Domains
                                                <span className="text-xs text-muted-foreground ml-2">
                                                    (one per line, leave empty for all)
                                                </span>
                                            </Label>
                                            <Textarea
                                                id="domains"
                                                placeholder="example.com&#10;*.example.com&#10;app.example.com"
                                                value={domains}
                                                onChange={(e) => setDomains(e.target.value)}
                                                rows={4}
                                            />
                                        </div>

                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="usage-limit">
                                                    Usage Limit
                                                    <span className="text-xs text-muted-foreground ml-2">
                                                        (optional)
                                                    </span>
                                                </Label>
                                                <Input
                                                    id="usage-limit"
                                                    type="number"
                                                    placeholder="Unlimited"
                                                    value={usageLimit}
                                                    onChange={(e) => setUsageLimit(e.target.value)}
                                                />
                                            </div>

                                            <div className="space-y-2">
                                                <Label htmlFor="expires">
                                                    Expires In (days)
                                                    <span className="text-xs text-muted-foreground ml-2">
                                                        (optional)
                                                    </span>
                                                </Label>
                                                <Input
                                                    id="expires"
                                                    type="number"
                                                    placeholder="Never"
                                                    value={expiresInDays}
                                                    onChange={(e) => setExpiresInDays(e.target.value)}
                                                />
                                            </div>
                                        </div>

                                        {embedToken && (
                                            <div className="rounded-lg bg-muted/50 p-4 space-y-2">
                                                <div className="flex items-center justify-between">
                                                    <span className="text-sm font-medium">Usage Statistics</span>
                                                    <Badge variant="secondary">
                                                        {embedToken.usage_count} / {embedToken.usage_limit || "∞"}
                                                    </Badge>
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    Created: {new Date(embedToken.created_at).toLocaleDateString()}
                                                    {embedToken.expires_at && (
                                                        <> · Expires: {new Date(embedToken.expires_at).toLocaleDateString()}</>
                                                    )}
                                                </div>
                                            </div>
                                        )}
                                    </TabsContent>

                                    <TabsContent value="appearance" className="space-y-4">
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="position">Position</Label>
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
                                                <Label htmlFor="theme">Theme</Label>
                                                <Select value={theme} onValueChange={setTheme}>
                                                    <SelectTrigger id="theme">
                                                        <SelectValue />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        <SelectItem value="light">Light</SelectItem>
                                                        <SelectItem value="dark">Dark</SelectItem>
                                                        <SelectItem value="auto">Auto</SelectItem>
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="button-text">Button Text</Label>
                                            <Input
                                                id="button-text"
                                                value={buttonText}
                                                onChange={(e) => setButtonText(e.target.value)}
                                            />
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="button-color">Button Color</Label>
                                            <div className="flex gap-2">
                                                <Input
                                                    id="button-color"
                                                    type="color"
                                                    value={buttonColor}
                                                    onChange={(e) => setButtonColor(e.target.value)}
                                                    className="w-20 h-10 cursor-pointer"
                                                />
                                                <Input
                                                    value={buttonColor}
                                                    onChange={(e) => setButtonColor(e.target.value)}
                                                    placeholder="#3B82F6"
                                                    className="flex-1"
                                                />
                                            </div>
                                        </div>

                                        {/* Preview */}
                                        <div className="space-y-2">
                                            <Label>Preview</Label>
                                            <div className="rounded-lg border bg-background p-8 flex items-center justify-center">
                                                <button
                                                    className="px-6 py-3 rounded-full font-medium shadow-lg hover:shadow-xl transition-all flex items-center gap-2"
                                                    style={{
                                                        backgroundColor: buttonColor,
                                                        color: "white",
                                                    }}
                                                >
                                                    <svg
                                                        width="20"
                                                        height="20"
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
                                    </TabsContent>

                                    <TabsContent value="embed-code" className="space-y-4">
                                        {embedToken ? (
                                            <>
                                                <div className="space-y-2">
                                                    <Label>Embed Script</Label>
                                                    <div className="relative">
                                                        <Textarea
                                                            value={embedToken.embed_script}
                                                            readOnly
                                                            rows={8}
                                                            className="font-mono text-xs"
                                                        />
                                                        <Button
                                                            size="sm"
                                                            variant="secondary"
                                                            className="absolute top-2 right-2"
                                                            onClick={() =>
                                                                copyToClipboard(embedToken.embed_script, setCopied)
                                                            }
                                                        >
                                                            {copied ? (
                                                                <>
                                                                    <Check className="h-4 w-4 mr-1" />
                                                                    Copied
                                                                </>
                                                            ) : (
                                                                <>
                                                                    <Copy className="h-4 w-4 mr-1" />
                                                                    Copy
                                                                </>
                                                            )}
                                                        </Button>
                                                    </div>
                                                </div>

                                                <div className="rounded-lg bg-muted/50 p-4 space-y-3">
                                                    <div className="flex items-center gap-2">
                                                        <Globe className="h-4 w-4" />
                                                        <span className="font-medium text-sm">Test Your Embed</span>
                                                    </div>
                                                    <p className="text-sm text-muted-foreground">
                                                        Try your embed configuration on our test page before deploying to
                                                        production.
                                                    </p>
                                                    <div className="flex gap-2">
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            onClick={() => window.open(getTestPageUrl(), "_blank")}
                                                        >
                                                            Open Test Page
                                                        </Button>
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            onClick={() =>
                                                                copyToClipboard(getTestPageUrl(), setTestPageCopied)
                                                            }
                                                        >
                                                            {testPageCopied ? (
                                                                <>
                                                                    <Check className="h-4 w-4 mr-1" />
                                                                    Copied
                                                                </>
                                                            ) : (
                                                                <>
                                                                    <Copy className="h-4 w-4 mr-1" />
                                                                    Copy URL
                                                                </>
                                                            )}
                                                        </Button>
                                                    </div>
                                                </div>
                                            </>
                                        ) : (
                                            <div className="text-center py-8 text-muted-foreground">
                                                Save your configuration to generate the embed code
                                            </div>
                                        )}
                                    </TabsContent>
                                </Tabs>
                            </>
                        )}
                    </div>
                )}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={saving}>
                        {saving ? (
                            <>
                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                Saving...
                            </>
                        ) : (
                            "Save Configuration"
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
