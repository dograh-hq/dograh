"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
}: {
    rows: CallDispositionRow[];
    onChange: (rows: CallDispositionRow[]) => void;
}) {
    const validation = validateCallDispositionRows(rows);

    const updateRow = (
        rowId: string,
        update: Partial<Pick<CallDispositionRow, "code" | "description">>,
    ) => {
        onChange(rows.map((row) => (row.id === rowId ? { ...row, ...update } : row)));
    };

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-sm font-medium">Call Disposition</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                    Define the exact business outcomes the AI may select after a call ends. Each description should clearly explain when its code applies.
                </p>
            </div>

            <div className="flex items-center justify-between gap-4">
                <Label className="text-sm">Dispositions</Label>
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
                    <Plus className="mr-1 h-4 w-4" /> Add disposition
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
                    <p className="text-xs text-muted-foreground">
                        No call dispositions configured. Dograh will keep the existing call-ending disposition without running dedicated outcome extraction.
                    </p>
                )}

                <div className="flex items-start justify-between gap-3 text-xs text-muted-foreground">
                    <p>
                        Codes are recorded before organization-level disposition mapping is applied.
                    </p>
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
