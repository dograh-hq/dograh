"use client";

import {
    AlertCircle,
    CheckCircle2,
    ExternalLink,
    HelpCircle,
    Pencil,
    Phone,
    Plus,
    RefreshCw,
    Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet } from "@/client/sdk.gen";
import type {
    WhatsAppPhoneNumberItem,
    WhatsAppTemplateConfig,
    WhatsAppTemplateItem,
    WhatsAppVariableMapping,
} from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export interface WhatsAppToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    executionMode: "async" | "sync";
    onExecutionModeChange: (mode: "async" | "sync") => void;
    fallbackRecipientPhone?: string;
    onFallbackRecipientPhoneChange?: (phone: string) => void;
    templates: WhatsAppTemplateConfig[];
    onTemplatesChange: (templates: WhatsAppTemplateConfig[]) => void;
    telephonyConfigId?: number | null;
}

export function WhatsAppToolConfig({
    name,
    onNameChange,
    description,
    onDescriptionChange,
    executionMode,
    onExecutionModeChange,
    fallbackRecipientPhone = "",
    onFallbackRecipientPhoneChange,
    templates,
    onTemplatesChange,
    telephonyConfigId,
}: WhatsAppToolConfigProps) {
    const { getAccessToken } = useAuth();
    const [availableTemplates, setAvailableTemplates] = useState<WhatsAppTemplateItem[]>([]);
    const [availablePhoneNumbers, setAvailablePhoneNumbers] = useState<WhatsAppPhoneNumberItem[]>([]);
    const [isLoadingData, setIsLoadingData] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    // Inline edit state
    const [editingId, setEditingId] = useState<string | null>(null);
    const [draftTemplate, setDraftTemplate] = useState<WhatsAppTemplateConfig | null>(null);
    const [isNewTemplate, setIsNewTemplate] = useState(false);
    const [validationError, setValidationError] = useState<string | null>(null);
    const draftCardRef = useRef<HTMLDivElement>(null);

    const fetchData = useCallback(async () => {
        try {
            setIsLoadingData(true);
            setLoadError(null);
            const token = await getAccessToken();
            const res = await listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet({
                query: telephonyConfigId ? { config_id: telephonyConfigId } : undefined,
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });
            if (res.data) {
                if (res.data.templates) {
                    setAvailableTemplates(res.data.templates);
                }
                if (res.data.phone_numbers) {
                    setAvailablePhoneNumbers(res.data.phone_numbers);
                }
            } else if (res.error) {
                setLoadError(
                    "Could not load WhatsApp templates or phone numbers. Ensure your WhatsApp Business Account is configured in Telephony settings."
                );
            }
        } catch (err: unknown) {
            setLoadError(err instanceof Error ? err.message : "Failed to fetch WhatsApp configuration");
        } finally {
            setIsLoadingData(false);
        }
    }, [getAccessToken, telephonyConfigId]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    useEffect(() => {
        if (isNewTemplate && draftCardRef.current?.scrollIntoView) {
            draftCardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }, [isNewTemplate]);

    const isTemplateIncomplete = (t: WhatsAppTemplateConfig) => {
        if (!t.template_name) return true;
        if (!t.name?.trim()) return true;
        if (t.variable_mappings && t.variable_mappings.some((v) => !v.name?.trim())) return true;
        return false;
    };

    const handleStartAdd = () => {
        const nextId = String(templates.length + 1);
        const newTpl: WhatsAppTemplateConfig = {
            id: nextId,
            name: `Template ${nextId}`,
            description: "",
            template_name: "",
            template_id: null,
            language_code: "en_US",
            from_phone_number_id: null,
            variable_mappings: [],
        };
        setDraftTemplate(newTpl);
        setEditingId(nextId);
        setIsNewTemplate(true);
        setValidationError(null);
        setTimeout(() => {
            if (draftCardRef.current?.scrollIntoView) {
                draftCardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
            }
        }, 50);
    };

    const handleStartEdit = (tpl: WhatsAppTemplateConfig) => {
        setDraftTemplate({
            ...tpl,
            variable_mappings: tpl.variable_mappings ? [...tpl.variable_mappings] : [],
        });
        setEditingId(tpl.id);
        setIsNewTemplate(false);
        setValidationError(null);
    };

    const handleCancelEdit = () => {
        setEditingId(null);
        setDraftTemplate(null);
        setIsNewTemplate(false);
        setValidationError(null);
    };

    const handleMetaTemplateSelectInDraft = (val: string) => {
        if (!draftTemplate) return;
        const found = availableTemplates.find(
            (tpl) => `${tpl.name}:${tpl.language}` === val || tpl.name === val
        );

        if (found) {
            const mappings: WhatsAppVariableMapping[] = (found.variables || []).map((v) => {
                const existing = draftTemplate.variable_mappings?.find((m) => m.index === v.index);
                if (existing) {
                    return existing;
                }
                return {
                    index: v.index,
                    name: v.name || `param_${v.index}`,
                    source: "llm",
                    value: v.example ? `Example: ${v.example}` : null,
                    fallback_value: null,
                };
            });

            setDraftTemplate({
                ...draftTemplate,
                template_name: found.name,
                template_id: found.id,
                language_code: found.language,
                name: !draftTemplate.name || draftTemplate.name === `Template ${draftTemplate.id}`
                    ? found.name
                    : draftTemplate.name,
                variable_mappings: mappings,
            });
            setValidationError(null);
        }
    };

    const handleUpdateDraftVariable = (varIndex: number, patch: Partial<WhatsAppVariableMapping>) => {
        if (!draftTemplate) return;
        const updated = (draftTemplate.variable_mappings || []).map((v) =>
            v.index === varIndex ? { ...v, ...patch } : v
        );
        setDraftTemplate({
            ...draftTemplate,
            variable_mappings: updated,
        });
    };

    const handleSaveDraft = () => {
        if (!draftTemplate) return;

        const trimmedName = draftTemplate.name?.trim() || "";
        if (!trimmedName) {
            setValidationError("Template display name is required.");
            return;
        }

        if (!draftTemplate.template_name) {
            setValidationError("Please select an approved Meta WhatsApp template.");
            return;
        }

        if (draftTemplate.variable_mappings) {
            for (const v of draftTemplate.variable_mappings) {
                if (!v.name?.trim()) {
                    setValidationError(`Variable {{${v.index}}} parameter name cannot be empty.`);
                    return;
                }
            }
        }

        const finalized: WhatsAppTemplateConfig = {
            ...draftTemplate,
            name: trimmedName,
            description: draftTemplate.description?.trim() || "",
            from_phone_number_id: draftTemplate.from_phone_number_id || null,
        };

        if (isNewTemplate) {
            onTemplatesChange([...templates, finalized]);
        } else {
            onTemplatesChange(
                templates.map((t) => (t.id === finalized.id ? finalized : t))
            );
        }

        setEditingId(null);
        setDraftTemplate(null);
        setIsNewTemplate(false);
        setValidationError(null);
    };

    const handleRemoveTemplate = (templateIdToRemove: string) => {
        if (editingId === templateIdToRemove) {
            handleCancelEdit();
        }
        const remaining = templates.filter((t) => t.id !== templateIdToRemove);
        const renumbered = remaining.map((t, idx) => ({
            ...t,
            id: String(idx + 1),
        }));
        onTemplatesChange(renumbered);
    };

    // Helper to render the inline edit form for a template
    const renderInlineEditForm = (tpl: WhatsAppTemplateConfig) => {
        const selectedMetaTemplate = availableTemplates.find(
            (meta) =>
                meta.name === tpl.template_name &&
                (!tpl.language_code || meta.language === tpl.language_code)
        ) || availableTemplates.find((meta) => meta.name === tpl.template_name);

        return (
            <Card className="border-2 border-primary/50 shadow-md bg-card overflow-hidden">
                <CardHeader className="bg-muted/30 pb-3 border-b">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                            <Badge variant="outline" className="font-mono text-xs font-semibold px-2">
                                #{tpl.id}
                            </Badge>
                            <span className="font-semibold text-sm">
                                {isNewTemplate ? `New Template #${tpl.id}` : `Editing Template #${tpl.id}`}
                            </span>
                            <Badge variant="secondary" className="font-mono text-xs">
                                LLM Choice: &quot;{tpl.id}&quot;
                            </Badge>
                        </div>
                        <div className="flex items-center gap-2">
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={handleCancelEdit}
                            >
                                Cancel
                            </Button>
                            <Button
                                type="button"
                                size="sm"
                                onClick={handleSaveDraft}
                            >
                                Done
                            </Button>
                        </div>
                    </div>
                </CardHeader>

                <CardContent className="p-4 space-y-4">
                    {validationError && (
                        <div className="flex items-center gap-2 p-3 text-sm rounded-lg bg-destructive/10 text-destructive border border-destructive/20">
                            <AlertCircle className="w-4 h-4 shrink-0" />
                            <span>{validationError}</span>
                        </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Display Name */}
                        <div className="space-y-1.5">
                            <Label className="text-sm font-medium">
                                Template Display Name <span className="text-destructive">*</span>
                            </Label>
                            <Input
                                value={tpl.name || ""}
                                autoFocus={isNewTemplate}
                                onChange={(e) => {
                                    setDraftTemplate({ ...tpl, name: e.target.value });
                                    if (validationError) setValidationError(null);
                                }}
                                placeholder="e.g., Booking Confirmation, Payment Link"
                            />
                            <p className="text-xs text-muted-foreground">
                                Friendly title for this template in Dograh.
                            </p>
                        </div>

                        {/* Sender Phone */}
                        <div className="space-y-1.5">
                            <Label className="text-sm font-medium">Sender Phone Number</Label>
                            <Select
                                value={tpl.from_phone_number_id || "default"}
                                onValueChange={(val) =>
                                    setDraftTemplate({
                                        ...tpl,
                                        from_phone_number_id: val === "default" ? null : val,
                                    })
                                }
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Use default account number" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="default">
                                        <span className="text-muted-foreground">Default account phone number</span>
                                    </SelectItem>
                                    {availablePhoneNumbers.map((phone) => (
                                        <SelectItem key={phone.phone_number_id} value={phone.phone_number_id}>
                                            <div className="flex items-center gap-2">
                                                <Phone className="w-3.5 h-3.5 text-muted-foreground" />
                                                <span className="font-mono">{phone.display_phone_number}</span>
                                                {phone.verified_name && (
                                                    <span className="text-xs text-muted-foreground">({phone.verified_name})</span>
                                                )}
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                Which WhatsApp number to send this template from.
                            </p>
                        </div>
                    </div>

                    {/* Prompt Guidance */}
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-1.5">
                            <Label className="text-sm font-medium">
                                Prompt Guidance / When to Send
                            </Label>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <HelpCircle className="w-3.5 h-3.5 text-muted-foreground cursor-pointer" />
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                    Fed directly into the tool description enum so the AI agent knows when to pick choice &quot;{tpl.id}&quot;.
                                </TooltipContent>
                            </Tooltip>
                        </div>
                        <Textarea
                            value={tpl.description || ""}
                            onChange={(e) =>
                                setDraftTemplate({ ...tpl, description: e.target.value })
                            }
                            placeholder="e.g., Send when caller confirms appointment booking, or asks for a follow-up link."
                            rows={2}
                            className="text-sm resize-none"
                        />
                    </div>

                    {/* Approved Meta Template Picker */}
                    <div className="space-y-1.5">
                        <div className="flex items-center justify-between">
                            <Label className="text-sm font-medium">
                                Approved Meta Template <span className="text-destructive">*</span>
                            </Label>
                            <a
                                href="https://business.facebook.com/wa/manage/message-templates"
                                target="_blank"
                                rel="noreferrer"
                                className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                            >
                                Manage in Meta Business Manager
                                <ExternalLink className="w-3 h-3" />
                            </a>
                        </div>
                        <Select
                            value={tpl.template_name ? `${tpl.template_name}:${tpl.language_code || "en_US"}` : ""}
                            onValueChange={handleMetaTemplateSelectInDraft}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select an approved Meta template..." />
                            </SelectTrigger>
                            <SelectContent>
                                {availableTemplates.length === 0 ? (
                                    <div className="p-3 text-xs text-muted-foreground text-center">
                                        No approved templates found. Ensure templates exist in your Meta WABA account.
                                    </div>
                                ) : (
                                    availableTemplates.map((meta) => {
                                        const key = `${meta.name}:${meta.language}`;
                                        return (
                                            <SelectItem key={key} value={key}>
                                                <div className="flex items-center gap-2">
                                                    <span className="font-mono font-medium">{meta.name}</span>
                                                    <Badge variant="outline" className="text-[10px] py-0 px-1 font-mono">
                                                        {meta.language}
                                                    </Badge>
                                                    {meta.category && (
                                                        <span className="text-xs text-muted-foreground">({meta.category})</span>
                                                    )}
                                                </div>
                                            </SelectItem>
                                        );
                                    })
                                )}
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Meta Preview Box */}
                    {selectedMetaTemplate && (
                        <div className="p-3.5 rounded-lg border bg-muted/40 space-y-2">
                            <div className="flex items-center justify-between">
                                <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                                    Meta Template Preview
                                </span>
                                <Badge variant="secondary" className="text-xs font-mono">
                                    {selectedMetaTemplate.category || "UTILITY"} • {selectedMetaTemplate.language}
                                </Badge>
                            </div>
                            {selectedMetaTemplate.body_text ? (
                                <p className="text-sm whitespace-pre-wrap font-sans text-foreground/90 bg-background/80 p-2.5 rounded border">
                                    {selectedMetaTemplate.body_text}
                                </p>
                            ) : (
                                <p className="text-xs text-muted-foreground italic">
                                    (No body text available for preview)
                                </p>
                            )}
                        </div>
                    )}

                    {/* Variable Mappings */}
                    <div className="space-y-3 pt-1">
                        <div className="flex items-center justify-between">
                            <div>
                                <h4 className="text-sm font-semibold">Template Variables</h4>
                                <p className="text-xs text-muted-foreground">
                                    Map where each placeholder value (&#123;&#123;1&#125;&#125;, &#123;&#123;2&#125;&#125;) is sourced from.
                                </p>
                            </div>
                            {(tpl.variable_mappings || []).length > 0 && (
                                <Badge variant="outline" className="text-xs">
                                    {(tpl.variable_mappings || []).length} Placeholder{(tpl.variable_mappings || []).length > 1 ? "s" : ""}
                                </Badge>
                            )}
                        </div>

                        {(!tpl.variable_mappings || tpl.variable_mappings.length === 0) ? (
                            <div className="text-xs text-muted-foreground p-3 border rounded-lg bg-muted/20 text-center">
                                {selectedMetaTemplate
                                    ? "This Meta template has no placeholders. It will be sent as a static message."
                                    : "Select an approved Meta template above to configure variable mappings."}
                            </div>
                        ) : (
                            <div className="rounded-md border overflow-hidden">
                                <Table>
                                    <TableHeader className="bg-muted/40">
                                        <TableRow>
                                            <TableHead className="w-24 text-xs font-semibold">Placeholder</TableHead>
                                            <TableHead className="w-44 text-xs font-semibold">
                                                Parameter Name <span className="text-destructive">*</span>
                                            </TableHead>
                                            <TableHead className="w-36 text-xs font-semibold">Source</TableHead>
                                            <TableHead className="text-xs font-semibold">Value / Path / Prompt</TableHead>
                                            <TableHead className="w-44 text-xs font-semibold">Fallback Value (Optional)</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {tpl.variable_mappings.map((v) => {
                                            const metaVar = selectedMetaTemplate?.variables?.find((mv) => mv.index === v.index);
                                            return (
                                                <TableRow key={v.index}>
                                                    <TableCell className="py-2.5 align-middle">
                                                        <div className="flex items-center gap-1.5">
                                                            <Badge variant="secondary" className="font-mono text-xs font-semibold shrink-0">
                                                                &#123;&#123;{v.index}&#125;&#125;
                                                            </Badge>
                                                            {metaVar?.example && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <span className="text-[11px] text-muted-foreground truncate max-w-[80px] cursor-help">
                                                                            {metaVar.example}
                                                                        </span>
                                                                    </TooltipTrigger>
                                                                    <TooltipContent>
                                                                        Meta Example: {metaVar.example}
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="py-2.5 align-middle">
                                                        <Input
                                                            value={v.name}
                                                            onChange={(e) =>
                                                                handleUpdateDraftVariable(v.index, { name: e.target.value })
                                                            }
                                                            placeholder={`param_${v.index}`}
                                                            className="h-8 text-xs font-mono"
                                                        />
                                                    </TableCell>
                                                    <TableCell className="py-2.5 align-middle">
                                                        <Select
                                                            value={v.source}
                                                            onValueChange={(val) =>
                                                                handleUpdateDraftVariable(v.index, {
                                                                    source: val as WhatsAppVariableMapping["source"],
                                                                })
                                                            }
                                                        >
                                                            <SelectTrigger className="h-8 text-xs">
                                                                <SelectValue />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value="llm">AI Model (LLM)</SelectItem>
                                                                <SelectItem value="context">Context Var</SelectItem>
                                                                <SelectItem value="literal">Static Text</SelectItem>
                                                                <SelectItem value="lead_phone">Lead Phone</SelectItem>
                                                            </SelectContent>
                                                        </Select>
                                                    </TableCell>
                                                    <TableCell className="py-2.5 align-middle">
                                                        <Input
                                                            value={v.value || ""}
                                                            disabled={v.source === "lead_phone"}
                                                            onChange={(e) =>
                                                                handleUpdateDraftVariable(v.index, { value: e.target.value })
                                                            }
                                                            placeholder={
                                                                v.source === "llm"
                                                                    ? "Instruction for AI agent (e.g. Appointment date & time)"
                                                                    : v.source === "context"
                                                                    ? "Context path (e.g. {{initial_context.name}})"
                                                                    : v.source === "literal"
                                                                    ? "Static text value"
                                                                    : "Auto-detected from caller/called number"
                                                            }
                                                            className="h-8 text-xs"
                                                        />
                                                    </TableCell>
                                                    <TableCell className="py-2.5 align-middle">
                                                        {v.source === "literal" ? (
                                                            <span className="text-xs text-muted-foreground px-2">—</span>
                                                        ) : (
                                                            <Input
                                                                value={v.fallback_value || ""}
                                                                onChange={(e) =>
                                                                    handleUpdateDraftVariable(v.index, { fallback_value: e.target.value })
                                                                }
                                                                placeholder={
                                                                    v.source === "context"
                                                                        ? "e.g. Valued Customer"
                                                                        : v.source === "lead_phone"
                                                                        ? "e.g. your phone / backup #"
                                                                        : "Default if omitted by model"
                                                                }
                                                                className="h-8 text-xs"
                                                            />
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>
        );
    };

    // Helper to render the clean view mode card for a template
    const renderViewModeCard = (tpl: WhatsAppTemplateConfig, tplIdx: number) => {
        const incomplete = isTemplateIncomplete(tpl);
        const selectedMetaTemplate = availableTemplates.find(
            (meta) =>
                meta.name === tpl.template_name &&
                (!tpl.language_code || meta.language === tpl.language_code)
        ) || availableTemplates.find((meta) => meta.name === tpl.template_name);

        const senderPhone = availablePhoneNumbers.find(
            (p) => p.phone_number_id === tpl.from_phone_number_id
        );

        return (
            <section
                key={tpl.id || tplIdx}
                className="overflow-hidden rounded-lg border bg-card shadow-xs transition-colors hover:border-muted-foreground/30"
            >
                {/* Header Summary */}
                <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/20 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2.5 min-w-0">
                        <Badge variant="outline" className="font-mono text-xs font-semibold px-2">
                            #{tpl.id}
                        </Badge>

                        <span className="font-semibold text-sm truncate">
                            {tpl.name || `Template ${tpl.id}`}
                        </span>

                        {tpl.template_name ? (
                            <Badge variant="secondary" className="text-[11px] font-mono">
                                {tpl.template_name} ({tpl.language_code || "en_US"})
                            </Badge>
                        ) : (
                            <Badge variant="outline" className="text-[11px] text-amber-600 border-amber-500/30 bg-amber-500/10">
                                No Meta Template
                            </Badge>
                        )}

                        {senderPhone ? (
                            <span className="text-xs text-muted-foreground hidden md:inline-flex items-center gap-1">
                                <Phone className="w-3 h-3" />
                                {senderPhone.display_phone_number}
                            </span>
                        ) : (
                            <span className="text-xs text-muted-foreground hidden md:inline">
                                (default number)
                            </span>
                        )}

                        <Badge variant="outline" className="text-[10px] font-mono text-muted-foreground">
                            LLM Choice: &quot;{tpl.id}&quot;
                        </Badge>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                        {incomplete ? (
                            <span className="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400 font-medium mr-2">
                                <AlertCircle className="w-3.5 h-3.5" />
                                Incomplete
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-1 text-[11px] text-green-600 dark:text-green-400 font-medium mr-2">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                Ready
                            </span>
                        )}

                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="h-8 gap-1.5"
                            onClick={() => handleStartEdit(tpl)}
                            disabled={Boolean(editingId)}
                            aria-label={`Edit template ${tpl.name || tpl.id}`}
                        >
                            <Pencil className="h-3.5 w-3.5" />
                            Edit
                        </Button>

                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                            onClick={() => handleRemoveTemplate(tpl.id)}
                            aria-label={`Delete template ${tpl.name || tpl.id}`}
                            title="Delete template"
                            disabled={editingId !== null}
                        >
                            <Trash2 className="h-4 w-4" />
                        </Button>
                    </div>
                </div>

                {/* Body Summary */}
                <div className="p-4 space-y-3 text-sm">
                    {tpl.description ? (
                        <p className="text-xs text-muted-foreground">
                            <span className="font-medium text-foreground">When to send: </span>
                            {tpl.description}
                        </p>
                    ) : (
                        <p className="text-xs text-muted-foreground italic">
                            No specific trigger guidance provided for AI agent.
                        </p>
                    )}

                    {selectedMetaTemplate?.body_text && (
                        <div className="p-2.5 rounded border bg-muted/30 font-sans text-xs text-muted-foreground whitespace-pre-wrap">
                            {selectedMetaTemplate.body_text}
                        </div>
                    )}

                    <div className="flex flex-wrap items-center gap-2 pt-1 text-xs">
                        <span className="font-medium text-muted-foreground">Variables:</span>
                        {(!tpl.variable_mappings || tpl.variable_mappings.length === 0) ? (
                            <span className="text-muted-foreground italic">None required</span>
                        ) : (
                            tpl.variable_mappings.map((v) => (
                                <Badge
                                    key={v.index}
                                    variant="outline"
                                    className="font-mono text-[11px] py-0.5 px-2 bg-background"
                                >
                                    &#123;&#123;{v.index}&#125;&#125; {v.name || `param_${v.index}`}{" "}
                                    <span className="text-muted-foreground">
                                        ({v.source === "llm" ? "AI Model" : v.source === "context" ? "Context" : v.source === "literal" || v.source === "static" ? "Static" : "Phone"}
                                        {v.fallback_value ? ` • fallback: "${v.fallback_value}"` : ""})
                                    </span>
                                </Badge>
                            ))
                        )}
                    </div>
                </div>
            </section>
        );
    };

    return (
        <div className="space-y-6">
            {/* Tool Information */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Tool Information</CardTitle>
                    <CardDescription>
                        Configure how the AI agent identifies and uses this WhatsApp tool during conversations.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="tool-name">Tool Name</Label>
                        <Input
                            id="tool-name"
                            value={name}
                            onChange={(e) => onNameChange(e.target.value)}
                            placeholder="e.g., Send WhatsApp Message"
                        />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="tool-description">Description for AI Agent</Label>
                        <Textarea
                            id="tool-description"
                            value={description}
                            onChange={(e) => onDescriptionChange(e.target.value)}
                            placeholder="e.g., Send follow-up WhatsApp messages, booking confirmations, or information links to the customer."
                            rows={2}
                        />
                    </div>
                </CardContent>
            </Card>

            {/* Execution Settings */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Delivery Settings</CardTitle>
                    <CardDescription>
                        Determine recipient routing and live call execution behavior.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <Label>Execution Mode</Label>
                        <RadioGroup
                            value={executionMode}
                            onValueChange={(val) => onExecutionModeChange(val as "async" | "sync")}
                            className="grid grid-cols-1 md:grid-cols-2 gap-4"
                        >
                            <div
                                role="button"
                                tabIndex={0}
                                onClick={() => onExecutionModeChange("async")}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        onExecutionModeChange("async");
                                    }
                                }}
                                className={cn(
                                    "flex items-start space-x-3 p-3 rounded-lg border bg-card hover:bg-muted/50 cursor-pointer transition-all",
                                    executionMode === "async" && "border-primary ring-1 ring-primary bg-primary/5"
                                )}
                            >
                                <RadioGroupItem value="async" id="mode-async" className="mt-1" />
                                <div className="space-y-1">
                                    <Label htmlFor="mode-async" className="font-medium cursor-pointer">
                                        Async (Zero Dead Air)
                                    </Label>
                                    <p className="text-xs text-muted-foreground">
                                        Dispatches the message in the background and lets the AI agent continue speaking immediately. Recommended for voice calls.
                                    </p>
                                </div>
                            </div>

                            <div
                                role="button"
                                tabIndex={0}
                                onClick={() => onExecutionModeChange("sync")}
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        onExecutionModeChange("sync");
                                    }
                                }}
                                className={cn(
                                    "flex items-start space-x-3 p-3 rounded-lg border bg-card hover:bg-muted/50 cursor-pointer transition-all",
                                    executionMode === "sync" && "border-primary ring-1 ring-primary bg-primary/5"
                                )}
                            >
                                <RadioGroupItem value="sync" id="mode-sync" className="mt-1" />
                                <div className="space-y-1">
                                    <Label htmlFor="mode-sync" className="font-medium cursor-pointer">
                                        Sync (Wait for Meta Confirmation)
                                    </Label>
                                    <p className="text-xs text-muted-foreground">
                                        Waits for Meta Graph API response before the agent speaks next. Useful if the agent must announce immediate confirmation.
                                    </p>
                                </div>
                            </div>
                        </RadioGroup>
                    </div>

                    <div className="p-3 rounded-lg border bg-muted/30 text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">Automatic Recipient Routing: </span>
                        Dograh automatically routes WhatsApp messages to the lead’s phone number (inbound caller ID on inbound calls, destination number on outbound campaigns). The agent can also pass a custom recipient number if the lead requests delivery to a different phone.
                    </div>

                    {onFallbackRecipientPhoneChange && (
                        <div className="space-y-1.5 pt-2">
                            <div className="flex items-center gap-1.5">
                                <Label htmlFor="fallback-recipient-phone" className="text-sm font-medium">
                                    Fallback / Test Recipient Phone Number
                                </Label>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <HelpCircle className="w-3.5 h-3.5 text-muted-foreground cursor-pointer" />
                                    </TooltipTrigger>
                                    <TooltipContent className="max-w-xs">
                                        Used automatically during web browser test calls (WebRTC) or whenever caller ID cannot be extracted from the telecom call.
                                    </TooltipContent>
                                </Tooltip>
                            </div>
                            <div className="relative max-w-sm">
                                <Phone className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                                <Input
                                    id="fallback-recipient-phone"
                                    value={fallbackRecipientPhone}
                                    onChange={(e) => onFallbackRecipientPhoneChange(e.target.value)}
                                    placeholder="+15551234567"
                                    className="pl-8 text-sm font-mono"
                                />
                            </div>
                            <p className="text-xs text-muted-foreground">
                                E.164 phone number used as fallback when live caller/called number is unavailable.
                            </p>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Configured Templates List */}
            <div className="space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div>
                        <h3 className="text-base font-semibold">Configured Templates</h3>
                        <p className="text-sm text-muted-foreground">
                            Configure the templates this tool can send. The AI agent selects templates simply using choice 1, 2, 3...
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={fetchData}
                                    disabled={isLoadingData}
                                >
                                    <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingData ? "animate-spin" : ""}`} />
                                    Sync from Meta
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                                Pull latest approved templates and phone numbers from your WhatsApp Business Account
                            </TooltipContent>
                        </Tooltip>

                        <Button
                            type="button"
                            size="sm"
                            onClick={handleStartAdd}
                            disabled={Boolean(editingId)}
                        >
                            <Plus className="w-3.5 h-3.5 mr-1.5" />
                            Add Template
                        </Button>
                    </div>
                </div>

                {loadError && (
                    <div className="flex items-center gap-2 p-3 text-sm rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                        <AlertCircle className="w-4 h-4 shrink-0" />
                        <span>{loadError}</span>
                    </div>
                )}

                {templates.length === 0 && !isNewTemplate ? (
                    <Card className="border-dashed">
                        <CardContent className="py-8 text-center space-y-3">
                            <p className="text-sm text-muted-foreground">
                                No templates configured yet. Add at least one template for the AI agent to trigger.
                            </p>
                            <Button type="button" variant="outline" size="sm" onClick={handleStartAdd}>
                                <Plus className="w-3.5 h-3.5 mr-1.5" />
                                Add First Template
                            </Button>
                        </CardContent>
                    </Card>
                ) : (
                    <div className="space-y-4">
                        {templates.map((tpl, tplIdx) => {
                            if (editingId === tpl.id && draftTemplate) {
                                return (
                                    <div key={tpl.id || tplIdx}>
                                        {renderInlineEditForm(draftTemplate)}
                                    </div>
                                );
                            }
                            return renderViewModeCard(tpl, tplIdx);
                        })}

                        {/* If adding a new template, show the inline edit form at the bottom */}
                        {isNewTemplate && draftTemplate && (
                            <div key="new-template" ref={draftCardRef}>
                                {renderInlineEditForm(draftTemplate)}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
