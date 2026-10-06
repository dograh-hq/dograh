'use client';

import { ArrowUp, Loader2, Plus } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { getBuilderSessionApiV1WorkflowBuilderSessionIdGet, saveBuilderSessionApiV1WorkflowBuilderSessionIdSavePost, sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost } from '@/client/sdk.gen';
import type { BuilderSession, BuilderTurnRequest } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { PlanApprovalCard } from '@/components/workflow-builder/PlanApprovalCard';
import { QuestionCards } from '@/components/workflow-builder/QuestionCards';
import { WorkflowPreview } from '@/components/workflow-builder/WorkflowPreview';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export default function CreateWorkflowPage() {
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
        setSession(null); setError(null); setRestoring(true);
        const id = new URL(window.location.href).searchParams.get('session');
        if (!id) { setSessionId(crypto.randomUUID()); setRestoring(false); }
        else {
            setSessionId(id);
            void (async () => {
                try {
                    const token = await getAccessToken();
                    const result = await getBuilderSessionApiV1WorkflowBuilderSessionIdGet({ path: { session_id: id }, signal: controller.signal, headers: { Authorization: `Bearer ${token}` } });
                    if (result.error) throw new Error(detailFromError(result.error, 'Could not restore this conversation.'));
                    if (!controller.signal.aborted && result.data) setSession(result.data);
                } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not restore this conversation.'); }
                finally { if (!controller.signal.aborted) setRestoring(false); }
            })();
        }
        return () => { controller.abort(); abort.current?.abort(); };
    // The authenticated user and organization identify this conversation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [authLoading, user?.id, organization]);

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
        if (!user || busy || restoring) return;
        const controller = new AbortController(); abort.current = controller;
        setBusy(true); setError(null); setStatus('Thinking about your agent…'); setOptimisticMessage(content.message ?? '');
        const url = new URL(window.location.href); url.searchParams.set('session', sessionId);
        window.history.replaceState(window.history.state, '', url);
        let completed = false;
        try {
            const token = await getAccessToken();
            const result = await sendBuilderTurnApiV1WorkflowBuilderSessionIdTurnPost({ path: { session_id: sessionId },
                body: { request_id: crypto.randomUUID(), checkpoint: session?.checkpoint ?? null, ...content },
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
        if (!session?.checkpoint || busy || saving) return;
        setSaving(true); setError(null);
        try {
            const token = await getAccessToken();
            const result = await saveBuilderSessionApiV1WorkflowBuilderSessionIdSavePost({ path: { session_id: sessionId }, body: { checkpoint: session.checkpoint }, headers: { Authorization: `Bearer ${token}` } });
            if (result.error) throw new Error(detailFromError(result.error, 'Could not save the draft.'));
            if (!result.data) throw new Error('The server did not return a workflow.');
            router.push(`/workflow/${result.data.workflow_id}`);
        } catch (err) { setError(err instanceof Error ? err.message : 'Could not save the draft.'); }
        finally { setSaving(false); }
    };
    return <main className="mx-auto flex min-h-[calc(100vh-80px)] max-w-[1500px] flex-col px-4 py-6 md:px-8">
        <header className="mb-6 flex items-center justify-between gap-3"><div><h1 className="text-xl font-semibold tracking-tight">Create an agent</h1><p className="mt-1 text-sm text-muted-foreground">Describe what you need. Build and refine it together.</p></div>{!isStarting && <Button variant="outline" size="sm" onClick={newConversation} disabled={busy || saving || restoring}><Plus className="mr-2 h-4 w-4" />New conversation</Button>}</header>
        <div className={`grid flex-1 gap-6 ${session?.proposal ? 'lg:grid-cols-[minmax(340px,0.9fr)_minmax(0,1.1fr)]' : `mx-auto w-full max-w-3xl ${isStarting ? 'content-center' : ''}`}`}>
            <section className={`flex min-w-0 flex-col ${isStarting ? '' : 'min-h-[650px] rounded-2xl border bg-muted/20'}`}>
                <div className={isStarting ? 'space-y-5 px-4 pb-6 text-center' : 'max-h-[65vh] min-h-[380px] flex-1 space-y-5 overflow-y-auto p-5 md:p-7'} aria-live="polite">
                    {restoring ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Restoring conversation…</div> : isStarting && <div>
                        <h2 className="text-2xl font-medium tracking-tight">What should your agent do?</h2>
                        <p className="mt-3 text-sm leading-6 text-muted-foreground">Describe who it should speak with and what it needs to do.</p>
                    </div>}
                    {session?.messages?.map(message => <div key={message.id} className={message.role === 'user' ? 'ml-8 rounded-2xl bg-muted px-4 py-3' : 'pr-5'}><div className="mb-1.5 text-xs font-medium text-muted-foreground">{message.role === 'user' ? 'You' : 'Dograh'}</div><div className="whitespace-pre-wrap text-sm leading-6">{message.content}</div></div>)}
                    {optimisticMessage && <div className="ml-8 rounded-2xl bg-muted px-4 py-3 text-sm">{optimisticMessage}</div>}
                    {!!pendingQuestions.length && <QuestionCards key={pendingQuestions.map(item => item.id).join(':')} pending={pendingQuestions} disabled={busy} onSubmit={answers => void send({ answers })} />}
                    {pendingApproval?.brief && pendingApproval.brief_revision != null && <PlanApprovalCard key={pendingApproval.id} interruptId={pendingApproval.id} revision={pendingApproval.brief_revision} brief={pendingApproval.brief} disabled={busy || restoring} onSubmit={approval => void send({ approval })} />}
                    {busy && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{status}</div>}
                    {error && <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{error}
                        {error.includes('/model-configurations') && <Link href="/model-configurations" className="mt-2 block font-medium underline">Open Model Configurations</Link>}
                    </div>}
                    {!busy && !awaitingUser && session?.can_continue && <Button variant="outline" onClick={() => void send()}>Resume building</Button>}
                    <div ref={bottom} />
                </div>
                <form className={`rounded-xl border bg-background p-3 ${isStarting ? 'shadow-sm' : 'm-4'}`} onSubmit={event => { event.preventDefault(); if (input.trim()) void send({ message: input.trim() }); }}>
                    <Textarea ref={composer} aria-label="Describe your agent or request a change" placeholder={pendingApproval ? 'Review the plan above to continue…' : pendingQuestions.length ? 'Answer the questions above to continue…' : session?.proposal ? 'Ask for a change to your agent…' : 'Build me an agent that…'} className="min-h-[80px] resize-none border-0 shadow-none focus-visible:ring-0" value={input} maxLength={12000} disabled={busy || restoring || awaitingUser}
                        onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (input.trim()) void send({ message: input.trim() }); } }} />
                    <div className="flex items-center justify-between"><span className="pl-3 text-xs text-muted-foreground">Shift + Enter for a new line</span><Button type="submit" size="icon" aria-label="Send message" disabled={!input.trim() || busy || restoring || authLoading || !user || awaitingUser}><ArrowUp className="h-4 w-4" /></Button></div>
                </form>
            </section>
            {session?.proposal && <section className="min-w-0"><WorkflowPreview proposal={session.proposal} onSave={() => void save()} disabled={busy || !!session.can_continue} saving={saving} /><p className="mt-3 text-xs text-muted-foreground">Click a node to read its prompt.</p></section>}
        </div>
    </main>;
}
