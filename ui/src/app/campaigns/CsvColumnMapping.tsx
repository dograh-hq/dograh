"use client";

import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";

interface CsvPreview {
    headers: string[];
    sample_rows: string[][];
    detected_phone_column: string | null;
    required_variables: string[];
    suggested_mapping: Record<string, string>;
}

const KEEP = "__keep__"; // use the CSV header name as-is (the variable name)
const PHONE = "phone_number";

interface Props {
    sourceId: string;
    workflowId: string;
    onChange: (mapping: Record<string, string>) => void;
    defaultCountryCode?: string;
}

/**
 * After a CSV/Excel is uploaded, previews it and lets the user map columns to the
 * workflow variables (phone number is auto-detected + pre-selected). Emits the
 * mapping via onChange; unmapped columns keep their header as the variable name.
 */
export default function CsvColumnMapping({ sourceId, workflowId, onChange, defaultCountryCode }: Props) {
    const [preview, setPreview] = useState<CsvPreview | null>(null);
    const [mapping, setMapping] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!sourceId) {
            setPreview(null);
            return;
        }
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError(null);
            const res = await client.post({
                url: "/api/v1/campaign/preview-csv",
                body: {
                    source_id: sourceId,
                    workflow_id: workflowId ? parseInt(workflowId) : null,
                    default_country_code: defaultCountryCode && defaultCountryCode !== "none" ? defaultCountryCode : null,
                },
            });
            if (cancelled) return;
            setLoading(false);
            if (res.error) {
                setError(detailFromError(res.error, "Failed to preview CSV"));
                return;
            }
            const p = res.data as CsvPreview;
            setPreview(p);
            const init = { ...(p.suggested_mapping || {}) };
            setMapping(init);
            onChange(init);
        })();
        return () => {
            cancelled = true;
        };
    }, [sourceId, workflowId, defaultCountryCode]);

    const setCol = useCallback(
        (header: string, target: string) => {
            setMapping((prev) => {
                const next = { ...prev };
                if (target === KEEP) delete next[header];
                else next[header] = target;
                onChange(next);
                return next;
            });
        },
        [onChange],
    );

    if (!sourceId) return null;
    if (loading)
        return <p className="text-sm text-muted-foreground">Reading your CSV…</p>;
    if (error) return <p className="text-sm text-red-600">{error}</p>;
    if (!preview) return null;

    const targets = Array.from(
        new Set([PHONE, ...(preview.required_variables || [])]),
    );

    return (
        <div className="space-y-3 rounded-md border p-4">
            <div>
                <Label className="text-sm font-medium">Map your columns</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                    We matched your CSV columns to the fields the agent uses.
                    {preview.detected_phone_column
                        ? ` The phone number was auto-detected from “${preview.detected_phone_column}”.`
                        : " Choose which column holds the phone number."}
                </p>
            </div>
            <div className="space-y-2">
                {preview.headers.map((h) => (
                    <div key={h} className="flex items-center gap-3">
                        <span
                            className="w-40 shrink-0 truncate text-sm font-medium"
                            title={h}
                        >
                            {h}
                        </span>
                        <span className="text-muted-foreground">→</span>
                        <Select
                            value={mapping[h] ?? KEEP}
                            onValueChange={(v) => setCol(h, v)}
                        >
                            <SelectTrigger className="h-8 w-60 text-sm">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value={KEEP}>— keep as “{h}” —</SelectItem>
                                {targets.map((t) => (
                                    <SelectItem key={t} value={t}>
                                        {t}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                ))}
            </div>
        </div>
    );
}
