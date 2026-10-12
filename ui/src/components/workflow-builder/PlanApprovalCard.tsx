'use client';

import { useId, useState } from 'react';

import type { BuilderPlanApproval, CallBrief } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

import { PlanFlow } from './PlanFlow';

function PlanList({ title, items }: { title: string; items?: string[] }) {
    if (!items?.length) return null;
    return <section><h3 className="font-medium">{title}</h3><ul className="mt-1 list-disc space-y-1 pl-5">{items.map((item, index) => <li key={index}>{item}</li>)}</ul></section>;
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

    return <article className="min-w-0 space-y-4 rounded-xl border bg-background p-4 text-sm leading-5 sm:p-5" aria-label="Plan awaiting approval">
        <header className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Your agent plan</h2>
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{brief.direction === 'both' ? 'Inbound & outbound' : brief.direction === 'inbound' ? 'Inbound' : 'Outbound'}</span>
            </div>
            <p className="whitespace-pre-wrap break-words">{brief.goal}</p>
            <p className="text-xs text-muted-foreground">For {brief.audience}</p>
        </header>
        <PlanFlow steps={brief.conversation_steps} />
        {!!brief.data_operations?.length && <section>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">Planned integrations</h3>
            <ul className="space-y-2">{brief.data_operations.map((operation, index) => <li key={index}>
                <p><span className="font-medium">{operation.system}</span> · {operation.purpose}</p>
                <p className="text-xs text-muted-foreground">If it fails: {operation.failure_behavior}</p>
            </li>)}</ul>
        </section>}
        {!!brief.assumptions?.length && <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-5"><PlanList title="Assumptions to confirm" items={brief.assumptions} /></div>}
        <details className="group">
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">Full plan details</summary>
            <div className="mt-3 space-y-4 border-l-2 pl-3 text-xs leading-5">
                <section><h3 className="font-medium">When it starts</h3><p>{brief.trigger}</p></section>
                <PlanList title="Conversation steps" items={brief.conversation_steps} />
                <PlanList title="Information available at the start" items={brief.information_available_at_start} />
                <PlanList title="Information to collect" items={brief.information_to_collect} />
                <PlanList title="Business rules" items={brief.business_rules} />
                {brief.data_operations?.length ? <section><h3 className="font-medium">Data and integrations</h3><ul className="mt-1 space-y-3">{brief.data_operations.map((operation, index) => <li key={index}>
                    <p className="font-medium">{operation.purpose} · {operation.system}</p>
                    <p>When: {operation.timing}</p>
                    {!!operation.inputs?.length && <p>Information sent: {operation.inputs.join(', ')}</p>}
                    <p>Expected result: {operation.expected_result}</p>
                </li>)}</ul></section> : <p>No data lookup or sync planned.</p>}
                <PlanList title="Successful outcome" items={brief.success_criteria} />
                <PlanList title="When the call ends" items={brief.exit_conditions} />
                <PlanList title="Scenarios to check" items={brief.acceptance_scenarios} />
            </div>
        </details>
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
