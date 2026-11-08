"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { previewWorkflowPromptsApiV1WorkflowWorkflowIdPromptPreviewPost, replayWorkflowPromptsApiV1WorkflowWorkflowIdPromptReplayPost } from "@/client/sdk.gen";
import type { PromptPreviewResponse, PromptReplayResponse, PromptVariablesConfiguration } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

const defaults: PromptVariablesConfiguration = { mode: "inline", include_weekday: true, call_context_note: null };
const percentage = (value: number | null) => value === null ? "No successful pairs" : `${Math.round(value * 100)}%`;
const cacheStatus: Record<string, string> = { ready: "Cache ready", not_ready: "Cache not ready yet", too_small: "Provider rejected this node as too small to cache", unsupported: "This model does not support explicit caching", disabled: "Caching is disabled by the server", registry_unavailable: "Cache registry unavailable; requests use the inline prompt", error: "Cache unavailable; requests use the inline prompt" };

export function PromptCachingSection({ workflowId, workflowConfigurations, templateContextVariables, onSave }: {
    workflowId: number;
    workflowConfigurations: WorkflowConfigurations;
    templateContextVariables: Record<string, string>;
    onSave: (value: WorkflowConfigurations) => Promise<void>;
}) {
    const { user, loading: authLoading } = useAuth();
    const [layout, setLayout] = useState<PromptVariablesConfiguration>({ ...defaults, ...workflowConfigurations.prompt_variables });
    const [preview, setPreview] = useState<PromptPreviewResponse | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [sampleRun, setSampleRun] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [comparing, setComparing] = useState(false);
    const [comparison, setComparison] = useState<PromptReplayResponse | null>(null);
    const dirty = JSON.stringify(layout) !== JSON.stringify({ ...defaults, ...workflowConfigurations.prompt_variables });
    useUnsavedChanges("prompt-caching", dirty);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        const timer = setTimeout(async () => {
            setPreviewLoading(true);
            setError(null);
            try {
                const response = await previewWorkflowPromptsApiV1WorkflowWorkflowIdPromptPreviewPost({
                    path: { workflow_id: workflowId },
                    body: { prompt_variables: layout, sample_values: templateContextVariables, sample_run_id: sampleRun ? Number(sampleRun) : null },
                });
                if (cancelled) return;
                if (response.error) { setError(detailFromError(response.error, "Could not preview prompts")); return; }
                setPreview(response.data ?? null);
            } catch { if (!cancelled) setError("Could not load the prompt preview"); }
            finally { if (!cancelled) setPreviewLoading(false); }
        }, 400);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [authLoading, user, workflowId, layout, sampleRun, templateContextVariables]);

    const save = async () => {
        setSaving(true);
        try {
            const configurations: WorkflowConfigurations = { ...workflowConfigurations, prompt_variables: layout };
            delete configurations.prompt_caching;
            await onSave(configurations);
            toast.success("Prompt settings saved. Publish the agent to apply them to new calls.");
        } catch { setError("Could not save prompt settings"); }
        finally { setSaving(false); }
    };
    const compare = async () => {
        setComparing(true);
        setError(null);
        try {
            const response = await replayWorkflowPromptsApiV1WorkflowWorkflowIdPromptReplayPost({ path: { workflow_id: workflowId }, body: { prompt_variables: { ...layout, mode: "call_context" }, limit: 3, max_turns: 10 } });
            if (response.error) { setError(detailFromError(response.error, "Could not compare prompts")); return; }
            setComparison(response.data ?? null);
        } catch { setError("The comparison did not finish. Use the replay CLI for longer evaluations."); }
        finally { setComparing(false); }
    };

    return <Card id="prompt-caching">
        <CardHeader>
            <CardTitle className="text-base">Prompt variables & caching</CardTitle>
            <CardDescription>Keep shared instructions stable across calls. Moving variables can change the agent’s responses; compare recent calls before publishing.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
            <div className="flex items-center justify-between gap-4">
                <Label htmlFor="optimize-prompt-caching">Optimize prompt caching</Label>
                <Switch id="optimize-prompt-caching" checked={layout.mode === "call_context"} onCheckedChange={enabled => setLayout({ ...layout, mode: enabled ? "call_context" : "inline" })} />
            </div>
            <p className="text-sm text-muted-foreground">Keep shared instructions stable by sending call-specific values in a hidden context message. Dograh manages caching automatically for your provider and model. This can change the agent’s responses; compare recent calls before publishing.</p>
            {layout.mode === "call_context" && preview && <p className="text-sm text-muted-foreground">
                {preview.explicit_cache_available
                    ? "Dograh automatically creates and refreshes caches for eligible nodes. Requests continue normally when a cache is unavailable. Google charges for cache storage."
                    : "Shared instructions are kept stable. Cache reuse depends on your provider and model."}
            </p>}
            {layout.mode === "call_context" && <>
                <div className="flex items-center justify-between gap-4">
                    <Label htmlFor="include-weekday">Include the weekday alongside time variables</Label>
                    <Switch id="include-weekday" checked={layout.include_weekday ?? true} onCheckedChange={include_weekday => setLayout({ ...layout, include_weekday })} />
                </div>
                <div className="space-y-2">
                    <Label htmlFor="context-note">Instructions for reading call variables</Label>
                    <Textarea id="context-note" rows={6} value={layout.call_context_note ?? preview?.default_note ?? ""} onChange={event => setLayout({ ...layout, call_context_note: event.target.value })} />
                    <div className="flex gap-2">
                        <Button variant="outline" size="sm" onClick={() => setLayout({ ...layout, call_context_note: null })}>English default</Button>
                        <Button variant="outline" size="sm" disabled={!preview} onClick={() => setLayout({ ...layout, call_context_note: preview?.italian_note })}>Italian default</Button>
                    </div>
                </div>
            </>}
            <div className="space-y-2">
                <Label htmlFor="sample-run">Sample run ID (optional)</Label>
                <Input id="sample-run" type="number" min={1} placeholder="Use test variables" value={sampleRun} onChange={event => setSampleRun(event.target.value)} />
            </div>
            <p className="text-sm text-muted-foreground">Preview uses the saved draft and test variables, or values from the selected run. Save graph edits before previewing or comparing.</p>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            {previewLoading && <p role="status" className="text-sm">Updating preview…</p>}
            {preview && <div className="space-y-3">
                {preview.notices.map(notice => <p key={notice} className="text-xs text-muted-foreground">{notice}</p>)}
                {preview.call_context && <details><summary className="cursor-pointer text-sm font-medium">Call-context message</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{preview.call_context}</pre></details>}
                {preview.nodes.map(node => <details key={node.node_id} className="rounded border p-3">
                    <summary className="cursor-pointer text-sm font-medium">{node.node_name} — ~{node.static_tokens_estimate.toLocaleString()} static / ~{node.per_call_tokens_estimate.toLocaleString()} per-call tokens</summary>
                    {node.cache_status && <p className="mt-2 text-xs text-muted-foreground">{cacheStatus[node.cache_status] ?? "Cache unavailable"}</p>}
                    {preview.minimum_tokens && node.static_tokens_estimate < preview.minimum_tokens && node.cache_status !== "ready" && layout.mode === "call_context" && <p className="mt-2 text-xs text-amber-700">Below the documented {preview.minimum_tokens.toLocaleString()}-token minimum. Provider acceptance may differ.</p>}
                    {node.unresolved_variables.length > 0 && <p className="mt-2 text-xs text-amber-700">No sample value or extraction source: {node.unresolved_variables.join(", ")}. A pre-call fetch or live integration may supply these.</p>}
                    <p className="mt-2 text-xs">Placeholders: {node.variables.join(", ") || "None"}</p>
                    <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-muted p-3 text-xs">{node.system_prompt}</pre>
                    <details className="mt-2"><summary className="cursor-pointer text-xs">Tool declarations</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(node.tools, null, 2)}</pre></details>
                </details>)}
            </div>}
            <div className="space-y-2 border-t pt-4">
                <Button variant="outline" onClick={compare} disabled={comparing || authLoading || !user}> {comparing ? "Comparing recent calls…" : "Compare on recent calls"}</Button>
                <p className="text-xs text-muted-foreground">Up to 10 turns from the last 3 calls, twice per layout, plus automated comparisons of spoken replies. The optimized layout uses automatic caching where available. Inference and cache storage may incur provider charges. Tools are never executed.</p>
                {comparison && <div className="space-y-2 text-sm">
                    <p>{comparison.valid_turns} successful turns; {comparison.failed_turns} failed.</p>
                    <p>Action agreement: {percentage(comparison.action_agreement)}. Within-layout agreement: inline {percentage(comparison.inline_noise_agreement)}, call context {percentage(comparison.call_context_noise_agreement)}.</p>
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs">
                            <caption className="pb-2 text-left">A: inline · B: call context · two samples each</caption>
                            <thead><tr><th>Sample</th><th>Cached input</th><th>TTFT p50</th><th>Recorded action match</th><th>Failures</th></tr></thead>
                            <tbody>{Object.entries(comparison.sample_metrics).map(([name, metrics]) => <tr key={name}>
                                <td className="py-1">{name}</td><td>{percentage(metrics.cached_share ?? null)}</td><td>{metrics.ttft_p50_seconds == null ? "—" : `${metrics.ttft_p50_seconds.toFixed(2)}s`}</td><td>{percentage(metrics.production_action_agreement ?? null)}</td><td>{metrics.failed_requests ?? 0}</td>
                            </tr>)}</tbody>
                        </table>
                    </div>
                    {Object.entries(comparison.semantic_agreement).map(([name, result]) => <p key={name}>Same conversational move ({name.replaceAll("_", " ")}): {percentage(result.same_move ?? null)} across {result.judged_turns ?? 0} spoken pairs.</p>)}
                    {comparison.notices.map(notice => <p key={notice} className="text-xs text-muted-foreground">{notice}</p>)}
                    <details><summary className="cursor-pointer">Inspect paired responses</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(comparison.turns, null, 2)}</pre></details>
                </div>}
            </div>
        </CardContent>
        <CardFooter><Button onClick={save} disabled={!dirty || saving || authLoading || !user}>{saving ? "Saving…" : "Save Prompt Settings"}</Button></CardFooter>
    </Card>;
}
