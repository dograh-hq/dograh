'use client';

import { useId, useState } from 'react';

import type { BuilderPlanApproval, CallBrief } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

const DIRECTION_LABEL: Record<CallBrief['direction'], string> = { inbound: 'Inbound', outbound: 'Outbound', both: 'Inbound & outbound' };

function PlanList({ title, items }: { title: string; items?: string[] }) {
    if (!items?.length) return null;
    return <section><h3 className="font-medium">{title}</h3><ul className="mt-1 list-disc space-y-1 pl-5">{items.map((item, index) => <li key={index} className="break-words">{item}</li>)}</ul></section>;
}

export function PlanApprovalCard({ interruptId, revision, brief, disabled, onSubmit }: {
    interruptId: string;
    revision: number;
    brief: CallBrief;
    disabled: boolean;
    onSubmit: (approval: BuilderPlanApproval) => void;
}) {
    const [feedback, setFeedback] = useState('');
    const [requestingChanges, setRequestingChanges] = useState(false);
    const feedbackId = useId();
    const decide = (decision: 'approve' | 'revise') => onSubmit({
        interrupt_id: interruptId,
        brief_revision: revision,
        decision,
        feedback: decision === 'revise' ? feedback.trim() : '',
    });

    return <article className="min-w-0 space-y-4 rounded-xl border bg-background p-4 text-sm leading-5 sm:p-5" aria-label="Requirements awaiting approval">
        <header className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Your agent requirements</h2>
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{DIRECTION_LABEL[brief.direction]}</span>
            </div>
            <p className="whitespace-pre-wrap break-words">{brief.goal}</p>
        </header>
        <PlanList title="Information to collect" items={brief.information_to_collect} />
        <PlanList title="Business rules" items={brief.business_rules} />
        <PlanList title="Successful outcome" items={brief.success_criteria} />
        {!!brief.assumptions?.length && <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-5"><PlanList title="Assumptions to confirm" items={brief.assumptions} /></div>}
        <div className="space-y-3 border-t pt-4">
            {requestingChanges ? <>
                <label htmlFor={feedbackId} className="text-xs font-medium">What would you like to change?</label>
                <Textarea id={feedbackId} autoFocus value={feedback} onChange={event => setFeedback(event.target.value)} maxLength={12000} disabled={disabled} placeholder="For example, collect a callback request instead of booking an appointment." />
                <div className="flex flex-wrap gap-2">
                    <Button type="button" disabled={disabled || !feedback.trim()} onClick={() => decide('revise')}>Submit changes</Button>
                    <Button type="button" variant="ghost" disabled={disabled} onClick={() => { setFeedback(''); setRequestingChanges(false); }}>Cancel</Button>
                </div>
            </> : <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={disabled} onClick={() => decide('approve')}>Approve &amp; build</Button>
                <Button type="button" variant="outline" disabled={disabled} onClick={() => setRequestingChanges(true)}>Request changes</Button>
            </div>}
            <p className="text-xs text-muted-foreground">You can review the built agent before saving.</p>
        </div>
    </article>;
}
