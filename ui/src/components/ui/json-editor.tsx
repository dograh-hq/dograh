import { AlertCircle, Check, Copy } from "lucide-react";
import { useCallback, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface JsonEditorProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    label?: string;
    description?: string;
    error?: string | null;
    minHeight?: string;
    showCopyButton?: boolean;
    className?: string;
}

interface JsonValidationResult {
    valid: boolean;
    parsed: Record<string, unknown> | unknown[];
    error?: string;
}

/**
 * Validates JSON and provides helpful error messages for common mistakes
 */
export function validateJson(jsonString: string): JsonValidationResult {
    const trimmed = jsonString.trim();

    // Empty or default empty object is valid
    if (!trimmed || trimmed === '{}' || trimmed === '[]') {
        return { valid: true, parsed: trimmed === '[]' ? [] : {} };
    }

    try {
        const parsed = JSON.parse(trimmed);
        return { valid: true, parsed };
    } catch (e) {
        const errorMessage = e instanceof Error ? e.message : 'Invalid JSON';

        // Detect common mistakes and provide helpful messages
        const helpfulError = getHelpfulJsonError(trimmed, errorMessage);
        return { valid: false, parsed: {}, error: helpfulError };
    }
}

/**
 * Analyzes JSON string and error to provide more helpful error messages
 */
function getHelpfulJsonError(jsonString: string, originalError: string): string {
    // Check for unquoted template variables like {{variable}} instead of "{{variable}}"
    const unquotedTemplateVar = /:\s*\{\{[^}]+\}\}/.test(jsonString);
    if (unquotedTemplateVar) {
        return 'Template variables must be quoted strings. Use "{{variable}}" instead of {{variable}}';
    }

    // Check for trailing comma before } or ]
    const trailingComma = /,\s*[}\]]/.test(jsonString);
    if (trailingComma) {
        return 'Trailing comma detected. Remove the comma before the closing bracket.';
    }

    // Check for missing comma between properties
    const missingComma = /"\s*\n\s*"/.test(jsonString) || /}\s*\n\s*"/.test(jsonString);
    if (missingComma) {
        return 'Missing comma between properties. Add a comma after each value.';
    }

    // Check for single quotes instead of double quotes
    const singleQuotes = /'[^']*'\s*:/.test(jsonString) || /:\s*'[^']*'/.test(jsonString);
    if (singleQuotes) {
        return 'JSON requires double quotes. Use "key" instead of \'key\'.';
    }

    // Check for unquoted string values
    const unquotedValue = /:\s*[a-zA-Z][a-zA-Z0-9_]*\s*[,}\]]/.test(jsonString);
    if (unquotedValue && !jsonString.includes('true') && !jsonString.includes('false') && !jsonString.includes('null')) {
        return 'String values must be quoted. Use "value" instead of value.';
    }

    // Check for unquoted keys
    const unquotedKey = /{\s*[a-zA-Z][a-zA-Z0-9_]*\s*:/.test(jsonString) || /,\s*[a-zA-Z][a-zA-Z0-9_]*\s*:/.test(jsonString);
    if (unquotedKey) {
        return 'Property names must be quoted. Use "key" instead of key.';
    }

    // Extract position info from error if available
    const positionMatch = originalError.match(/position (\d+)/i);
    if (positionMatch) {
        const position = parseInt(positionMatch[1], 10);
        const lines = jsonString.substring(0, position).split('\n');
        const line = lines.length;
        const column = lines[lines.length - 1].length + 1;
        return `Invalid JSON at line ${line}, column ${column}. Check for missing quotes, commas, or brackets.`;
    }

    return 'Invalid JSON syntax. Check for missing quotes, commas, or brackets.';
}

export function JsonEditor({
    value,
    onChange,
    placeholder = '{}',
    label,
    description,
    error,
    minHeight = "200px",
    showCopyButton = true,
    className = "",
}: JsonEditorProps) {
    const [copied, setCopied] = useState(false);

    const handleCopy = useCallback(async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    }, [value]);

    return (
        <div className={`grid gap-2 ${className}`}>
            {(label || showCopyButton) && (
                <div className="flex items-center justify-between">
                    {label && <Label>{label}</Label>}
                    {showCopyButton && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleCopy}
                            type="button"
                        >
                            {copied ? (
                                <Check className="h-4 w-4 mr-1" />
                            ) : (
                                <Copy className="h-4 w-4 mr-1" />
                            )}
                            Copy
                        </Button>
                    )}
                </div>
            )}
            {description && (
                <Label className="text-xs text-muted-foreground">
                    {description}
                </Label>
            )}
            <Textarea
                value={value}
                onChange={(e) => onChange(e.target.value)}
                className={`font-mono text-sm`}
                style={{ minHeight }}
                placeholder={placeholder}
            />
            {error && (
                <div className="flex items-start gap-2 p-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md">
                    <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                    <span>{error}</span>
                </div>
            )}
        </div>
    );
}
