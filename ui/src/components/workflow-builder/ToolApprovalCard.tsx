'use client';

import { useId, useState } from 'react';

import type { BuilderToolApproval } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

const CATEGORY_LABELS: Record<string, string> = {
    http_api: 'API call',
    transfer_call: 'Call transfer',
    transfer_agent: 'Agent transfer',
    end_call: 'End call',
    calculator: 'Calculator',
    mcp: 'MCP server',
};

function toolCategory(tool: Record<string, unknown> | null | undefined): string | undefined {
    const category = tool?.category;
    if (typeof category === 'string') return category;
    const definition = tool?.definition;
    if (definition && typeof definition === 'object' && 'type' in definition) {
        const type = (definition as { type?: unknown }).type;
        if (typeof type === 'string') return type;
    }
    return undefined;
}

/**
 * Human approval for a tool the builder proposes to create or edit. Approving
 * commits it to the account (every workflow that references an edited tool sees
 * the change); denying sends the agent feedback instead. Mirrors
 * PlanApprovalCard: plain-language summary up top, the exact definition behind a
 * details toggle, and a two-state approve / request-changes footer.
 */
export function ToolApprovalCard({ interruptId, operation, tool, toolUuid, plainLanguage, usedByCount, disabled, onSubmit }: {
    interruptId: string;
    operation: 'create' | 'update';
    tool: Record<string, unknown> | null | undefined;
    toolUuid?: string | null;
    plainLanguage?: string | null;
    usedByCount?: number | null;
    disabled: boolean;
    onSubmit: (approval: BuilderToolApproval) => void;
}) {
    const [feedback, setFeedback] = useState('');
    const [denying, setDenying] = useState(false);
    const feedbackId = useId();
    const decide = (decision: 'approve' | 'deny') => onSubmit({
        interrupt_id: interruptId,
        decision,
        feedback: decision === 'deny' ? feedback.trim() : '',
    });

    const category = toolCategory(tool);
    const name = typeof tool?.name === 'string' ? tool.name : undefined;
    const isEdit = operation === 'update';

    return <article className="min-w-0 space-y-4 rounded-xl border bg-background p-4 text-sm leading-5 sm:p-5" aria-label="Tool awaiting approval">
        <header className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">{isEdit ? 'Edit a tool' : 'New tool'}</h2>
                {category && <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{CATEGORY_LABELS[category] ?? category}</span>}
            </div>
            {name && <p className="font-medium break-words">{name}</p>}
            {plainLanguage && <p className="whitespace-pre-wrap break-words text-muted-foreground">{plainLanguage}</p>}
        </header>
        {isEdit && <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-5">
            This edits a shared tool, so the change applies to every workflow that uses it{typeof usedByCount === 'number' ? ` (${usedByCount} ${usedByCount === 1 ? 'workflow' : 'workflows'})` : ''}.
        </div>}
        <details className="group">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Tool definition</summary>
            <div className="mt-3 border-l-2 pl-3">
                {toolUuid && <p className="mb-2 text-xs text-muted-foreground">Tool ID: {toolUuid}</p>}
                <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs leading-5">{JSON.stringify(tool ?? {}, null, 2)}</pre>
            </div>
        </details>
        <div className="space-y-3 border-t pt-4">
            {denying ? <>
                <label htmlFor={feedbackId} className="text-xs font-medium">What should change? (optional)</label>
                <Textarea id={feedbackId} autoFocus value={feedback} onChange={event => setFeedback(event.target.value)} maxLength={12000} disabled={disabled} placeholder="For example, create a separate tool instead of editing the shared one, or point it at a different endpoint." />
                <div className="flex flex-wrap gap-2">
                    <Button type="button" disabled={disabled} onClick={() => decide('deny')}>Send feedback</Button>
                    <Button type="button" variant="ghost" disabled={disabled} onClick={() => { setFeedback(''); setDenying(false); }}>Cancel</Button>
                </div>
            </> : <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={disabled} onClick={() => decide('approve')}>{isEdit ? 'Approve edit' : 'Approve & create'}</Button>
                <Button type="button" variant="outline" disabled={disabled} onClick={() => setDenying(true)}>Deny</Button>
            </div>}
            <p className="text-xs text-muted-foreground">{isEdit ? 'Approving updates the tool for every workflow that uses it.' : 'Approving adds this tool to your account so the agent can use it.'}</p>
        </div>
    </article>;
}
