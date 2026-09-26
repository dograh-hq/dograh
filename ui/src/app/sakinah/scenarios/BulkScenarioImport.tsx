"use client";

import {
    AlertCircle,
    CheckCircle2,
    FileArchive,
    FileJson,
    Loader2,
    UploadCloud,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
    type BulkScenarioDuplicatePolicy,
    type BulkScenarioImportResponse,
    commitBulkSakinahScenarios,
    previewBulkSakinahScenarios,
} from "@/lib/sakinahPersistence";
import { cn } from "@/lib/utils";

interface BulkScenarioImportProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onCommitted: (response: BulkScenarioImportResponse) => void;
}

const ACCEPTED_SUFFIXES = [".json", ".zip"];

function isAcceptedFile(file: File): boolean {
    return ACCEPTED_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix));
}

function statusLabel(status: string): string {
    return status.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
    if (["invalid", "failed"].includes(status)) return "destructive";
    if (["duplicate", "skipped", "not_selected"].includes(status)) return "secondary";
    return "default";
}

export function BulkScenarioImport({ open, onOpenChange, onCommitted }: BulkScenarioImportProps) {
    const [files, setFiles] = useState<File[]>([]);
    const [preview, setPreview] = useState<BulkScenarioImportResponse | null>(null);
    const [result, setResult] = useState<BulkScenarioImportResponse | null>(null);
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [duplicatePolicy, setDuplicatePolicy] = useState<BulkScenarioDuplicatePolicy>("skip_existing");
    const [busy, setBusy] = useState(false);
    const [dragging, setDragging] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement | null>(null);

    useEffect(() => {
        if (!open) {
            setFiles([]);
            setPreview(null);
            setResult(null);
            setSelected(new Set());
            setDuplicatePolicy("skip_existing");
            setMessage(null);
            setBusy(false);
        }
    }, [open]);

    const requestPreview = async (nextFiles: File[]) => {
        setBusy(true);
        setMessage(null);
        setPreview(null);
        setResult(null);
        setSelected(new Set());
        try {
            const response = await previewBulkSakinahScenarios(nextFiles);
            setPreview(response);
            setSelected(new Set(response.items.filter((item) => item.validation_status === "valid").map((item) => item.item_index)));
        } catch (error) {
            setMessage(error instanceof Error ? error.message : "Unable to preview the scenario import.");
        } finally {
            setBusy(false);
        }
    };

    const chooseFiles = (candidateFiles: File[]) => {
        const accepted = candidateFiles.filter(isAcceptedFile);
        if (accepted.length !== candidateFiles.length) {
            setMessage("Only .zip and .json files can be imported.");
        }
        if (accepted.length === 0) return;
        const unique = accepted.filter((file, index, all) => all.findIndex((other) =>
            other.name === file.name && other.size === file.size && other.lastModified === file.lastModified
        ) === index);
        setFiles(unique);
        void requestPreview(unique);
    };

    const toggleSelected = (itemIndex: number) => {
        setSelected((current) => {
            const next = new Set(current);
            if (next.has(itemIndex)) next.delete(itemIndex);
            else next.add(itemIndex);
            return next;
        });
    };

    const commit = async () => {
        if (!preview?.preview_token || selected.size === 0) return;
        setBusy(true);
        setMessage(null);
        try {
            const response = await commitBulkSakinahScenarios(
                preview.preview_token,
                duplicatePolicy,
                [...selected].sort((a, b) => a - b),
            );
            setResult(response);
            onCommitted(response);
        } catch (error) {
            setMessage(error instanceof Error ? error.message : "Unable to import the scenarios.");
        } finally {
            setBusy(false);
        }
    };

    const displayedResponse = result ?? preview;
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-6xl">
                <DialogHeader>
                    <DialogTitle>Bulk Import Scenarios</DialogTitle>
                    <DialogDescription>
                        Upload a ZIP archive or select multiple JSON files. Files are validated first and are not saved until you confirm the import.
                    </DialogDescription>
                </DialogHeader>

                <div
                    role="button"
                    tabIndex={0}
                    onClick={() => inputRef.current?.click()}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
                    }}
                    onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
                    onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
                    onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
                    onDrop={(event) => {
                        event.preventDefault();
                        setDragging(false);
                        chooseFiles([...event.dataTransfer.files]);
                    }}
                    className={cn(
                        "flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center transition-colors",
                        dragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50",
                    )}
                >
                    <UploadCloud className="size-8 text-muted-foreground" />
                    <p className="font-medium">Drop a ZIP or JSON files here</p>
                    <p className="text-sm text-muted-foreground">or click to browse · multiple JSON files supported</p>
                    <input
                        ref={inputRef}
                        type="file"
                        accept=".zip,.json,application/zip,application/json"
                        multiple
                        className="hidden"
                        onChange={(event) => {
                            chooseFiles([...(event.target.files ?? [])]);
                            event.target.value = "";
                        }}
                    />
                </div>

                {files.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                        {files.map((file) => {
                            const Icon = file.name.toLowerCase().endsWith(".zip") ? FileArchive : FileJson;
                            return <Badge key={`${file.name}-${file.lastModified}`} variant="outline" className="gap-1.5"><Icon className="size-3.5" />{file.name}</Badge>;
                        })}
                    </div>
                ) : null}

                {busy && !displayedResponse ? <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Preparing preview…</div> : null}
                {message ? <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><AlertCircle className="mr-2 inline size-4" />{message}</p> : null}

                {displayedResponse ? (
                    <div className="space-y-5">
                        <div className="grid gap-3 sm:grid-cols-5">
                            {[
                                ["JSON files found", displayedResponse.files_detected],
                                ["Valid", displayedResponse.valid],
                                ["Invalid", displayedResponse.invalid],
                                ["Duplicates", displayedResponse.duplicates],
                                ["Already existing", displayedResponse.already_existing],
                            ].map(([label, value]) => <div key={label} className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></div>)}
                        </div>

                        {result ? <p role="status" className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-emerald-700"><CheckCircle2 className="mr-2 inline size-4" />Imported {result.imported}; failed {result.failed}.</p> : null}

                        {!result ? <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><Label htmlFor="duplicate-policy">Duplicate handling</Label><p className="text-xs text-muted-foreground">Default: skip existing scenarios.</p></div><Select value={duplicatePolicy} onValueChange={(value) => setDuplicatePolicy(value as BulkScenarioDuplicatePolicy)}><SelectTrigger id="duplicate-policy" className="w-full sm:w-64"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="skip_existing">Skip existing</SelectItem><SelectItem value="replace_existing">Replace existing</SelectItem><SelectItem value="import_as_new">Import as new</SelectItem></SelectContent></Select></div> : null}

                        <div className="overflow-x-auto rounded-lg border"><table className="w-full min-w-[760px] text-sm"><thead className="bg-muted/40 text-left"><tr><th className="w-12 p-3">Import</th><th className="p-3">Scenario title</th><th className="p-3">Filename</th><th className="p-3">Validation</th><th className="p-3">Status / error</th></tr></thead><tbody>{displayedResponse.items.map((item) => { const selectable = item.validation_status === "valid" && !result; return <tr key={item.item_index} className="border-t align-top"><td className="p-3">{selectable ? <input type="checkbox" checked={selected.has(item.item_index)} onChange={() => toggleSelected(item.item_index)} aria-label={`Import ${item.scenario_title ?? item.source_filename}`} /> : null}</td><td className="max-w-sm whitespace-pre-wrap p-3 font-medium">{item.scenario_title ?? "—"}</td><td className="max-w-xs break-all p-3 text-muted-foreground">{item.source_filename}</td><td className="p-3"><Badge variant={statusVariant(item.validation_status)}>{statusLabel(item.validation_status)}</Badge></td><td className="max-w-sm p-3">{item.status !== item.validation_status ? <Badge variant={statusVariant(item.status)}>{statusLabel(item.status)}</Badge> : null}{item.validation_error ? <p className="mt-1 whitespace-pre-wrap text-xs text-destructive">{item.validation_error}</p> : null}</td></tr>; })}</tbody></table></div>
                    </div>
                ) : null}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Close</Button>
                    {!result && preview ? <Button onClick={() => void commit()} disabled={busy || !preview.preview_token || selected.size === 0}>{busy ? <Loader2 className="animate-spin" /> : null}{busy ? "Importing…" : `Import ${selected.size} scenario${selected.size === 1 ? "" : "s"}`}</Button> : null}
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
