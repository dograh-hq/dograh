import { useTranslations } from "next-intl";
import { PlusIcon, Trash2Icon } from "lucide-react";

import type {
    DocumentResponseSchema,
    PropertySpec,
    RecordingResponseSchema,
    ToolResponse,
} from "@/client/types.gen";
import { DocumentSelector } from "@/components/flow/DocumentSelector";
import { MentionTextarea } from "@/components/flow/MentionTextarea";
import { RecordingSelect } from "@/components/flow/TextOrAudioInput";
import { ToolSelector } from "@/components/flow/ToolSelector";
import { CredentialSelector, UrlInput } from "@/components/http";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { evaluateDisplayOptions } from "./displayOptions";
import {
    getPropertyColumnSpan,
    isFractionalNumberInput,
} from "./propertyRendererOptions";

export interface RendererContext {
    tools: ToolResponse[];
    documents: DocumentResponseSchema[];
    recordings: RecordingResponseSchema[];
    /** Per-node MCP function allowlist (sibling of tool_uuids on node data). */
    mcpToolFilters?: Record<string, string[]>;
    /** Persist a new mcp_tool_filters object onto the node form values. */
    onMcpToolFiltersChange?: (next: Record<string, string[]>) => void;
}

export interface PropertyInputProps {
    spec: PropertySpec;
    value: unknown;
    onChange: (value: unknown) => void;
    context: RendererContext;
    /** The node type name (e.g. 'startCall', 'agentNode') used for i18n lookups. */
    nodeType?: string;
}

/**
 * Generic property dispatcher. Renders the right widget based on
 * `spec.type` and the standard label/description layout. Widgets that
 * already own their own label structure (Tool/DocumentSelector) are told
 * to suppress it via `showLabel={false}`.
 *
 * Caller is responsible for evaluating `display_options` — `PropertyInput`
 * always renders. NodeEditForm filters out hidden properties before
 * mounting them.
 */
export function PropertyInput({ spec, value, onChange, context, nodeType }: PropertyInputProps) {
    switch (spec.type) {
        case "string":
            return <StringWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "number":
            return <NumberWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "boolean":
            return <BooleanWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "options":
            return <OptionsWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "multi_options":
            return <MultiOptionsWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "fixed_collection":
            return (
                <FixedCollectionWidget
                    spec={spec}
                    value={value}
                    onChange={onChange}
                    context={context}
                    nodeType={nodeType}
                />
            );
        case "json":
            return <JsonWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "url":
            return <UrlWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        case "mention_textarea":
            return (
                <MentionWidget
                    spec={spec}
                    value={value}
                    onChange={onChange}
                    recordings={context.recordings}
                    nodeType={nodeType}
                />
            );
        case "tool_refs":
            return (
                <ToolRefsWidget
                    spec={spec}
                    value={value}
                    onChange={onChange}
                    tools={context.tools}
                    mcpToolFilters={context.mcpToolFilters ?? {}}
                    onMcpToolFiltersChange={
                        context.onMcpToolFiltersChange ?? (() => {})
                    }
                    nodeType={nodeType}
                />
            );
        case "document_refs":
            return (
                <DocumentRefsWidget
                    spec={spec}
                    value={value}
                    onChange={onChange}
                    documents={context.documents}
                    nodeType={nodeType}
                />
            );
        case "recording_ref":
            return (
                <RecordingRefWidget
                    spec={spec}
                    value={value}
                    onChange={onChange}
                    recordings={context.recordings}
                    nodeType={nodeType}
                />
            );
        case "credential_ref":
            return <CredentialRefWidget spec={spec} value={value} onChange={onChange} nodeType={nodeType} />;
        default: {
            const exhaustiveCheck: never = spec.type;
            return (
                <div className="text-xs text-destructive">
                    Unknown property type: {String(exhaustiveCheck)}
                </div>
            );
        }
    }
}

// ─── i18n helpers ────────────────────────────────────────────────────────

/** Resolve a translated label for a node field, falling back to spec.display_name. */
function useFieldLabel(nodeType: string | undefined, fieldName: string, fallback: string): string {
    const t = useTranslations("nodeFields");
    const et = useTranslations("extractionFields");
    if (nodeType) {
        const key = `${nodeType}.${fieldName}`;
        const translated = t(key);
        if (translated !== key) return translated;
    }
    // Fallback: try standalone extractionFields key
    const standalone = et(fieldName);
    if (standalone !== fieldName) return standalone;
    return fallback;
}

function useFieldDescription(nodeType: string | undefined, fieldName: string, fallback?: string): string | undefined {
    const t = useTranslations("nodeFields");
    const et = useTranslations("extractionFields");
    if (nodeType) {
        const key = `${nodeType}.${fieldName}_desc`;
        const translated = t(key);
        if (translated !== key) return translated;
    }
    const standalone = et(`${fieldName}_desc`);
    if (standalone !== `${fieldName}_desc`) return standalone;
    return fallback;
}

function useFieldPlaceholder(nodeType: string | undefined, fieldName: string, fallback?: string): string | undefined {
    const t = useTranslations("nodeFields");
    if (!nodeType) return fallback;
    const key = `${nodeType}.${fieldName}_placeholder`;
    const translated = t(key);
    return translated !== key ? translated : fallback;
}

function useFieldOptionLabel(nodeType: string | undefined, fieldName: string, optionValue: string, fallback: string): string {
    const t = useTranslations("nodeFields");
    const et = useTranslations("extractionFields");
    if (nodeType) {
        const key = `${nodeType}.${fieldName}_option_${optionValue}`;
        const translated = t(key);
        if (translated !== key) return translated;
    }
    const standalone = et(`${fieldName}_option_${optionValue}`);
    if (standalone !== `${fieldName}_option_${optionValue}`) return standalone;
    return fallback;
}

// ─── Layout helpers ──────────────────────────────────────────────────────

function StackedLabel({ spec, nodeType }: { spec: PropertySpec; nodeType?: string }) {
    const label = useFieldLabel(nodeType, spec.name, spec.display_name ?? spec.name);
    const desc = useFieldDescription(nodeType, spec.name, spec.description ?? undefined);
    return (
        <>
            <Label>
                {label}
                {spec.required && <span className="text-destructive ml-1">*</span>}
            </Label>
            {desc && (
                <Label className="text-xs text-muted-foreground">{desc}</Label>
            )}
        </>
    );
}

// ─── Widgets ─────────────────────────────────────────────────────────────

interface WidgetProps {
    spec: PropertySpec;
    value: unknown;
    onChange: (v: unknown) => void;
}

function StringWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    const v = (value as string | undefined) ?? "";
    const isMultiline = spec.editor === "textarea";
    const placeholder = useFieldPlaceholder(nodeType, spec.name, spec.placeholder ?? undefined);
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            {isMultiline ? (
                <Textarea
                    value={v}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={placeholder}
                    className="min-h-[80px] max-h-[200px] resize-none"
                    style={{ overflowY: "auto" }}
                />
            ) : (
                <Input
                    value={v}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={placeholder}
                />
            )}
        </div>
    );
}

function NumberWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    const v = (value as number | undefined) ?? "";
    const isCompact = getPropertyColumnSpan(spec.renderer_options) < 12;
    const isFractional = isFractionalNumberInput(spec.renderer_options);
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <Input
                type="number"
                value={v as number | string}
                step={
                    isFractional
                        ? "any"
                        : spec.min_value && spec.min_value < 1
                          ? 0.1
                          : 1
                }
                min={spec.min_value ?? undefined}
                max={spec.max_value ?? undefined}
                onChange={(e) => {
                    const next = e.target.value;
                    onChange(next === "" ? undefined : parseFloat(next));
                }}
                placeholder={spec.placeholder ?? undefined}
                className={isCompact ? "w-full" : "w-32"}
            />
        </div>
    );
}

function BooleanWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    const v = !!value;
    const label = useFieldLabel(nodeType, spec.name, spec.display_name ?? spec.name);
    const desc = useFieldDescription(nodeType, spec.name, spec.description ?? undefined);
    return (
        <div className="flex items-center space-x-2">
            <Switch id={`prop-${spec.name}`} checked={v} onCheckedChange={onChange} />
            <Label htmlFor={`prop-${spec.name}`}>{label}</Label>
            {desc && (
                <Label className="text-xs text-muted-foreground ml-2">
                    {desc}
                </Label>
            )}
        </div>
    );
}

function OptionsWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <select
                className="border rounded-md p-2 text-sm bg-background"
                value={(value as string | number | undefined) ?? ""}
                onChange={(e) => {
                    const raw = e.target.value;
                    const opt = spec.options?.find((o) => String(o.value) === raw);
                    onChange(opt?.value ?? raw);
                }}
            >
                {spec.options?.map((o) => (
                    <option key={String(o.value)} value={String(o.value)}>
                        {useFieldOptionLabel(nodeType, spec.name, String(o.value), o.label)}
                    </option>
                ))}
            </select>
        </div>
    );
}

function MultiOptionsWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    const selected = new Set(((value as unknown[]) ?? []).map((v) => String(v)));
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <div className="flex flex-col gap-1 border rounded-md p-2">
                {spec.options?.map((o) => {
                    const key = String(o.value);
                    const optLabel = useFieldOptionLabel(nodeType, spec.name, key, o.label);
                    return (
                        <label key={key} className="flex items-center gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={selected.has(key)}
                                onChange={(e) => {
                                    const next = new Set(selected);
                                    if (e.target.checked) next.add(key);
                                    else next.delete(key);
                                    onChange(
                                        spec.options
                                            ?.filter((opt) => next.has(String(opt.value)))
                                            .map((opt) => opt.value) ?? [],
                                    );
                                }}
                            />
                            {optLabel}
                        </label>
                    );
                })}
            </div>
        </div>
    );
}

function FixedCollectionWidget({
    spec,
    value,
    onChange,
    context,
    nodeType,
}: WidgetProps & { context: RendererContext; nodeType?: string }) {
    const t = useTranslations("nodeEditor");
    const nt = useTranslations("nodeFields");
    // Try node-specific add label first, fallback to "Add"
    const addKey = nodeType ? `${nodeType}.${spec.name}_add` : null;
    const addLabel = addKey ? (nt(addKey) !== addKey ? nt(addKey) : t("add")) : t("add");
    const rows = (value as Array<Record<string, unknown>> | undefined) ?? [];
    const subProps = spec.properties ?? [];

    const handleRowChange = (idx: number, propName: string, propValue: unknown) => {
        const next = rows.map((row, i) =>
            i === idx ? { ...row, [propName]: propValue } : row,
        );
        onChange(next);
    };

    const handleRemove = (idx: number) => {
        onChange(rows.filter((_, i) => i !== idx));
    };

    const handleAdd = () => {
        const blank: Record<string, unknown> = {};
        for (const p of subProps) blank[p.name] = p.default ?? undefined;
        onChange([...rows, blank]);
    };

    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <div className="space-y-2">
                {rows.map((row, idx) => (
                    <div key={idx} className="border rounded-md p-2 bg-background space-y-2">
                        <div className="flex items-start gap-2">
                            <div className="flex-1 space-y-2">
                                {subProps
                                    .filter((sub) =>
                                        evaluateDisplayOptions(sub.display_options, row),
                                    )
                                    .map((sub) => (
                                        <PropertyInput
                                            key={sub.name}
                                            spec={sub}
                                            value={row[sub.name]}
                                            onChange={(v) =>
                                                handleRowChange(idx, sub.name, v)
                                            }
                                            context={context}
                                        />
                                    ))}
                            </div>
                            <Button
                                variant="outline"
                                size="icon"
                                onClick={() => handleRemove(idx)}
                                aria-label={`Remove row ${idx + 1}`}
                            >
                                <Trash2Icon className="w-4 h-4" />
                            </Button>
                        </div>
                    </div>
                ))}
                <Button variant="outline" size="sm" className="w-fit" onClick={handleAdd}>
                    <PlusIcon className="w-4 h-4 mr-1" /> {addLabel}
                </Button>
            </div>
        </div>
    );
}

function JsonWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    // Render as a textarea with JSON serialization. Invalid JSON keeps the
    // raw text so the user can finish editing without losing input.
    const text = (() => {
        if (value === undefined || value === null) return "";
        if (typeof value === "string") return value;
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return String(value);
        }
    })();

    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <Textarea
                value={text}
                onChange={(e) => {
                    const raw = e.target.value;
                    try {
                        onChange(raw === "" ? undefined : JSON.parse(raw));
                    } catch {
                        // Keep raw string in state until it parses; downstream
                        // serialization picks it up as-is.
                        onChange(raw);
                    }
                }}
                placeholder={spec.placeholder ?? "{ }"}
                className="font-mono text-xs min-h-[120px]"
            />
        </div>
    );
}

function UrlWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <UrlInput
                value={(value as string | undefined) ?? ""}
                onChange={onChange}
                placeholder={spec.placeholder ?? undefined}
                showValidation
            />
        </div>
    );
}

function MentionWidget({
    spec,
    value,
    onChange,
    recordings,
    nodeType,
}: WidgetProps & { recordings: RecordingResponseSchema[]; nodeType?: string }) {
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <MentionTextarea
                value={(value as string | undefined) ?? ""}
                onChange={onChange}
                placeholder={spec.placeholder ?? undefined}
                className="min-h-[100px] max-h-[300px] resize-none overflow-y-auto"
                recordings={recordings}
            />
        </div>
    );
}

function ToolRefsWidget({
    spec,
    value,
    onChange,
    tools,
    mcpToolFilters,
    onMcpToolFiltersChange,
    nodeType,
}: WidgetProps & {
    tools: ToolResponse[];
    mcpToolFilters: Record<string, string[]>;
    onMcpToolFiltersChange: (next: Record<string, string[]>) => void;
    nodeType?: string;
}) {
    const label = useFieldLabel(nodeType, spec.name, spec.display_name ?? spec.name);
    const desc = useFieldDescription(nodeType, spec.name, spec.description ?? undefined);
    return (
        <ToolSelector
            value={(value as string[] | undefined) ?? []}
            onChange={onChange}
            tools={tools}
            label={label}
            description={desc}
            mcpToolFilters={mcpToolFilters}
            onMcpToolFiltersChange={onMcpToolFiltersChange}
        />
    );
}

function DocumentRefsWidget({
    spec,
    value,
    onChange,
    documents,
    nodeType,
}: WidgetProps & { documents: DocumentResponseSchema[]; nodeType?: string }) {
    const label = useFieldLabel(nodeType, spec.name, spec.display_name ?? spec.name);
    const desc = useFieldDescription(nodeType, spec.name, spec.description ?? undefined);
    return (
        <DocumentSelector
            value={(value as string[] | undefined) ?? []}
            onChange={onChange}
            documents={documents}
            label={label}
            description={desc}
        />
    );
}

function RecordingRefWidget({
    spec,
    value,
    onChange,
    recordings,
    nodeType,
}: WidgetProps & { recordings: RecordingResponseSchema[]; nodeType?: string }) {
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <RecordingSelect
                value={(value as string | undefined) ?? ""}
                onChange={onChange}
                recordings={recordings}
            />
        </div>
    );
}

function CredentialRefWidget({ spec, value, onChange, nodeType }: WidgetProps & { nodeType?: string }) {
    return (
        <div className="grid gap-2">
            <StackedLabel spec={spec} nodeType={nodeType} />
            <CredentialSelector
                value={(value as string | undefined) ?? ""}
                onChange={onChange}
                showLabel={false}
            />
        </div>
    );
}
