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

export interface UrlValidationResult {
    valid: boolean;
    error?: string;
}

export function validateUrl(url: string): UrlValidationResult {
    const trimmedUrl = url.trim();

    if (!trimmedUrl) {
        return { valid: false, error: "URL is required" };
    }

    if (!URL_REGEX.test(trimmedUrl)) {
        return {
            valid: false,
            error: "Invalid URL format. Must start with http:// or https://",
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
