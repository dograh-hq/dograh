"use client";

import { ExternalLink, Plus, X } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VoiceSelector } from "@/components/VoiceSelector";
import { VoiceSelectorModal } from "@/components/VoiceSelectorModal";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";

import { fieldForModel, isFieldVisible, resolveFieldSchema, schemaDefaults } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import type { FieldSchema, ServiceRole } from "./types";

const PROVIDER_DEFAULT = "__provider_default__";

function optionLabel(name: string, value: string) {
    if (name === "language" || name === "language_hints") return LANGUAGE_DISPLAY_NAMES[value] || value;
    return value;
}

function JsonInput({ id, value, onChange, required }: { id: string; value: unknown; onChange: (value: unknown) => void; required: boolean }) {
    const serialized = value === undefined ? "" : JSON.stringify(value, null, 2);
    const [text, setText] = useState(serialized);
    useEffect(() => { setText(serialized); }, [serialized]);
    return <Textarea id={id} value={text} required={required} rows={5} className="font-mono text-xs" onChange={event => {
        const next = event.target.value;
        setText(next);
        try {
            const parsed = next.trim() ? JSON.parse(next) : undefined;
            event.target.setCustomValidity("");
            onChange(parsed);
        } catch { event.target.setCustomValidity("Enter valid JSON."); }
    }} />;
}

function StringListInput({ id, name, values, onChange, secret, required }: {
    id: string; name: string; values: string[]; onChange: (values: string[]) => void; secret: boolean; required: boolean;
}) {
    const label = name === "api_key" ? "API key" : name.replaceAll("_", " ");
    return <div className="space-y-2">
        {values.map((value, index) => <div key={index} className="flex gap-2">
            <Input id={index === 0 ? id : `${id}-${index}`} aria-label={index ? `${label} ${index + 1}` : undefined}
                type={secret ? "password" : "text"} autoComplete={secret ? "new-password" : "off"}
                placeholder={`Enter ${label}`} value={value} required={required && index === 0}
                onChange={event => onChange(values.map((item, i) => i === index ? event.target.value : item))} />
            {(!secret || values.length > 1) && <Button type="button" variant="ghost" size="icon" className="shrink-0" aria-label={`Remove ${label} ${index + 1}`}
                onClick={() => onChange(values.filter((_, i) => i !== index))}><X className="h-4 w-4" /></Button>}
        </div>)}
        <Button type="button" variant="outline" size="sm" onClick={() => onChange([...values, ""])}>
            <Plus className="h-4 w-4" />{name === "api_key" ? "Add API Key" : `Add ${label}`}
        </Button>
    </div>;
}

function ValueInput({ id, name, schema, value, onChange, secret, required, provider, role, model }: {
    id: string; name: string; schema: FieldSchema; value: unknown; onChange: (value: unknown) => void;
    secret: boolean; required: boolean; provider?: string; role?: ServiceRole; model?: string;
}) {
    const alternatives = schema.anyOf || [schema];
    const nullable = alternatives.some(option => option.type === "null");
    const concrete = alternatives.find(option => option.type !== "null") || schema;
    const numeric = alternatives.find(option => option.type === "number" || option.type === "integer");
    const canPool = secret && alternatives.some(option => option.type === "array") && alternatives.some(option => option.type === "string");
    const options = schema.enum || concrete.enum || schema.examples;
    const [custom, setCustom] = useState(false);

    if (canPool) {
        return <StringListInput id={id} name={name} secret required={required} values={Array.isArray(value) ? value : [String(value || "")]}
            onChange={next => onChange(next.length === 1 ? next[0] : next)} />;
    }
    if (role === "tts" && name === "voice" && provider) {
        if (provider === "dograh") return <VoiceSelectorModal id={id} provider={provider} value={String(value || "")} onChange={onChange} allowManualInput={schema.allow_custom_input} />;
        if (!schema.allow_custom_input && !options?.length) return <VoiceSelector id={id} provider={provider} value={String(value || "")} onChange={onChange} model={model} />;
    }
    if (concrete.type === "array") {
        if (concrete.items?.type && concrete.items.type !== "string") return <JsonInput id={id} value={value} onChange={onChange} required={required} />;
        const values: string[] = Array.isArray(value) ? value : [];
        const choices = options || concrete.items?.enum || concrete.items?.examples;
        if (choices?.length) return <div role="group" aria-labelledby={`${id}-label`} className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {[...choices, ...values.filter(item => !choices.includes(item))].map(option => <div key={option} className="flex items-center gap-2">
                <Checkbox id={`${id}-${option}`} checked={values.includes(option)} onCheckedChange={checked => onChange(checked === true ? [...values, option] : values.filter(item => item !== option))} />
                <Label htmlFor={`${id}-${option}`} className="cursor-pointer text-sm font-normal">{optionLabel(name, option)}</Label>
            </div>)}
        </div>;
        return <StringListInput id={id} name={name} secret={secret} required={required} values={values} onChange={onChange} />;
    }
    if (numeric) {
        return <Input id={id} type="number" value={value == null ? "" : String(value)} required={required && !nullable}
            min={schema.minimum ?? numeric.minimum} max={schema.maximum ?? numeric.maximum}
            step={numeric.type === "integer" ? 1 : "any"} placeholder={nullable ? "Provider default" : undefined}
            onChange={event => onChange(event.target.value === "" ? (nullable ? null : undefined) : Number(event.target.value))} />;
    }
    if (concrete.type === "boolean") {
        return <ConfigurationSelect id={id} value={value == null ? (nullable ? PROVIDER_DEFAULT : "") : String(value)}
            onValueChange={next => onChange(next === PROVIDER_DEFAULT ? null : next === "true")}
            options={[...(nullable ? [{ value: PROVIDER_DEFAULT, label: "Provider default" }] : []), { value: "true", label: "Enabled" }, { value: "false", label: "Disabled" }]} />;
    }
    if (concrete.type === "object" || concrete.properties) return <JsonInput id={id} value={value} onChange={onChange} required={required} />;
    if (schema.multiline) return <Textarea id={id} rows={4} required={required} autoComplete="off" spellCheck={false} className="font-mono text-xs"
        value={value == null ? "" : String(value)} onChange={event => onChange(event.target.value || (nullable ? null : ""))} />;
    if (!secret && options?.length) {
        const current = value == null ? "" : String(value);
        const customInput = schema.allow_custom_input && (custom || Boolean(current && !options.includes(current)));
        return <div className="space-y-2">
            {customInput ? <Input id={id} value={current} required={required} placeholder={`Enter ${name.replaceAll("_", " ")}`} onChange={event => onChange(event.target.value)} />
                : <ConfigurationSelect id={id} value={value == null && nullable ? PROVIDER_DEFAULT : current} required={required}
                    placeholder={`Select ${name.replaceAll("_", " ")}`} onValueChange={next => onChange(next === PROVIDER_DEFAULT ? null : next)}
                    options={[...(nullable ? [{ value: PROVIDER_DEFAULT, label: "Provider default" }] : []),
                        ...[...options, ...(!schema.allow_custom_input && current && !options.includes(current) ? [current] : [])].map(option => ({ value: option, label: optionLabel(name, option) }))]} />}
            {schema.allow_custom_input && <div className="flex items-center gap-2">
                <Checkbox id={`${id}-custom`} checked={Boolean(customInput)} onCheckedChange={checked => {
                    setCustom(checked === true);
                    if (!checked) onChange(options[0]);
                }} />
                <Label htmlFor={`${id}-custom`} className="cursor-pointer text-sm font-normal">Enter Custom Value</Label>
            </div>}
        </div>;
    }
    return <Input id={id} type={secret ? "password" : "text"} value={value == null ? "" : String(value)} required={required}
        autoComplete={secret ? "new-password" : "off"} placeholder={nullable ? "Provider default" : undefined}
        onChange={event => onChange(event.target.value || (nullable ? null : ""))} />;
}

export function SchemaFields({ schema, values, onChange, secret = false, configuredFields = [], context = {}, provider, role, className = "grid grid-cols-1 gap-4 sm:grid-cols-2" }: {
    schema: FieldSchema; values: Record<string, unknown>; onChange: (name: string, value: unknown) => void;
    secret?: boolean; configuredFields?: string[];
    context?: Record<string, unknown>; provider?: string; role?: ServiceRole;
    /** "contents" lays the fields into the parent's two-column grid. */
    className?: string;
}) {
    const prefix = useId();
    const [replacing, setReplacing] = useState<string[]>([]);
    const resolvedValues = { ...schemaDefaults(schema), ...values };
    return <div className={className}>
        {Object.entries(schema.properties || {}).map(([name, raw]) => {
            const field = fieldForModel(resolveFieldSchema(raw, schema), { ...context, ...resolvedValues });
            if (!isFieldVisible(field, resolvedValues.model)) return null;
            const id = `${prefix}-${name}`;
            const configured = secret && configuredFields.includes(name);
            const editing = !configured || replacing.includes(name);
            const removing = configured && values[name] === null;
            const suggestions = field.model_options?.[String(resolvedValues.model || "")];
            const label = field.title || name.replaceAll("_", " ");
            const fullWidth = field.multiline || field.type === "array" || field.anyOf?.some(option => option.type === "array") || (role === "tts" && name === "voice");
            return <div key={name} className={`space-y-1.5 ${fullWidth ? "sm:col-span-2" : ""}`}>
                <div className="flex min-h-6 items-center justify-between gap-2">
                    <Label id={`${id}-label`} htmlFor={id} className="capitalize">{label}</Label>
                </div>
                {editing ? <ValueInput id={id} name={name} provider={provider} role={role} model={String(resolvedValues.model || "") || undefined}
                    schema={suggestions ? { ...field, examples: suggestions, enum: field.enum ? suggestions : undefined } : field}
                    value={Object.hasOwn(values, name) ? values[name] : field.default} secret={secret}
                    required={Boolean(schema.required?.includes(name))} onChange={value => onChange(name, value)} />
                    : <div className="flex h-10 items-center justify-between rounded-md border px-3 text-sm">
                        <span className="text-muted-foreground">{removing ? "Will be removed" : "Configured"}</span>
                        {removing ? <Button type="button" size="sm" variant="ghost" onClick={() => onChange(name, undefined)}>Keep existing credential</Button> : <div className="flex">
                            {!schema.required?.includes(name) && <Button type="button" size="sm" variant="ghost" onClick={() => onChange(name, null)}>Remove</Button>}
                            <Button type="button" size="sm" variant="ghost" onClick={() => setReplacing(previous => [...previous, name])}>Replace</Button>
                        </div>}
                    </div>}
                {configured && editing && <button type="button" className="text-xs underline" onClick={() => {
                    setReplacing(previous => previous.filter(field => field !== name));
                    onChange(name, undefined);
                }}>Keep existing credential</button>}
                {(field.description || field.docs_url) && <p className="text-xs text-muted-foreground">{field.description}{" "}
                    {field.docs_url && <a href={field.docs_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                        {field.docs_label || "Learn more"}<ExternalLink className="h-3 w-3" />
                    </a>}
                </p>}
            </div>;
        })}
    </div>;
}
