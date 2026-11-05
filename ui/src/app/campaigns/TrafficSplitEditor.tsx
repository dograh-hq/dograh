"use client";

import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

import { getWorkflowsSummaryApiV1WorkflowSummaryGet, getWorkflowVersionSummariesApiV1WorkflowWorkflowIdVersionSummariesGet } from '@/client/sdk.gen';
import type { TrafficVariantRequest, WorkflowSummaryResponse, WorkflowVersionSummaryResponse } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export function trafficSplitError(variants: TrafficVariantRequest[]): string | null {
    if (!variants.length || variants.length > 5) return 'Choose between one and five variants.';
    if (variants.some(v => !Number.isInteger(v.workflow_id) || v.workflow_id <= 0)) return 'Choose an agent for every variant.';
    if (variants.some(v => !Number.isInteger(v.weight) || v.weight < 1 || v.weight > 100)) return 'Each percentage must be a whole number from 1 to 100.';
    if (variants.reduce((total, v) => total + v.weight, 0) !== 100) return 'Traffic percentages must add up to 100%.';
    if (new Set(variants.map(v => `${v.workflow_id}:${v.workflow_definition_id ?? 'latest'}`)).size !== variants.length) return 'Choose a different agent or version for each variant.';
    return null;
}

export default function TrafficSplitEditor({ value, onChange, disabled = false, editing = false }: {
    value: TrafficVariantRequest[];
    onChange: (variants: TrafficVariantRequest[]) => void;
    disabled?: boolean;
    editing?: boolean;
}) {
    const { user, loading } = useAuth();
    const [agents, setAgents] = useState<WorkflowSummaryResponse[]>([]);
    const [agentsLoading, setAgentsLoading] = useState(true);
    const [versions, setVersions] = useState<Record<number, WorkflowVersionSummaryResponse[]>>({});
    const [agentError, setAgentError] = useState<string | null>(null);
    const [versionError, setVersionError] = useState<string | null>(null);
    const [retry, setRetry] = useState(0);
    const ids = [...new Set(value.map(v => v.workflow_id).filter(Boolean))].sort((a, b) => a - b).join(',');

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        setAgentError(null);
        setAgentsLoading(true);
        getWorkflowsSummaryApiV1WorkflowSummaryGet({ query: { status: 'active,archived' } }).then(response => {
            if (cancelled) return;
            if (response.error) setAgentError(detailFromError(response.error, 'Failed to load agents'));
            else setAgents(response.data ?? []);
        }).catch(() => { if (!cancelled) setAgentError('Failed to load agents'); })
            .finally(() => { if (!cancelled) setAgentsLoading(false); });
        return () => { cancelled = true; };
    }, [loading, user, retry]);

    useEffect(() => {
        if (loading || !user || !ids) return;
        let cancelled = false;
        setVersionError(null);
        Promise.all(ids.split(',').map(async id => {
            const response = await getWorkflowVersionSummariesApiV1WorkflowWorkflowIdVersionSummariesGet({ path: { workflow_id: Number(id) } });
            if (response.error) throw new Error(detailFromError(response.error, 'Failed to load versions'));
            return [Number(id), response.data ?? []] as const;
        })).then(entries => {
            if (!cancelled) setVersions(Object.fromEntries(entries));
        }).catch(error => {
            if (!cancelled) setVersionError(error instanceof Error ? error.message : 'Failed to load versions');
        });
        return () => { cancelled = true; };
    }, [ids, loading, user, retry]);

    const total = value.reduce((sum, v) => sum + v.weight, 0);
    const validationError = value.length > 1 || value[0]?.workflow_id ? trafficSplitError(value) : null;
    const update = (index: number, patch: Partial<TrafficVariantRequest>) => onChange(value.map((v, i) => i === index ? { ...v, ...patch } : v));
    const splitEvenly = () => onChange(value.map((v, index) => ({ ...v, weight: Math.floor(100 / value.length) + (index < 100 % value.length ? 1 : 0) })));

    return <fieldset className="space-y-3" disabled={disabled}>
        <legend className="text-sm font-medium mb-3">Agents &amp; traffic split</legend>
        {value.map((variant, index) => <div key={index} className="rounded-md border p-3 space-y-3">
            <div className="grid grid-cols-[1fr_auto] gap-2 items-end">
                <div className="space-y-2">
                    <Label htmlFor={`split-agent-${index}`}>Agent {index + 1}</Label>
                    <Select disabled={disabled} value={variant.workflow_id ? String(variant.workflow_id) : ''} onValueChange={id => update(index, { workflow_id: Number(id), workflow_definition_id: null })}>
                        <SelectTrigger id={`split-agent-${index}`}><SelectValue placeholder="Choose an agent" /></SelectTrigger>
                        <SelectContent>
                            {agentsLoading ? <SelectItem value="loading" disabled>Loading agents…</SelectItem> : agents.length === 0 ? <SelectItem value="empty" disabled>No agents found</SelectItem> : agents.map(agent => <SelectItem key={agent.id} value={String(agent.id)}>{agent.name} (#{agent.id})</SelectItem>)}
                        </SelectContent>
                    </Select>
                </div>
                <Button type="button" variant="ghost" size="icon" aria-label={`Remove variant ${index + 1}`} disabled={disabled || value.length === 1} onClick={() => onChange(value.filter((_, i) => i !== index))}><Trash2 className="h-4 w-4" /></Button>
            </div>
            <div className="grid grid-cols-[1fr_6rem] gap-3">
                <div className="space-y-2">
                    <Label htmlFor={`split-version-${index}`}>Version</Label>
                    <Select disabled={disabled || !variant.workflow_id} value={String(variant.workflow_definition_id ?? 'latest')} onValueChange={id => update(index, { workflow_definition_id: id === 'latest' ? null : Number(id) })}>
                        <SelectTrigger id={`split-version-${index}`}><SelectValue placeholder="Choose a version" /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="latest">Latest published</SelectItem>
                            {(versions[variant.workflow_id] ?? []).map(version => <SelectItem key={version.id} value={String(version.id)}>Version {version.version_number ?? version.id}</SelectItem>)}
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-2">
                    <Label htmlFor={`split-weight-${index}`}>Traffic %</Label>
                    <Input id={`split-weight-${index}`} type="number" min={1} max={100} step={1} required value={variant.weight || ''} onChange={e => update(index, { weight: Number(e.target.value) })} />
                </div>
            </div>
        </div>)}
        <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" disabled={disabled || value.length >= 5} onClick={() => onChange([...value, { workflow_id: 0, workflow_definition_id: null, weight: 1 }])}><Plus className="h-4 w-4 mr-1" />Add agent</Button>
            <Button type="button" variant="outline" disabled={disabled} onClick={splitEvenly}>Split evenly</Button>
            <span className={`text-sm ml-auto ${total === 100 ? 'text-muted-foreground' : 'text-destructive'}`} aria-live="polite">Total: {total}% / 100%</span>
        </div>
        {(agentError || versionError) && <div role="alert" className="text-sm text-destructive">{agentError || versionError} <Button type="button" variant="link" onClick={() => setRetry(r => r + 1)}>Retry</Button></div>}
        {validationError && <p className="text-sm text-destructive" aria-live="polite">{validationError}</p>}
        <p className="text-sm text-muted-foreground">Any listed version can receive traffic. Choose a numbered version to keep it fixed; “Latest published” follows new releases. Percentages are approximate.</p>
        {editing && <p className="text-sm text-muted-foreground">Changes apply from the next batch, including retries. Changing weights, agents, or versions can move contacts between variants. Live calls keep their original version.</p>}
    </fieldset>;
}
