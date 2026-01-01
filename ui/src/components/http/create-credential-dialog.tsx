"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import { useState } from "react";

import { createCredentialApiV1CredentialsPost } from "@/client";
import { CredentialResponse, WebhookCredentialType } from "@/client/types.gen";
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
import { useAuth } from "@/lib/auth";

interface CreateCredentialDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onCreated?: (credential: CredentialResponse) => void;
}

interface CredentialField {
    key: string;
    label: string;
    placeholder: string;
    isSecret?: boolean;
}

const getCredentialDataFields = (type: WebhookCredentialType): CredentialField[] => {
    switch (type) {
        case "api_key":
            return [
                { key: "header_name", label: "Header Name", placeholder: "X-API-Key" },
                { key: "api_key", label: "API Key", placeholder: "your-api-key", isSecret: true },
            ];
        case "bearer_token":
            return [
                { key: "token", label: "Token", placeholder: "your-bearer-token", isSecret: true },
            ];
        case "basic_auth":
            return [
                { key: "username", label: "Username", placeholder: "username" },
                { key: "password", label: "Password", placeholder: "password", isSecret: true },
            ];
        case "custom_header":
            return [
                { key: "header_name", label: "Header Name", placeholder: "X-Custom-Header" },
                { key: "header_value", label: "Header Value", placeholder: "header-value", isSecret: true },
            ];
        default:
            return [];
    }
};

export function CreateCredentialDialog({
    open,
    onOpenChange,
    onCreated,
}: CreateCredentialDialogProps) {
    const { getAccessToken } = useAuth();

    const [name, setName] = useState("");
    const [description, setDescription] = useState("");
    const [credentialType, setCredentialType] = useState<WebhookCredentialType>("bearer_token");
    const [credentialData, setCredentialData] = useState<Record<string, string>>({});
    const [isCreating, setIsCreating] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleCreate = async () => {
        if (!name.trim()) return;

        setIsCreating(true);
        setError(null);

        try {
            const accessToken = await getAccessToken();
            const response = await createCredentialApiV1CredentialsPost({
                headers: { Authorization: `Bearer ${accessToken}` },
                body: {
                    name,
                    description: description || undefined,
                    credential_type: credentialType,
                    credential_data: credentialData,
                },
            });

            if (response.error) {
                const errorDetail = (response.error as { detail?: string })?.detail
                    || "Failed to create credential";
                setError(errorDetail);
                return;
            }

            if (response.data) {
                onCreated?.(response.data);
                handleClose();
            }
        } catch (err) {
            console.error("Failed to create credential:", err);
            setError(
                err instanceof Error ? err.message : "An unexpected error occurred"
            );
        } finally {
            setIsCreating(false);
        }
    };

    const handleClose = () => {
        onOpenChange(false);
        // Reset form
        setName("");
        setDescription("");
        setCredentialType("bearer_token");
        setCredentialData({});
        setError(null);
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (!newOpen) {
            setError(null);
        }
        onOpenChange(newOpen);
    };

    const fields = getCredentialDataFields(credentialType);

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Add Credential</DialogTitle>
                    <DialogDescription>
                        Create a new credential for authentication.
                    </DialogDescription>
                </DialogHeader>

                {error && (
                    <div className="flex items-start gap-2 p-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md">
                        <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                        <span>{error}</span>
                    </div>
                )}

                <div className="space-y-4 py-4">
                    <div className="grid gap-2">
                        <Label htmlFor="cred-name">Name *</Label>
                        <Input
                            id="cred-name"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder="My API Key"
                        />
                    </div>

                    <div className="grid gap-2">
                        <Label htmlFor="cred-description">Description</Label>
                        <Input
                            id="cred-description"
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            placeholder="Optional description"
                        />
                    </div>

                    <div className="grid gap-2">
                        <Label>Credential Type</Label>
                        <Select
                            value={credentialType}
                            onValueChange={(v) => {
                                setCredentialType(v as WebhookCredentialType);
                                setCredentialData({});
                            }}
                        >
                            <SelectTrigger>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="bearer_token">Bearer Token</SelectItem>
                                <SelectItem value="api_key">API Key</SelectItem>
                                <SelectItem value="basic_auth">Basic Auth</SelectItem>
                                <SelectItem value="custom_header">Custom Header</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {fields.map((field) => (
                        <div key={field.key} className="grid gap-2">
                            <Label htmlFor={`cred-${field.key}`}>{field.label}</Label>
                            <Input
                                id={`cred-${field.key}`}
                                type={field.isSecret ? "password" : "text"}
                                value={credentialData[field.key] || ""}
                                onChange={(e) =>
                                    setCredentialData((prev) => ({
                                        ...prev,
                                        [field.key]: e.target.value,
                                    }))
                                }
                                placeholder={field.placeholder}
                            />
                        </div>
                    ))}
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={handleClose}
                        disabled={isCreating}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={handleCreate}
                        disabled={!name.trim() || isCreating}
                    >
                        {isCreating ? (
                            <>
                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                Creating...
                            </>
                        ) : (
                            "Create"
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
