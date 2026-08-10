"use client";

import { useCallback, useState } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

// URL regex pattern that validates:
// - http:// or https:// protocol (required)
// - Optional username:password@
// - Domain name or IP address
// - Optional port number
// - Optional path, query string, and fragment
const URL_REGEX =
    /^https?:\/\/(?:[\w-]+(?::[\w-]+)?@)?(?:[\w-]+\.)*[\w-]+(?::\d{1,5})?(?:\/[^\s]*)?$/i;
const HTTP_URL_PREFIX_REGEX = /^https?:\/\//i;
const URL_TEMPLATE_REGEX = /\{\{\s*([^|}\s]+)\s*(?:\|[^}]+)?\}\}/g;

export interface UrlValidationResult {
    valid: boolean;
    error?: string;
}

function extractUrlSegmentParameters(segment: string): string[] {
    const parameters = Array.from(segment.matchAll(URL_TEMPLATE_REGEX), (match) => match[1]);
    return Array.from(new Set(parameters));
}

export function extractUrlHostnameParameters(url: string): string[] {
    const schemeSeparatorIndex = url.indexOf("://");
    if (schemeSeparatorIndex === -1) return [];

    const authorityStart = schemeSeparatorIndex + 3;
    const authorityEndOffset = url.slice(authorityStart).search(/[/?#]/);
    const authorityEnd =
        authorityEndOffset === -1 ? url.length : authorityStart + authorityEndOffset;

    return extractUrlSegmentParameters(url.slice(authorityStart, authorityEnd));
}

export function extractUrlPathParameters(url: string): string[] {
    const schemeSeparatorIndex = url.indexOf("://");
    const authorityStart = schemeSeparatorIndex === -1 ? 0 : schemeSeparatorIndex + 3;
    const pathStart = url.indexOf("/", authorityStart);

    if (pathStart === -1) return [];

    const pathEndCandidates = [url.indexOf("?", pathStart), url.indexOf("#", pathStart)].filter(
        (index) => index !== -1
    );
    const pathEnd = pathEndCandidates.length > 0 ? Math.min(...pathEndCandidates) : url.length;
    return extractUrlSegmentParameters(url.slice(pathStart, pathEnd));
}

export function validateUrl(url: string): UrlValidationResult {
    const trimmedUrl = url.trim();

    if (!trimmedUrl) {
        return { valid: false, error: "URL is required" };
    }

    if (!HTTP_URL_PREFIX_REGEX.test(trimmedUrl)) {
        return {
            valid: false,
            error: "Invalid URL format. Must start with http:// or https://",
        };
    }

    const urlWithTemplatePlaceholders = trimmedUrl.replace(URL_TEMPLATE_REGEX, "template-value");
    if (urlWithTemplatePlaceholders.includes("{{") || urlWithTemplatePlaceholders.includes("}}")) {
        return {
            valid: false,
            error: "Invalid URL template format",
        };
    }

    if (!URL_REGEX.test(urlWithTemplatePlaceholders)) {
        return {
            valid: false,
            error: "Invalid URL format",
        };
    }

    return { valid: true };
}

interface UrlInputProps {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    disabled?: boolean;
    className?: string;
    /** Show validation error styling and message inline */
    showValidation?: boolean;
    /** Called when validation state changes */
    onValidationChange?: (result: UrlValidationResult) => void;
}

export function UrlInput({
    value,
    onChange,
    placeholder = "https://api.example.com/endpoint",
    disabled = false,
    className,
    showValidation = false,
    onValidationChange,
}: UrlInputProps) {
    const [touched, setTouched] = useState(false);

    const handleChange = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            const newValue = e.target.value;
            onChange(newValue);

            if (onValidationChange && (touched || newValue)) {
                onValidationChange(validateUrl(newValue));
            }
        },
        [onChange, onValidationChange, touched]
    );

    const handleBlur = useCallback(() => {
        setTouched(true);
        const trimmedValue = value.trim();
        if (trimmedValue !== value) {
            onChange(trimmedValue);
        }
        if (onValidationChange && trimmedValue) {
            onValidationChange(validateUrl(trimmedValue));
        }
    }, [onChange, onValidationChange, value]);

    const validation = validateUrl(value);
    const showError = showValidation && touched && !validation.valid && value;

    return (
        <div className="space-y-1">
            <Input
                value={value}
                onChange={handleChange}
                onBlur={handleBlur}
                placeholder={placeholder}
                disabled={disabled}
                className={cn(
                    showError && "border-destructive focus-visible:ring-destructive",
                    className
                )}
            />
            {showError && (
                <p className="text-xs text-destructive">{validation.error}</p>
            )}
        </div>
    );
}
