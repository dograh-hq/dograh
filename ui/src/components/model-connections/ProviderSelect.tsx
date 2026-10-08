"use client";

import * as SelectPrimitive from "@radix-ui/react-select";
import { Plus } from "lucide-react";
import { useEffect, useRef } from "react";

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";

import { ConnectionEditor } from "./ConnectionEditor";
import type { ModelConnectionCatalog, ProviderConnection, ServiceRole } from "./types";

const ADD = "__add_provider__";

export interface ProviderOption {
    value: string;
    label: string;
    /** The connection's own name, when it says more than the provider does. */
    detail?: string;
}

/**
 * A provider connection picker that ends with a way to add one, so the
 * providers not yet connected are one click away instead of on another page.
 */
export function ProviderSelect({ id, value, options, onValueChange, onAdd, addLabel, addHint, placeholder, required }: {
    id: string;
    value: string;
    options: ProviderOption[];
    onValueChange: (value: string) => void;
    onAdd: () => void;
    addLabel: string;
    /** Providers that could be added, named under the add entry. */
    addHint?: string;
    placeholder: string;
    required?: boolean;
}) {
    const trigger = useRef<HTMLButtonElement>(null);
    const guard = useRef<HTMLInputElement>(null);
    // A select left on its placeholder still passes native validation, since
    // the browser selects its first option. This input is what blocks the form.
    useEffect(() => { guard.current?.setCustomValidity(value ? "" : `${placeholder}.`); }, [value, placeholder, required]);
    return <div className="relative">
        <Select value={value} required={required} onValueChange={next => {
            if (next === ADD) onAdd();
            else onValueChange(next);
        }}>
            <SelectTrigger ref={trigger} id={id} className="w-full min-w-0"><SelectValue placeholder={placeholder} /></SelectTrigger>
            <SelectContent>
                {options.map(option => <SelectItem key={option.value} value={option.value}>
                    <span className="truncate">{option.label}</span>
                    {option.detail && <>{" "}<span className="truncate text-muted-foreground">{option.detail}</span></>}
                </SelectItem>)}
                {options.length > 0 && <SelectSeparator />}
                <SelectPrimitive.Item value={ADD} className="relative flex w-full cursor-default select-none items-start gap-2 rounded-sm py-1.5 pl-2 pr-8 text-sm outline-hidden focus:bg-accent focus:text-accent-foreground">
                    <Plus className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0">
                        <SelectPrimitive.ItemText>{addLabel}</SelectPrimitive.ItemText>
                        {addHint && <span className="block truncate text-xs text-muted-foreground">{addHint}</span>}
                    </span>
                </SelectPrimitive.Item>
            </SelectContent>
        </Select>
        {/* Not read-only: read-only inputs are left out of validation. */}
        {required && <input ref={guard} tabIndex={-1} aria-hidden value={value} onChange={() => undefined} onFocus={() => trigger.current?.focus()}
            className="pointer-events-none absolute inset-x-0 bottom-0 h-px w-full opacity-0" />}
    </div>;
}

/** The provider connection form, in a dialog over the model configuration. */
export function AddProviderDialog({ open, onOpenChange, title, catalog, providerKeys, role, onSaved }: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    catalog: ModelConnectionCatalog;
    providerKeys: string[];
    role?: ServiceRole;
    onSaved: (connection: ProviderConnection) => void;
}) {
    return <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
            <DialogHeader>
                <DialogTitle>{title}</DialogTitle>
                <DialogDescription>Connect an account with its API key. It is saved to Providers, so every model configuration can use it.</DialogDescription>
            </DialogHeader>
            <ConnectionEditor embedded catalog={catalog} providerKeys={providerKeys} role={role} onSaved={async (_, connection) => onSaved(connection)} />
        </DialogContent>
    </Dialog>;
}
