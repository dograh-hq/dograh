"use client";

import { AlertCircle } from "lucide-react";
import { useEffect, useRef,useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { copyTextToClipboard } from "@/lib/clipboard";

interface BodyTemplateEditorProps {
    value: Record<string, unknown> | null;
    onChange: (template: Record<string, unknown> | null) => void;
    availableParams: string[];
    disabled?: boolean;
    onValidityChange?: (isValid: boolean) => void;
}

const RESERVED_PARAM_NAMES = ["initial_context", "gathered_context"];
// current_time and current_weekday (plus their _<TZ> suffixed variants) are
// resolved by the renderer as built-in variables before looking up the
// caller-supplied argument, so a parameter with these names would be silently
// replaced by a generated timestamp/weekday in the outbound payload.
const BUILTIN_PREFIXES = ["current_time", "current_weekday"];

function isBuiltinConflict(name: string): boolean {
    return BUILTIN_PREFIXES.some(
        (prefix) => name === prefix || name.startsWith(prefix + "_")
    );
}

/** Returns true if `name` appears as a placeholder in `raw` (with or without fallback). */
function isParamUsed(name: string, raw: string): boolean {
    if (!name) return false;
    // Escape special regex chars in the param name.
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // Match {{name}}, {{name | fallback}}, {{ name }}, {{ name | fallback:val }},
    // and dotted paths like {{name.sub}} (char after name is }, |, whitespace, or .).
    return new RegExp(`\\{\\{\\s*${escaped}(?:[\\s|}.}])`).test(raw);
}

function getObjectDepth(value: unknown): number {
    if (typeof value !== "object" || value === null) return 0;
    let maxChildDepth = 0;
    for (const key of Object.keys(value)) {
        const childDepth = getObjectDepth((value as Record<string, unknown>)[key]);
        if (childDepth > maxChildDepth) maxChildDepth = childDepth;
    }
    return 1 + maxChildDepth;
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
    const lastPushedValue = useRef<Record<string, unknown> | null>(value);

    // Sync raw when value changes externally (e.g. switching tools).
    // Use reference equality so switching between two tools with identical saved
    // templates still resets the editor draft.
    useEffect(() => {
        if (value !== lastPushedValue.current) {
            setRaw(value ? JSON.stringify(value, null, 2) : "");
            lastPushedValue.current = value;
            setError(null);
        }
    }, [value]);

    const handleChange = (text: string) => {
        setRaw(text);
        if (!text.trim()) {
            setError(null);
            onChange(null);
            lastPushedValue.current = null;
            return;
        }
        try {
            const parsed = JSON.parse(text);
            if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
                setError("Body template must be a JSON object, not an array or primitive.");
                return;
            }
            const size = new TextEncoder().encode(JSON.stringify(parsed)).length;
            if (size > 65_536) {
                setError(`Template too large (${(size / 1024).toFixed(1)} KB). Max 64 KB.`);
                return;
            }
            if (getObjectDepth(parsed) > 20) {
                setError("Template nesting exceeds maximum depth of 20 levels.");
                return;
            }
            setError(null);
            onChange(parsed);
            lastPushedValue.current = parsed;
        } catch {
            setError("Invalid JSON — please check your syntax.");
        }
    };

    const normalizedParams = Array.from(
        new Set(availableParams.map((p) => (p || "").trim()).filter(Boolean))
    );

    const unusedParams = normalizedParams.filter(
        (name) => !isParamUsed(name, raw)
    );
    const reservedConflicts = normalizedParams.filter(
        (n) => RESERVED_PARAM_NAMES.includes(n) && isParamUsed(n, raw)
    );
    const builtinConflicts = normalizedParams.filter(
        (n) => isBuiltinConflict(n) && isParamUsed(n, raw)
    );

    useEffect(() => {
        if (reservedConflicts.length > 0 || builtinConflicts.length > 0) {
            onValidityChange?.(false);
        } else if (error) {
            onValidityChange?.(false);
        } else {
            onValidityChange?.(true);
        }
    }, [reservedConflicts.length, builtinConflicts.length, error, onValidityChange]);

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
                {builtinConflicts.length > 0 && (
                    <div className="flex items-center gap-2 rounded-md border border-destructive/50 text-destructive p-3 text-xs">
                        <AlertCircle className="h-4 w-4" />
                        <span>
                            Parameter name(s) conflict with built-in template variables:{" "}
                            {builtinConflicts.join(", ")}. The renderer replaces{" "}
                            <code className="bg-muted px-1 rounded">current_time</code> and{" "}
                            <code className="bg-muted px-1 rounded">current_weekday</code>{" "}
                            (and their <code className="bg-muted px-1 rounded">_&lt;TZ&gt;</code> variants)
                            with generated values, silently discarding the agent-supplied argument.
                            Rename these parameters to avoid incorrect outbound payloads.
                        </span>
                    </div>
                )}
            </div>
            {normalizedParams.length > 0 && (
                <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">
                        Available placeholders (click to copy):
                    </Label>
                    <div className="flex flex-wrap gap-1.5">
                        {normalizedParams.map((name) => (
                            <Badge
                                key={name}
                                variant={unusedParams.includes(name) ? "outline" : "secondary"}
                                className="font-mono text-xs cursor-pointer select-none"
                                title={`Click to copy {{${name}}}`}
                                tabIndex={0}
                                role="button"
                                onClick={() => copyTextToClipboard(`{{${name}}}`)}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        copyTextToClipboard(`{{${name}}}`);
                                    }
                                }}
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
