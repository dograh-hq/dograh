'use client';

import { ArrowUp, Loader2, Plus } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { getBuilderSessionApiV1WorkflowBuilderSessionIdGet, openWorkflowBuilderSessionApiV1WorkflowBuilderWorkflowWorkflowIdSessionPost, saveBuilderSessionApiV1WorkflowBuilderSessionIdSavePost, sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost } from '@/client/sdk.gen';
import type { BuilderSession, BuilderTurnRequest } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { PlanApprovalCard } from '@/components/workflow-builder/PlanApprovalCard';
import { QuestionCards } from '@/components/workflow-builder/QuestionCards';
import { WorkflowPreview } from '@/components/workflow-builder/WorkflowPreview';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

type WorkflowBuilderProps = {
    workflowId?: number;
    disabled?: boolean;
    onSaved?: () => Promise<void>;
};

export function WorkflowBuilder({ workflowId, disabled = false, onSaved }: WorkflowBuilderProps) {
    const router = useRouter();
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const organization = user && ('selectedTeam' in user ? user.selectedTeam?.id : 'organizationId' in user ? user.organizationId : '');
    const [session, setSession] = useState<BuilderSession | null>(null);
    const [sessionId, setSessionId] = useState('');
    const [input, setInput] = useState('');
    const [busy, setBusy] = useState(false);
    const [restoring, setRestoring] = useState(true);
    const [saving, setSaving] = useState(false);
    const [status, setStatus] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [optimisticMessage, setOptimisticMessage] = useState('');
    const abort = useRef<AbortController | null>(null);
    const bottom = useRef<HTMLDivElement>(null);
    const composer = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        if (authLoading || !user) return;
        const controller = new AbortController();
        setSession(null); setSessionId(''); setError(null); setRestoring(true);
        const id = new URL(window.location.href).searchParams.get('session');
        if (!workflowId && !id) { setSessionId(crypto.randomUUID()); setRestoring(false); }
        else {
            void (async () => {
                try {
                    const token = await getAccessToken();
                    const options = { signal: controller.signal, headers: { Authorization: `Bearer ${token}` } };
                    const result = workflowId
                        ? await openWorkflowBuilderSessionApiV1WorkflowBuilderWorkflowWorkflowIdSessionPost({ path: { workflow_id: workflowId }, ...options })
                        : await getBuilderSessionApiV1WorkflowBuilderSessionIdGet({ path: { session_id: id! }, ...options });
                    if (result.error) throw new Error(detailFromError(result.error, 'Could not restore this conversation.'));
                    if (!result.data) throw new Error('Could not restore this conversation.');
                    if (!controller.signal.aborted) { setSession(result.data); setSessionId(result.data.id); }
                } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not restore this conversation.'); }
                finally { if (!controller.signal.aborted) setRestoring(false); }
            })();
        }
        return () => { controller.abort(); abort.current?.abort(); };
    // The authenticated user and organization identify this conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authLoading, user?.id, organization, workflowId]);

    useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [session?.messages?.length, session?.pending?.length, status]);
    const pendingQuestions = session?.pending?.filter(item => item.kind === 'questions') ?? [];
    const pendingApproval = session?.pending?.find(item => item.kind === 'plan_approval');
    const awaitingUser = !!pendingQuestions.length || !!pendingApproval;
    const isStarting = !restoring && !session?.messages?.length && !awaitingUser && !session?.proposal && !busy && !optimisticMessage && !session?.can_continue;
    useEffect(() => { if (isStarting) composer.current?.focus(); }, [isStarting]);
    const newConversation = () => {
        abort.current?.abort(); setSession(null); setInput(''); setError(null); setOptimisticMessage(''); setStatus('');
        setSessionId(crypto.randomUUID());
        const url = new URL(window.location.href); url.searchParams.delete('session');
        window.history.replaceState(window.history.state, '', url);
    };
    const send = async (content: Pick<BuilderTurnRequest, 'message' | 'answers' | 'approval'> = {}) => {
        if (!user || !sessionId || busy || saving || restoring || disabled) return;
        const controller = new AbortController(); abort.current = controller;
        setBusy(true); setError(null); setStatus('Thinking about your agent…'); setOptimisticMessage(content.message ?? '');
        if (!workflowId) {
            const url = new URL(window.location.href); url.searchParams.set('session', sessionId);
            window.history.replaceState(window.history.state, '', url);
        }
        let completed = false;
        try {
            const token = await getAccessToken();
            const result = await sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost({ path: { session_id: sessionId },
                body: { request_id: crypto.randomUUID(), checkpoint: session?.checkpoint ?? null, workflow_id: workflowId, ...content },
                sseMaxRetryAttempts: 1, signal: controller.signal, headers: { Authorization: `Bearer ${token}` },
            });
            for await (const raw of result.stream) {
                if (!raw || typeof raw !== 'object') continue;
                const event = raw as { type: string; message?: string; session?: BuilderSession };
                if (event.type === 'session' && event.session) { setSession(event.session); setOptimisticMessage(''); setInput(''); }
                if (event.type === 'status') setStatus(event.message ?? 'Working…');
                if (event.type === 'error') throw new Error(event.message ?? 'Builder failed.');
                if (event.type === 'done') completed = true;
            }
            if (!completed) throw new Error('Connection interrupted. Reload to restore your conversation.');
        } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not complete the request.'); }
        finally { if (abort.current === controller) { setBusy(false); setStatus(''); setOptimisticMessage(''); } }
    };
    const save = async () => {
        if (!session?.checkpoint || busy || saving || disabled) return;
        setSaving(true); setError(null);
        try {
            const token = await getAccessToken();
            const result = await saveBuilderSessionApiV1WorkflowBuilderSessionIdSavePost({ path: { session_id: sessionId }, body: { checkpoint: session.checkpoint, workflow_id: workflowId }, headers: { Authorization: `Bearer ${token}` } });
            if (result.error) throw new Error(detailFromError(result.error, 'Could not save the draft.'));
            if (!result.data) throw new Error('The server did not return a workflow.');
            if (onSaved) await onSaved();
            else router.push(`/workflow/${result.data.workflow_id}?view=builder`);
        } catch (err) { setError(err instanceof Error ? err.message : 'Could not save the draft.'); }
        finally { setSaving(false); }
    };
    return <div className="flex min-h-full flex-1 flex-col">
        <header className="mb-6 flex items-center justify-between gap-3"><div><h1 className="text-xl font-semibold tracking-tight">{workflowId ? 'Agent builder' : 'Create an agent'}</h1><p className="mt-1 text-sm text-muted-foreground">{workflowId ? 'Continue your conversation and refine this agent. Save changes to update the draft.' : 'Describe what you need. Build and refine it together.'}</p></div>{!workflowId && !isStarting && <Button variant="outline" size="sm" onClick={newConversation} disabled={busy || saving || restoring}><Plus className="mr-2 h-4 w-4" />New conversation</Button>}</header>
        {disabled && <p role="status" className="mb-4 rounded-md border bg-muted p-3 text-sm">Save your canvas changes before continuing in the builder.</p>}
        <div className={`grid flex-1 gap-6 ${session?.proposal ? (workflowId ? '2xl:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.1fr)]' : 'lg:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.1fr)]') : `mx-auto w-full max-w-3xl ${isStarting ? 'content-center' : ''}`}`}>
            <section className={`flex min-w-0 flex-col ${isStarting ? '' : 'min-h-[650px] rounded-2xl border bg-muted/20'}`}>
                <div className={isStarting ? 'space-y-5 px-4 pb-6 text-center' : 'max-h-[65vh] min-h-[380px] flex-1 space-y-5 overflow-y-auto p-5 md:p-7'} aria-live="polite">
                    {restoring ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Restoring conversation…</div> : isStarting && <div>
                        <h2 className="text-2xl font-medium tracking-tight">{workflowId ? 'What would you like to change?' : 'What should your agent do?'}</h2>
                        <p className="mt-3 text-sm leading-6 text-muted-foreground">{workflowId ? 'Start a conversation to refine this agent. The builder will use your current saved workflow.' : 'Describe who it should speak with and what it needs to do.'}</p>
                    </div>}
                    {session?.messages?.map(message => <div key={message.id} className={message.role === 'user' ? 'ml-8 rounded-2xl bg-muted px-4 py-3' : 'pr-5'}><div className="mb-1.5 text-xs font-medium text-muted-foreground">{message.role === 'user' ? 'You' : 'Dograh'}</div><div className="whitespace-pre-wrap text-sm leading-6">{message.content}</div></div>)}
                    {optimisticMessage && <div className="ml-8 rounded-2xl bg-muted px-4 py-3 text-sm">{optimisticMessage}</div>}
                    {!!pendingQuestions.length && <QuestionCards key={pendingQuestions.map(item => item.id).join(':')} pending={pendingQuestions} disabled={busy || saving || disabled} onSubmit={answers => void send({ answers })} />}
                    {pendingApproval?.brief && pendingApproval.brief_revision != null && <PlanApprovalCard key={pendingApproval.id} interruptId={pendingApproval.id} revision={pendingApproval.brief_revision} brief={pendingApproval.brief} disabled={busy || saving || restoring || disabled} onSubmit={approval => void send({ approval })} />}
                    {busy && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{status}</div>}
                    {error && <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{error}
                        {error.includes('/model-configurations') && <Link href="/model-configurations" className="mt-2 block font-medium underline">Open Model Configurations</Link>}
                    </div>}
                    {!busy && !awaitingUser && session?.can_continue && <Button variant="outline" disabled={disabled || saving} onClick={() => void send()}>Resume building</Button>}
                    <div ref={bottom} />
                </div>
                <form className={`rounded-xl border bg-background p-3 ${isStarting ? 'shadow-sm' : 'm-4'}`} onSubmit={event => { event.preventDefault(); if (input.trim()) void send({ message: input.trim() }); }}>
                    <Textarea ref={composer} aria-label="Describe your agent or request a change" placeholder={pendingApproval ? 'Review the requirements above to continue…' : pendingQuestions.length ? 'Answer the questions above to continue…' : workflowId || session?.proposal ? 'Ask for a change to your agent…' : 'Build me an agent that…'} className="min-h-[80px] resize-none border-0 shadow-none focus-visible:ring-0" value={input} maxLength={12000} disabled={busy || saving || restoring || awaitingUser || disabled || !sessionId}
                        onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (input.trim()) void send({ message: input.trim() }); } }} />
                    <div className="flex items-center justify-between"><span className="pl-3 text-xs text-muted-foreground">Shift + Enter for a new line</span><Button type="submit" size="icon" aria-label="Send message" disabled={!input.trim() || !sessionId || busy || saving || restoring || disabled || authLoading || !user || awaitingUser}><ArrowUp className="h-4 w-4" /></Button></div>
                </form>
            </section>
            {session?.proposal && <section className="min-w-0"><WorkflowPreview proposal={session.proposal} onSave={() => void save()} disabled={disabled || busy || !!session.can_continue || (!!workflowId && session.workflow_id !== workflowId)} saving={saving} editing={!!workflowId} /><p className="mt-3 text-xs text-muted-foreground">Click a node to read its prompt.</p></section>}
        </div>
    </div>;
}
