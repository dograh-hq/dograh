"use client";

import { AlertCircle } from "lucide-react";
import { useEffect,useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface BodyTemplateEditorProps {
    value: Record<string, unknown> | null;
    onChange: (template: Record<string, unknown> | null) => void;
    availableParams: string[];
    disabled?: boolean;
    onValidityChange?: (isValid: boolean) => void;
}

const RESERVED_PARAM_NAMES = ["initial_context", "gathered_context"];

/** Returns true if `name` appears as a placeholder in `raw` (with or without fallback). */
function isParamUsed(name: string, raw: string): boolean {
    if (!name) return false;
    // Escape special regex chars in the param name.
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // Match {{name}}, {{name | fallback}}, {{ name }}, {{ name | fallback:val }}, etc.
    // The lookbehind-equivalent: char after name must be }, |, or whitespace.
    return new RegExp(`\\{\\{\\s*${escaped}(?:[\\s|}])`).test(raw);
}

export function BodyTemplateEditor({
    value,
    onChange,
    availableParams,
    disabled = false,
    onValidityChange,
}: BodyTemplateEditorProps) {
    const [raw, setRaw] = useState(value ? JSON.stringify(value, null, 2) : "");
    const [error, setError] = useState<string | null>(null);

    const handleChange = (text: string) => {
        setRaw(text);
        if (!text.trim()) {
            setError(null);
            onChange(null);
            onValidityChange?.(true);
            return;
        }
        try {
            const parsed = JSON.parse(text);
            if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
                setError("Body template must be a JSON object, not an array or primitive.");
                onValidityChange?.(false);
                return;
            }
            const size = new TextEncoder().encode(text).length;
            if (size > 65_536) {
                setError(`Template too large (${(size / 1024).toFixed(1)} KB). Max 64 KB.`);
                onValidityChange?.(false);
                return;
            }
            setError(null);
            onChange(parsed);
            onValidityChange?.(true);
        } catch {
            setError("Invalid JSON — please check your syntax.");
            onValidityChange?.(false);
        }
    };

    const unusedParams = availableParams.filter(
        (name) => name && !isParamUsed(name, raw)
    );
    const reservedConflicts = availableParams.filter((n) =>
        RESERVED_PARAM_NAMES.includes(n)
    );

    useEffect(() => {
        if (reservedConflicts.length > 0) {
            onValidityChange?.(false);
        } else if (error) {
            onValidityChange?.(false);
        } else {
            onValidityChange?.(true);
        }
    }, [reservedConflicts.length, error, onValidityChange]);

    return (
        <div className="space-y-3">
            <div className="space-y-1.5">
                <Label className="text-xs font-medium">JSON Body Template</Label>
                <Label className="text-xs text-muted-foreground">
                    Define the nested JSON structure your API expects. Use{" "}
                    <code className="bg-muted px-1 rounded text-xs">{"{{param_name}}"}</code>{" "}
                    for agent-collected values or{" "}
                    <code className="bg-muted px-1 rounded text-xs">{"{{initial_context.x}}"}</code>{" "}
                    for call context. Static values are forwarded as-is.
                </Label>
                <Textarea
                    className="font-mono text-xs min-h-[200px] resize-y"
                    placeholder={`{\n  "reservations": [{\n    "arrival": "{{arrival}}",\n    "primaryGuest": { "firstName": "{{firstName}}" }\n  }]\n}`}
                    value={raw}
                    onChange={(e) => handleChange(e.target.value)}
                    disabled={disabled}
                    spellCheck={false}
                />
                {error && (
                    <div className="flex items-center gap-2 rounded-md border border-destructive/50 text-destructive p-3 text-xs">
                        <AlertCircle className="h-4 w-4" />
                        <span>{error}</span>
                    </div>
                )}
                {reservedConflicts.length > 0 && (
                    <div className="flex items-center gap-2 rounded-md border border-destructive/50 text-destructive p-3 text-xs">
                        <AlertCircle className="h-4 w-4" />
                        <span>
                            Reserved parameter name(s) detected: {reservedConflicts.join(", ")}.
                            Rename to avoid conflicts with Dograh&apos;s call context system.
                        </span>
                    </div>
                )}
            </div>
            {availableParams.length > 0 && (
                <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">
                        Available placeholders (click to copy):
                    </Label>
                    <div className="flex flex-wrap gap-1.5">
                        {availableParams.filter(Boolean).map((name) => (
                            <Badge
                                key={name}
                                variant={unusedParams.includes(name) ? "outline" : "secondary"}
                                className="font-mono text-xs cursor-pointer select-none"
                                title={`Click to copy {{${name}}}`}
                                onClick={() => navigator.clipboard.writeText(`{{${name}}}`)}
                            >
                                {`{{${name}}}`}
                            </Badge>
                        ))}
                    </div>
                    {unusedParams.length > 0 && (
                        <p className="text-xs text-yellow-600 dark:text-yellow-400">
                            ⚠ {unusedParams.length} param(s) not used in template:{" "}
                            {unusedParams.map((n) => `{{${n}}}`).join(", ")}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
