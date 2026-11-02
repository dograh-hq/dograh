"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function ConfigurationSelect({ id, value, onValueChange, options, placeholder = "Select a value", required, disabled }: {
    id: string;
    value: string;
    onValueChange: (value: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
    required?: boolean;
    disabled?: boolean;
}) {
    return <Select value={value} onValueChange={onValueChange} required={required} disabled={disabled}>
        <SelectTrigger id={id} className="w-full min-w-0"><SelectValue placeholder={placeholder} /></SelectTrigger>
        <SelectContent>{options.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent>
    </Select>;
}
