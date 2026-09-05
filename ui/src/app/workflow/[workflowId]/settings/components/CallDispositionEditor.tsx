"use client";

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { createUuid } from "@/lib/uuid";
import type { CallDispositionOption } from "@/types/workflow-configurations";

export const MAX_CALL_DISPOSITIONS = 50;
export const MAX_CALL_DISPOSITION_CODE_LENGTH = 64;
export const MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH = 1000;
export const MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH = 4000;

const CALL_DISPOSITION_CODE_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;

export interface CallDispositionRow extends CallDispositionOption {
    id: string;
}

interface RowErrors {
    code?: string;
    description?: string;
}

export interface CallDispositionValidation {
    isValid: boolean;
    rowErrors: Record<string, RowErrors>;
    totalDescriptionLength: number;
    totalError?: string;
}

export function createCallDispositionRows(
    dispositions: CallDispositionOption[],
): CallDispositionRow[] {
    return dispositions.map((disposition) => ({
        id: createUuid(),
        ...disposition,
    }));
}

export function normalizeCallDispositions(
    rows: CallDispositionRow[],
): CallDispositionOption[] {
    return rows.map(({ code, description }) => ({
        code: code.trim(),
        description: description.trim(),
    }));
}

export function validateCallDispositionRows(
    rows: CallDispositionRow[],
): CallDispositionValidation {
    const rowErrors: Record<string, RowErrors> = {};
    const codeCounts = new Map<string, number>();

    for (const row of rows) {
        const code = row.code.trim();
        if (code) {
            const normalizedCode = code.toLowerCase();
            codeCounts.set(normalizedCode, (codeCounts.get(normalizedCode) ?? 0) + 1);
        }
    }

    for (const row of rows) {
        const code = row.code.trim();
        const description = row.description.trim();
        const errors: RowErrors = {};

        if (!code) {
            errors.code = "Disposition code is required.";
        } else if (code.length > MAX_CALL_DISPOSITION_CODE_LENGTH) {
            errors.code = `Disposition code must be at most ${MAX_CALL_DISPOSITION_CODE_LENGTH} characters.`;
        } else if (!CALL_DISPOSITION_CODE_PATTERN.test(code)) {
            errors.code = "Start with a letter and use only letters, numbers, underscores, or hyphens.";
        } else if ((codeCounts.get(code.toLowerCase()) ?? 0) > 1) {
            errors.code = "Disposition codes must be unique.";
        }

        if (!description) {
            errors.description = "Description is required.";
        } else if (description.length > MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH) {
            errors.description = `Description must be at most ${MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH.toLocaleString()} characters.`;
        }

        if (errors.code || errors.description) {
            rowErrors[row.id] = errors;
        }
    }

    const totalDescriptionLength = rows.reduce(
        (total, row) => total + row.description.trim().length,
        0,
    );
    const totalError = totalDescriptionLength > MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH
        ? `Descriptions must total at most ${MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH.toLocaleString()} characters.`
        : undefined;

    return {
        isValid:
            rows.length <= MAX_CALL_DISPOSITIONS
            && Object.keys(rowErrors).length === 0
            && !totalError,
        rowErrors,
        totalDescriptionLength,
        totalError,
    };
}

export function CallDispositionEditor({
    rows,
    onChange,
    defaultDispositions = [],
}: {
    rows: CallDispositionRow[];
    onChange: (rows: CallDispositionRow[]) => void;
    defaultDispositions?: CallDispositionOption[];
}) {
    const enabled = rows.length > 0;
    const rememberedRows = useRef<CallDispositionRow[]>(rows);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [draftRows, setDraftRows] = useState<CallDispositionRow[]>([]);

    useEffect(() => {
        if (rows.length > 0) {
            rememberedRows.current = rows;
        }
    }, [rows]);

    const openEditor = (editorRows: CallDispositionRow[]) => {
        setDraftRows(editorRows.map((row) => ({ ...row })));
        setDialogOpen(true);
    };

    const handleEnabledChange = (checked: boolean) => {
        if (!checked) {
            rememberedRows.current = rows;
            onChange([]);
            return;
        }

        const options = rememberedRows.current.length > 0
            ? rememberedRows.current
            : createCallDispositionRows(defaultDispositions);
        openEditor(options);
    };

    const handleApply = () => {
        const validation = validateCallDispositionRows(draftRows);
        if (draftRows.length === 0 || !validation.isValid) return;

        rememberedRows.current = draftRows;
        onChange(draftRows);
        setDialogOpen(false);
    };

    const draftValidation = validateCallDispositionRows(draftRows);

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between gap-6">
                <div className="space-y-1">
                    <Label htmlFor="call-disposition-extraction-enabled" className="text-sm font-medium">
                        Extract call disposition at the end of the call
                    </Label>
                    <p className="text-xs text-muted-foreground">
                        {enabled
                            ? `${rows.length} outcome${rows.length === 1 ? "" : "s"} configured. Dograh will classify the completed conversation into one of them.`
                            : "Disabled. Dograh will keep the disposition recorded by the call-ending event."}
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                    {enabled && (
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => openEditor(rows)}
                        >
                            Configure options
                        </Button>
                    )}
                    <Switch
                        id="call-disposition-extraction-enabled"
                        checked={enabled}
                        onCheckedChange={handleEnabledChange}
                    />
                </div>
            </div>

            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogContent className="max-h-[90vh] grid-rows-[auto_minmax(0,1fr)_auto] sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>Call disposition extraction</DialogTitle>
                        <DialogDescription>
                            Configure the code and description pairs the model can choose from. Codes are recorded before organization-level disposition mapping is applied.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="min-h-0 overflow-y-auto pr-1">
                        <CallDispositionRowsEditor
                            rows={draftRows}
                            onChange={setDraftRows}
                            validation={draftValidation}
                        />
                    </div>

                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setDialogOpen(false)}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            disabled={draftRows.length === 0 || !draftValidation.isValid}
                            onClick={handleApply}
                        >
                            {enabled ? "Save options" : "Enable extraction"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function CallDispositionRowsEditor({
    rows,
    onChange,
    validation,
}: {
    rows: CallDispositionRow[];
    onChange: (rows: CallDispositionRow[]) => void;
    validation: CallDispositionValidation;
}) {

    const updateRow = (
        rowId: string,
        update: Partial<Pick<CallDispositionRow, "code" | "description">>,
    ) => {
        onChange(rows.map((row) => (row.id === rowId ? { ...row, ...update } : row)));
    };

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
                <Label className="text-sm">Disposition options</Label>
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={rows.length >= MAX_CALL_DISPOSITIONS}
                    onClick={() => onChange([
                        ...rows,
                        { id: createUuid(), code: "", description: "" },
                    ])}
                >
                    <Plus className="mr-1 h-4 w-4" /> Add custom disposition
                </Button>
            </div>

            <div className="space-y-2">
                {rows.map((row, index) => {
                    const errors = validation.rowErrors[row.id];
                    const codeId = `call-disposition-code-${row.id}`;
                    const descriptionId = `call-disposition-description-${row.id}`;
                    return (
                        <div
                            key={row.id}
                            className="rounded-md border bg-background p-3"
                        >
                            <div className="flex items-start gap-3">
                                <div className="min-w-0 flex-1 space-y-3">
                                    <p className="text-xs font-medium text-muted-foreground">
                                        Disposition {index + 1}
                                    </p>
                                    <div className="space-y-1.5">
                                        <Label htmlFor={codeId} className="text-xs">
                                            Disposition Code
                                        </Label>
                                        <Input
                                            id={codeId}
                                            value={row.code}
                                            maxLength={MAX_CALL_DISPOSITION_CODE_LENGTH}
                                            aria-invalid={Boolean(errors?.code)}
                                            aria-describedby={errors?.code ? `${codeId}-error` : undefined}
                                            onChange={(event) => updateRow(row.id, { code: event.target.value })}
                                            placeholder="qualified"
                                        />
                                        {errors?.code && (
                                            <p id={`${codeId}-error`} className="text-xs text-destructive">
                                                {errors.code}
                                            </p>
                                        )}
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label htmlFor={descriptionId} className="text-xs">
                                            Description
                                        </Label>
                                        <Textarea
                                            id={descriptionId}
                                            value={row.description}
                                            rows={3}
                                            maxLength={MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH}
                                            aria-invalid={Boolean(errors?.description)}
                                            aria-describedby={errors?.description ? `${descriptionId}-error` : undefined}
                                            onChange={(event) => updateRow(row.id, { description: event.target.value })}
                                            placeholder="Use when the customer meets the qualification criteria and wants to proceed."
                                        />
                                        {errors?.description && (
                                            <p id={`${descriptionId}-error`} className="text-xs text-destructive">
                                                {errors.description}
                                            </p>
                                        )}
                                    </div>
                                </div>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    aria-label={`Remove disposition ${index + 1}`}
                                    onClick={() => onChange(rows.filter((item) => item.id !== row.id))}
                                >
                                    <Trash2 className="h-4 w-4" />
                                </Button>
                            </div>
                        </div>
                    );
                })}

                {rows.length === 0 && (
                    <div className="rounded-md border border-dashed p-4 text-center">
                        <p className="text-sm font-medium">Add at least one disposition</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            Extraction needs a closed list of outcomes to choose from.
                        </p>
                    </div>
                )}

                <div className="flex justify-end text-xs text-muted-foreground">
                    <p className={validation.totalError ? "text-destructive" : undefined}>
                        {validation.totalDescriptionLength.toLocaleString()} / {MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH.toLocaleString()} description characters
                    </p>
                </div>
                {validation.totalError && (
                    <p className="text-xs text-destructive">{validation.totalError}</p>
                )}
            </div>
        </div>
    );
}
