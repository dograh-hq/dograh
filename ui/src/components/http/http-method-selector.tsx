"use client";

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface HttpMethodSelectorProps {
    value: HttpMethod;
    onChange: (method: HttpMethod) => void;
    disabled?: boolean;
}

const HTTP_METHODS: HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

export function HttpMethodSelector({
    value,
    onChange,
    disabled = false,
}: HttpMethodSelectorProps) {
    return (
        <Select
            value={value}
            onValueChange={(v) => onChange(v as HttpMethod)}
            disabled={disabled}
        >
            <SelectTrigger>
                <SelectValue />
            </SelectTrigger>
            <SelectContent>
                {HTTP_METHODS.map((method) => (
                    <SelectItem key={method} value={method}>
                        {method}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}
