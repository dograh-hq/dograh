"use client";

import { Copy, Library, Pencil, Play, Plus, Search, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { getAuthUserApiV1UserAuthUserGet } from "@/client";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import {
    type BulkScenarioImportResponse,
    deleteSakinahScenario, listSakinahScenarios, loadSakinahScenariosWithLegacyMigration,
    saveSakinahScenario,
} from "@/lib/sakinahPersistence";
import {
    duplicateScenario, EMPTY_SCENARIO_DRAFT, formatScenarioNumber,
    parseScenarioImport, type Scenario, type ScenarioDraft, type ScenarioMode,
    scenarioToDraft,
} from "@/lib/sakinahScenarios";

import { BulkScenarioImport } from "./BulkScenarioImport";

const LANGUAGES = ["English", "Arabic", "French", "Spanish", "Urdu", "Hindi", "Other"];
const GENDERS = ["Female", "Male", "Non-binary", "Not specified", "Custom"];
const EMOTIONS = ["Neutral", "Anxious", "Low mood", "Distressed", "Fearful", "Angry", "Irritable", "Guarded", "Suspicious", "Tearful", "Overwhelmed", "Agitated", "Withdrawn", "Hopeful", "Mixed", "Other"];

type TextFieldName = keyof Pick<ScenarioDraft, "title" | "category" | "persona" | "age" | "communicationStyle" | "initialInformation" | "hiddenInformation" | "disclosure" | "behaviour" | "background" | "additionalFactors" | "notes" | "freestylePrompt">;

function Field({ name, label, value, onChange, textarea = false, required = false, helper }: {
    name: TextFieldName;
    label: string;
    value: string;
    onChange: (name: TextFieldName, value: string) => void;
    textarea?: boolean;
    required?: boolean;
    helper?: string;
}) {
    const id = `scenario-${name}`;
    return <div className="space-y-2">
        <Label htmlFor={id}>{label}{required ? " *" : ""}</Label>
        {helper ? <p id={`${id}-help`} className="text-xs text-muted-foreground">{helper}</p> : null}
        {textarea ? <Textarea id={id} value={value} onChange={(event) => onChange(name, event.target.value)} rows={name === "freestylePrompt" ? 12 : 4} aria-describedby={helper ? `${id}-help` : undefined} />
            : <Input id={id} value={value} onChange={(event) => onChange(name, event.target.value)} />}
    </div>;
}

function ChoiceField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
    const custom = value !== "" && !options.includes(value);
    const id = `scenario-${label.toLowerCase().replaceAll(/[^a-z]+/g, "-")}`;
    return <div className="space-y-2">
        <Label htmlFor={id}>{label}</Label>
        <Select value={custom ? "__custom" : value} onValueChange={(next) => onChange(next === "__custom" ? "" : next)}>
            <SelectTrigger id={id} className="w-full"><SelectValue placeholder={`Select ${label.toLowerCase()}`} /></SelectTrigger>
            <SelectContent>{options.map((option) => <SelectItem key={option} value={option === "Other" || option === "Custom" ? "__custom" : option}>{option}</SelectItem>)}</SelectContent>
        </Select>
        {(!value || custom) ? <Input aria-label={`Custom ${label.toLowerCase()}`} value={value} onChange={(event) => onChange(event.target.value)} placeholder={`Enter ${label.toLowerCase()}`} /> : null}
    </div>;
}

function ScenarioEditor({ open, scenario, onOpenChange, onSave }: { open: boolean; scenario: Scenario | null; onOpenChange: (open: boolean) => void; onSave: (draft: ScenarioDraft) => void }) {
    const [draft, setDraft] = useState<ScenarioDraft>(EMPTY_SCENARIO_DRAFT);
    useEffect(() => {
        if (!open) return;
        if (scenario) setDraft(scenarioToDraft(scenario));
        else setDraft(EMPTY_SCENARIO_DRAFT);
    }, [open, scenario]);
    const update = <K extends keyof ScenarioDraft>(name: K, value: ScenarioDraft[K]) => setDraft((current) => ({ ...current, [name]: value }));
    const meaningful = draft.mode === "freestyle" ? draft.freestylePrompt.trim() : draft.title.trim() && draft.persona.trim() && draft.behaviour.trim();
    return <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
            <DialogHeader><DialogTitle>{scenario ? "Edit Scenario" : "Create Scenario"}</DialogTitle><DialogDescription>Create a reusable simulated service-user persona for Sakinah testing.</DialogDescription></DialogHeader>
            <Tabs value={draft.mode} onValueChange={(value) => update("mode", value as ScenarioMode)}>
                <TabsList><TabsTrigger value="structured">Structured</TabsTrigger><TabsTrigger value="freestyle">Freestyle</TabsTrigger></TabsList>
                <TabsContent value="structured" className="mt-4 space-y-5">
                    <div className="grid gap-4 sm:grid-cols-2"><Field name="title" label="Scenario title" value={draft.title} onChange={update} required /><Field name="category" label="Category" value={draft.category} onChange={update} /></div>
                    <div className="space-y-2"><Label htmlFor="scenario-tags">Tags</Label><Input id="scenario-tags" value={draft.tags.join(", ")} onChange={(event) => update("tags", event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean))} placeholder="e.g. anxiety, housing, first contact" /></div>
                    <Field name="age" label="Service-user age or age range" value={draft.age} onChange={update} />
                    <Field name="persona" label="Persona" value={draft.persona} onChange={update} textarea required />
                    <div className="grid gap-4 sm:grid-cols-2"><ChoiceField label="Gender" value={draft.gender} options={GENDERS} onChange={(value) => update("gender", value)} /><ChoiceField label="Language" value={draft.language} options={LANGUAGES} onChange={(value) => update("language", value)} /></div>
                    <div className="grid gap-4 sm:grid-cols-2"><ChoiceField label="Emotional state / emotion" value={draft.emotion} options={EMOTIONS} onChange={(value) => update("emotion", value)} /><Field name="communicationStyle" label="Communication style" value={draft.communicationStyle} onChange={update} /></div>
                    <Field name="initialInformation" label="Initial information" value={draft.initialInformation} onChange={update} textarea />
                    <Field name="hiddenInformation" label="Hidden information" value={draft.hiddenInformation} onChange={update} textarea />
                    <Field name="disclosure" label="Disclosure" value={draft.disclosure} onChange={update} textarea />
                    <Field name="behaviour" label="Behaviour" value={draft.behaviour} onChange={update} textarea required helper="Include conditional rules describing how the service user responds to Sakinah." />
                    <Field name="background" label="Background / context" value={draft.background} onChange={update} textarea />
                    <Field name="additionalFactors" label="Additional factors" value={draft.additionalFactors} onChange={update} textarea />
                    <Field name="notes" label="Optional free notes" value={draft.notes} onChange={update} textarea />
                </TabsContent>
                <TabsContent value="freestyle" className="mt-4 space-y-5">
                    <div className="grid gap-4 sm:grid-cols-2"><Field name="title" label="Title (optional)" value={draft.title} onChange={update} /><Field name="category" label="Category" value={draft.category} onChange={update} /></div>
                    <div className="space-y-2"><Label htmlFor="scenario-tags-freestyle">Tags</Label><Input id="scenario-tags-freestyle" value={draft.tags.join(", ")} onChange={(event) => update("tags", event.target.value.split(",").map((tag) => tag.trim()).filter(Boolean))} placeholder="e.g. wellbeing, safeguarding" /></div>
                    <div className="grid gap-4 sm:grid-cols-2"><ChoiceField label="Language" value={draft.language} options={LANGUAGES} onChange={(value) => update("language", value)} /><ChoiceField label="Gender" value={draft.gender} options={GENDERS} onChange={(value) => update("gender", value)} /></div>
                    <Field name="freestylePrompt" label="Scenario instructions" value={draft.freestylePrompt} onChange={update} textarea required helper="Describe the service user, their background, what they initially disclose, what they keep hidden, their emotional state, and how their behaviour should change in response to Sakinah." />
                </TabsContent>
            </Tabs>
            <DialogFooter><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button disabled={!meaningful} onClick={() => onSave({ ...draft, title: draft.title.trim() || "Untitled scenario" })}>Save Scenario</Button></DialogFooter>
        </DialogContent>
    </Dialog>;
}

export default function ScenarioLibraryPage() {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const [scenarios, setScenarios] = useState<Scenario[]>([]);
    const [loaded, setLoaded] = useState(false);
    const [editorOpen, setEditorOpen] = useState(false);
    const [editing, setEditing] = useState<Scenario | null>(null);
    const [deleting, setDeleting] = useState<Scenario | null>(null);
    const [importMessage, setImportMessage] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [bulkImportOpen, setBulkImportOpen] = useState(false);
    const [isAdmin, setIsAdmin] = useState(false);
    const [search, setSearch] = useState("");
    const [appliedSearch, setAppliedSearch] = useState("");
    const [searching, setSearching] = useState(false);
    const importInputRef = useRef<HTMLInputElement | null>(null);

    useEffect(() => {
        let cancelled = false;
        if (authLoading || !user) {
            setIsAdmin(false);
            return () => { cancelled = true; };
        }
        void getAuthUserApiV1UserAuthUserGet()
            .then((response) => {
                if (!cancelled) setIsAdmin(response.data?.is_superuser === true);
            })
            .catch(() => {
                if (!cancelled) setIsAdmin(false);
            });
        return () => { cancelled = true; };
    }, [authLoading, user]);
    useEffect(() => {
        let cancelled = false;
        if (authLoading || !user) return () => { cancelled = true; };
        void loadSakinahScenariosWithLegacyMigration()
            .then((loadedScenarios) => { if (!cancelled) setScenarios(loadedScenarios); })
            .catch((loadError) => {
                if (!cancelled) setImportMessage(loadError instanceof Error ? loadError.message : "Unable to load scenarios.");
            })
            .finally(() => { if (!cancelled) setLoaded(true); });
        return () => { cancelled = true; };
    }, [authLoading, user]);
    const openCreate = () => { setEditing(null); setEditorOpen(true); };
    const runScenarioSearch = async (value = search) => {
        setSearching(true);
        try {
            const term = value.trim();
            setScenarios(await listSakinahScenarios(term));
            setAppliedSearch(term);
        } catch (searchError) {
            setImportMessage(searchError instanceof Error ? searchError.message : "Unable to search scenarios.");
        } finally {
            setSearching(false);
        }
    };
    const clearScenarioSearch = () => {
        setSearch("");
        void runScenarioSearch("");
    };
    const handleImport = async (file: File | undefined) => {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".json")) {
            setImportMessage("Please select a scenario file with a .json extension.");
            if (importInputRef.current) importInputRef.current.value = "";
            return;
        }
        try {
            const imported = parseScenarioImport(await file.text(), scenarios);
            setSaving(true);
            const saved = [...scenarios];
            for (const scenario of imported) saved.push(await saveSakinahScenario(null, scenarioToDraft(scenario)));
            setScenarios(saved);
            setImportMessage(`Imported ${imported.length} scenario${imported.length === 1 ? "" : "s"}.`);
        } catch (importError) {
            setImportMessage(importError instanceof Error ? importError.message : "Unable to import scenarios.");
        } finally {
            setSaving(false);
            if (importInputRef.current) importInputRef.current.value = "";
        }
    };
    const handleSave = async (draft: ScenarioDraft) => {
        setSaving(true);
        try {
            const saved = await saveSakinahScenario(editing, draft);
            if (appliedSearch) setScenarios(await listSakinahScenarios(appliedSearch));
            else setScenarios((current) => editing
                ? current.map((item) => item.id === editing.id ? saved : item)
                : [...current, saved]);
            setEditorOpen(false);
        } catch (saveError) {
            setImportMessage(saveError instanceof Error ? saveError.message : "Unable to save scenario.");
        } finally {
            setSaving(false);
        }
    };
    const handleDuplicate = async (scenario: Scenario) => {
        setSaving(true);
        try {
            const saved = await saveSakinahScenario(null, scenarioToDraft(duplicateScenario(scenario, scenarios)));
            setScenarios((current) => [...current, saved]);
        } catch (duplicateError) {
            setImportMessage(duplicateError instanceof Error ? duplicateError.message : "Unable to duplicate scenario.");
        } finally {
            setSaving(false);
        }
    };
    const handleDelete = async () => {
        if (!deleting) return;
        setSaving(true);
        try {
            await deleteSakinahScenario(deleting.id);
            setScenarios((current) => current.filter((item) => item.id !== deleting.id));
            setDeleting(null);
        } catch (deleteError) {
            setImportMessage(deleteError instanceof Error ? deleteError.message : "Unable to delete scenario.");
        } finally {
            setSaving(false);
        }
    };
    const handleBulkImportCommitted = (response: BulkScenarioImportResponse) => {
        setImportMessage(`Imported ${response.imported} scenario${response.imported === 1 ? "" : "s"}; ${response.failed} failed.`);
        void loadSakinahScenariosWithLegacyMigration(appliedSearch)
            .then(setScenarios)
            .catch((loadError) => setImportMessage(loadError instanceof Error ? loadError.message : "Unable to refresh scenarios."));
    };
    return <main className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"><div className="space-y-2"><p className="text-sm font-medium uppercase tracking-[0.2em] text-primary">Sakinah</p><h1 className="text-3xl font-bold tracking-tight">Scenario Library</h1><p className="text-muted-foreground">Create and manage simulated service-user scenarios for Sakinah testing.</p></div><div className="flex flex-wrap gap-2"><input ref={importInputRef} type="file" accept=".json,application/json" className="hidden" onChange={(event) => void handleImport(event.target.files?.[0])} />{isAdmin ? <Button variant="outline" disabled={saving} onClick={() => setBulkImportOpen(true)}><Upload />Bulk Import Scenarios</Button> : null}<Button variant="outline" disabled={saving} onClick={() => importInputRef.current?.click()}><Upload />Import JSON</Button><Button disabled={saving} onClick={openCreate}><Plus />Create Scenario</Button></div></header>
        {importMessage ? <p role="status" className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">{importMessage}</p> : null}
        <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(event) => { event.preventDefault(); void runScenarioSearch(); }}><div className="relative flex-1"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search scenarios..." aria-label="Search scenarios" className="pl-9" /></div><Button type="submit" disabled={searching}><Search />Search</Button>{appliedSearch ? <Button type="button" variant="outline" disabled={searching} onClick={clearScenarioSearch}>Clear / Show all</Button> : null}</form>
        {loaded ? <p className="text-sm text-muted-foreground">{appliedSearch ? `${scenarios.length} match${scenarios.length === 1 ? "" : "es"}` : `${scenarios.length} scenario${scenarios.length === 1 ? "" : "s"}`}</p> : null}
        {loaded && scenarios.length === 0 ? <p role="status" className="rounded-lg border border-dashed p-8 text-center text-muted-foreground">{appliedSearch ? `No scenarios found for “${appliedSearch}”.` : "No scenarios found. Create or import a scenario to get started."}</p> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{scenarios.map((scenario) => <Card key={scenario.id} className="flex flex-col"><CardHeader><div className="flex items-center justify-between gap-3"><Badge variant="secondary">{formatScenarioNumber(scenario.sequence)}</Badge><Badge variant="outline" className="capitalize">{scenario.mode}</Badge></div><CardTitle className="mt-2">{scenario.title}</CardTitle><CardDescription className="line-clamp-3">{scenario.mode === "freestyle" ? scenario.freestylePrompt : scenario.persona}</CardDescription></CardHeader><CardContent className="flex-1 space-y-1 text-sm text-muted-foreground">{scenario.category ? <p>Category: {scenario.category}</p> : null}{scenario.tags.length ? <p>Tags: {scenario.tags.join(", ")}</p> : null}{scenario.language ? <p>Language: {scenario.language}</p> : null}{scenario.emotion ? <p>Emotion: {scenario.emotion}</p> : null}</CardContent><CardFooter className="flex flex-wrap gap-2"><Button size="sm" disabled={saving} onClick={() => router.push(`/sakinah/sim?scenario=${encodeURIComponent(scenario.id)}`)}><Play />Use in Simulation</Button><Button size="icon" variant="outline" disabled={saving} aria-label={`Edit ${scenario.title}`} onClick={() => { setEditing(scenario); setEditorOpen(true); }}><Pencil /></Button><Button size="icon" variant="outline" disabled={saving} aria-label={`Duplicate ${scenario.title}`} onClick={() => void handleDuplicate(scenario)}><Copy /></Button><Button size="icon" variant="outline" disabled={saving} aria-label={`Delete ${scenario.title}`} onClick={() => setDeleting(scenario)}><Trash2 /></Button></CardFooter></Card>)}</div>}
        <ScenarioEditor open={editorOpen} scenario={editing} onOpenChange={setEditorOpen} onSave={handleSave} />
        <BulkScenarioImport open={bulkImportOpen} onOpenChange={setBulkImportOpen} onCommitted={handleBulkImportCommitted} />
        <AlertDialog open={deleting !== null} onOpenChange={(open) => { if (!open && !saving) setDeleting(null); }}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Delete scenario?</AlertDialogTitle><AlertDialogDescription>This permanently removes {deleting ? formatScenarioNumber(deleting.sequence) : "this scenario"}. Other scenario numbers will not change.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel><AlertDialogAction disabled={saving} className="bg-destructive text-white hover:bg-destructive/90" onClick={() => void handleDelete()}>Delete Scenario</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
    </main>;
}
