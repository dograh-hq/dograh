'use client';

import { useStackApp, useUser } from '@stackframe/stack';
import { AlertCircle, CheckCircle2, Loader2, Mail, Server, Users, XCircle } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { type ReactNode, Suspense, useCallback, useEffect, useRef, useState } from 'react';

import {
    approveDograhAccountConnectRequest,
    denyDograhAccountConnectRequest,
    getDograhAccountConnectRequest,
} from '@/client/sdk.gen';
import type { AccountConnectRequest } from '@/client/types.gen';
import { BrandLogo } from '@/components/BrandLogo';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

type Decision = 'approved' | 'denied';

const CODE_LENGTH = 8;
// The one-time code in the sign-in email, beside its link.
const EMAIL_CODE_LENGTH = 6;

function returnPath(code: string) {
    return `/connect${code ? `?code=${encodeURIComponent(code)}` : ''}`;
}

function lettersAndDigits(value: string, length: number) {
    return value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, length);
}

/** Codes are eight letters shown as two groups: BCDF-GHJK. */
function formatCode(value: string) {
    const letters = lettersAndDigits(value, CODE_LENGTH);
    return letters.length > 4 ? `${letters.slice(0, 4)}-${letters.slice(4)}` : letters;
}

function Shell({ children }: { children: ReactNode }) {
    return <main className="auth-imprint flex min-h-screen items-center justify-center bg-background p-6 sm:p-10">
        <div className="w-full max-w-md space-y-6 rounded-2xl border border-border/60 bg-card p-6 shadow-lg sm:p-8">
            <BrandLogo className="h-7" />
            {children}
        </div>
    </main>;
}

function Heading({ title, children }: { title: string; children?: ReactNode }) {
    return <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {children && <p className="text-sm leading-6 text-muted-foreground">{children}</p>}
    </div>;
}

/** The end of the flow, or a dead end: what happened and what to do next. */
function Outcome({ icon, tone, title, children, action }: {
    icon: typeof CheckCircle2;
    tone: 'success' | 'neutral' | 'error';
    title: string;
    children: ReactNode;
    action?: ReactNode;
}) {
    const Icon = icon;
    return <div role={tone === 'error' ? 'alert' : 'status'} className="space-y-4">
        <span className={cn('flex size-11 items-center justify-center rounded-full',
            tone === 'success' && 'bg-cta/15 text-cta motion-safe:animate-in motion-safe:zoom-in-50 motion-safe:duration-300',
            tone === 'neutral' && 'bg-muted text-muted-foreground',
            tone === 'error' && 'bg-destructive/10 text-destructive')}>
            <Icon className="size-5" aria-hidden />
        </span>
        <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <div className="space-y-2 text-sm leading-6 text-muted-foreground">{children}</div>
        </div>
        {action}
    </div>;
}

function Waiting({ children }: { children: ReactNode }) {
    return <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden />{children}
    </p>;
}

function CodeEntry() {
    const router = useRouter();
    const [code, setCode] = useState('');
    const complete = code.replace('-', '').length === CODE_LENGTH;
    return <>
        <Heading title="Connect a self-hosted installation">
            Enter the code your Dograh installation shows when you choose to connect a Dograh account.
        </Heading>
        <form className="space-y-4" onSubmit={event => {
            event.preventDefault();
            if (complete) router.replace(returnPath(code));
        }}>
            <div className="space-y-2">
                <Label htmlFor="connect-code">Connection code</Label>
                <Input id="connect-code" required autoFocus autoComplete="off" autoCapitalize="characters" spellCheck={false}
                    inputMode="text" placeholder="XXXX-XXXX"
                    className="h-14 pl-[0.3em] text-center font-mono text-2xl tracking-[0.3em] placeholder:tracking-[0.3em] md:text-2xl"
                    value={code} onChange={event => setCode(formatCode(event.target.value))} />
            </div>
            <Button type="submit" className="w-full" disabled={!complete}>Continue</Button>
        </form>
    </>;
}

function SignIn({ code }: { code: string }) {
    const app = useStackApp();
    const [email, setEmail] = useState('');
    // Set once the email is sent; pairs with the code in that email.
    const [nonce, setNonce] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const returnTo = encodeURIComponent(returnPath(code));

    async function sendLink(event: React.FormEvent) {
        event.preventDefault();
        setBusy(true);
        setError(null);
        try {
            const result = await app.sendMagicLinkEmail(email.trim(), {
                callbackUrl: `${window.location.origin}/handler/magic-link-callback?after_auth_return_to=${returnTo}`,
            });
            if (result.status === 'error') throw new Error();
            setNonce(result.data.nonce);
        } catch {
            setError('The sign-in email could not be sent. Try again in a minute.');
        } finally {
            setBusy(false);
        }
    }

    if (nonce !== null) return <EmailCode email={email.trim()} nonce={nonce} onBack={() => setNonce(null)} />;

    return <>
        <Heading title="Sign in to connect">
            A self-hosted Dograh installation wants to use Dograh models with your account.
            Sign in, or create a free account with trial credits.
        </Heading>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <form onSubmit={sendLink} className="space-y-4">
            <div className="space-y-2">
                <Label htmlFor="connect-email">Email</Label>
                <Input id="connect-email" type="email" required autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} />
            </div>
            <Button type="submit" className="w-full" disabled={busy}>{busy ? 'Sending…' : 'Email me a sign-in link'}</Button>
        </form>
        <div className="flex items-center gap-3 text-xs text-muted-foreground" aria-hidden>
            <span className="h-px flex-1 bg-border" />or<span className="h-px flex-1 bg-border" />
        </div>
        <Button asChild variant="outline" className="w-full">
            <a href={`/handler/sign-in?after_auth_return_to=${returnTo}`}>Sign in another way</a>
        </Button>
    </>;
}

/**
 * Signing in with the code from the email instead of its link, for when the
 * link would open in another browser or on another device. Once signed in, the
 * page moves on to the approval.
 */
function EmailCode({ email, nonce, onBack }: { email: string; nonce: string; onBack: () => void }) {
    const app = useStackApp();
    const [value, setValue] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const complete = value.length === EMAIL_CODE_LENGTH;

    async function signIn(otp: string) {
        if (busy) return;
        setBusy(true);
        setError(null);
        let result: Awaited<ReturnType<typeof app.signInWithMagicLink>> | null = null;
        try {
            result = await app.signInWithMagicLink(otp + nonce, { noRedirect: true });
        } catch {
            result = null;
        }
        setBusy(false);
        if (result?.status !== 'ok') {
            setError("That code didn't work. Check it against the email, or open the link instead.");
            setValue('');
        }
    }

    return <div className="space-y-6">
        <div className="space-y-4">
            <span className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <Mail className="size-5" aria-hidden />
            </span>
            <div className="space-y-2">
                <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
                <p role="status" className="text-sm leading-6 text-muted-foreground">
                    We sent a sign-in link to <span className="font-medium text-foreground">{email}</span>.
                    Open the link to come back here, or enter the code from the email.
                </p>
            </div>
        </div>
        <form className="space-y-4" onSubmit={event => { event.preventDefault(); if (complete) void signIn(value); }}>
            <div className="space-y-2">
                <Label htmlFor="email-code">Code from the email</Label>
                <Input id="email-code" autoFocus autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false}
                    inputMode="text" placeholder="XXXXXX" disabled={busy}
                    className="h-12 pl-[0.3em] text-center font-mono text-xl tracking-[0.3em] placeholder:tracking-[0.3em] md:text-xl"
                    value={value} onChange={event => {
                        const next = lettersAndDigits(event.target.value, EMAIL_CODE_LENGTH);
                        setValue(next);
                        setError(null);
                        // A pasted or fully typed code signs in without another click.
                        if (next.length === EMAIL_CODE_LENGTH) void signIn(next);
                    }} />
            </div>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={!complete || busy}>
                {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}{busy ? 'Signing in…' : 'Sign in'}
            </Button>
        </form>
        <Button variant="outline" className="w-full" onClick={onBack}>Use a different email</Button>
    </div>;
}

function minutesLeft(deadline: number, now: number) {
    return Math.max(0, Math.ceil((deadline - now) / 60_000));
}

/** Counts down to the code's expiry, re-reading the clock every few seconds. */
function useDeadline(expiresIn: number | undefined) {
    const [deadline, setDeadline] = useState<number | null>(null);
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (expiresIn === undefined) return;
        const loadedAt = Date.now();
        setNow(loadedAt);
        setDeadline(loadedAt + expiresIn * 1000);
        const timer = setInterval(() => setNow(Date.now()), 5000);
        return () => clearInterval(timer);
    }, [expiresIn]);
    return deadline === null ? null : { expired: now >= deadline, minutes: minutesLeft(deadline, now) };
}

function Approval({ code }: { code: string }) {
    const user = useUser();
    const auth = useAuth();
    const [request, setRequest] = useState<AccountConnectRequest | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<Decision | null>(null);
    const [decision, setDecision] = useState<Decision | null>(null);
    const loadedFor = useRef<string | null>(null);
    const deadline = useDeadline(request?.status === 'pending' ? request.expires_in : undefined);

    const headers = useCallback(async () => ({ Authorization: `Bearer ${await auth.getAccessToken()}` }), [auth]);

    useEffect(() => {
        if (auth.loading || !auth.isAuthenticated || loadedFor.current === code) return;
        loadedFor.current = code;
        void (async () => {
            const response = await getDograhAccountConnectRequest({ path: { user_code: code }, headers: await headers() });
            if (response.error) setError(detailFromError(response.error, 'This code is not valid, or it has expired.'));
            else setRequest(response.data ?? null);
        })();
    }, [auth.loading, auth.isAuthenticated, code, headers]);

    async function decide(next: Decision) {
        setBusy(next);
        setError(null);
        const call = next === 'approved' ? approveDograhAccountConnectRequest : denyDograhAccountConnectRequest;
        const response = await call({ path: { user_code: code }, headers: await headers() });
        setBusy(null);
        if (response.error) setError(detailFromError(response.error, 'Your decision was not saved. Try again.'));
        else setDecision(next);
    }

    const team = user?.selectedTeam?.displayName ?? 'your team';
    const install = request?.install_name ?? 'The installation';
    const enterAnother = <Button asChild variant="outline" className="w-full"><Link href="/connect">Enter a different code</Link></Button>;

    if (decision === 'approved') return <Outcome icon={CheckCircle2} tone="success" title="Installation connected">
        <p><span className="font-medium text-foreground">{install}</span> can now use Dograh models on {team}.</p>
        <p>Go back to your installation. It finishes setting up within a few seconds, and you can close this tab.</p>
    </Outcome>;
    if (decision === 'denied') return <Outcome icon={XCircle} tone="neutral" title="Request denied">
        <p>{install} was not connected to your account.</p>
    </Outcome>;
    if (error && !request) return <Outcome icon={AlertCircle} tone="error" title="This code didn't work" action={enterAnother}>
        <p>{error}</p>
    </Outcome>;
    if (!request) return <Waiting>Loading the connection request…</Waiting>;
    if (request.status !== 'pending') return <Outcome icon={AlertCircle} tone="neutral"
        title={`This request was already ${request.status === 'denied' ? 'denied' : 'approved'}`} action={enterAnother}>
        <p>To connect again, start from your installation and use the new code it shows.</p>
    </Outcome>;
    if (deadline?.expired) return <Outcome icon={AlertCircle} tone="neutral" title="This code has expired" action={enterAnother}>
        <p>Start again from your installation and use the new code it shows.</p>
    </Outcome>;

    return <>
        <Heading title="Connect a self-hosted installation">
            A self-hosted Dograh installation wants to use Dograh models with your account,{' '}
            <span className="font-medium text-foreground">{user?.primaryEmail}</span>.
        </Heading>

        <div className="rounded-xl border border-border/60 bg-background/40 p-4">
            <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><Server className="size-4" aria-hidden /></span>
                <div className="min-w-0">
                    <p className="truncate font-medium">{request.install_name}</p>
                    <p className="truncate text-sm text-muted-foreground">{request.install_url ?? 'Self-hosted installation'}</p>
                </div>
            </div>
            {/* The connection being approved: installation above, account below. */}
            <div className="ml-[1.125rem] h-5 border-l-2 border-dashed border-muted-foreground/40" aria-hidden />
            <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted"><Users className="size-4" aria-hidden /></span>
                <div className="min-w-0">
                    <p className="truncate font-medium">{team}</p>
                    <p className="text-sm text-muted-foreground">Your Dograh team. Its credits pay for this installation&apos;s usage.</p>
                </div>
            </div>
        </div>

        <div className="space-y-2">
            <p className="text-sm">Check that your installation shows this code:</p>
            <p className="rounded-xl border border-cta/40 bg-cta/5 py-4 pl-[0.3em] text-center font-mono text-3xl font-medium tracking-[0.3em]">
                {request.user_code}
            </p>
            {deadline && <p className="text-xs text-muted-foreground">
                {deadline.minutes <= 1 ? 'The code expires in less than a minute.' : `The code expires in ${deadline.minutes} minutes.`}
            </p>}
        </div>

        <p className="text-sm leading-6 text-muted-foreground">
            Approving gives the installation a key for Dograh models only, not access to your account.
            It appears under Service Keys on your <Link href="/api-keys" className="text-foreground underline underline-offset-4">API Keys</Link> page,
            where you can archive it to disconnect the installation.
        </p>

        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="space-y-3">
            <Button className="w-full" onClick={() => void decide('approved')} disabled={busy !== null}>
                {busy === 'approved' && <Loader2 className="size-4 animate-spin" aria-hidden />}Approve connection
            </Button>
            <Button variant="outline" className="w-full" onClick={() => void decide('denied')} disabled={busy !== null}>
                {busy === 'denied' && <Loader2 className="size-4 animate-spin" aria-hidden />}Deny
            </Button>
        </div>
        <p className="text-center text-sm text-muted-foreground">
            Not {user?.primaryEmail}? <SwitchAccount code={code} />
        </p>
    </>;
}

function SwitchAccount({ code }: { code: string }) {
    const user = useUser();
    if (!user) return null;
    return <Button variant="link" className="h-auto p-0 text-sm text-foreground underline underline-offset-4"
        onClick={() => void user.signOut({ redirectUrl: returnPath(code) })}>Use a different account</Button>;
}

function ConnectFlow() {
    const user = useUser();
    const code = (useSearchParams().get('code') ?? '').trim();
    const verified = Boolean(user?.primaryEmail && user.primaryEmailVerified);

    if (!code) return <CodeEntry />;
    if (!user) return <SignIn code={code} />;
    if (!verified) return <Outcome icon={Mail} tone="neutral" title="Verify your email to continue"
        action={<SwitchAccount code={code} />}>
        <p>{user.primaryEmail ?? 'This account'} is not verified yet. Verify it in <Link href="/handler/account-settings" className="text-foreground underline underline-offset-4">account settings</Link>, then come back to this page.</p>
    </Outcome>;
    if (!user.selectedTeam) return <Outcome icon={Users} tone="neutral" title="Choose a team to continue">
        <p>The installation uses a team&apos;s credits. Select or create a team in <Link href="/handler/account-settings" className="text-foreground underline underline-offset-4">account settings</Link>, then come back to this page.</p>
    </Outcome>;
    return <Approval code={code} />;
}

export default function ConnectPage() {
    const auth = useAuth();
    return <Shell>
        {auth.loading ? <Waiting>Loading…</Waiting> : auth.provider === 'stack'
            ? <Suspense fallback={<Waiting>Loading…</Waiting>}><ConnectFlow /></Suspense>
            : <Outcome icon={AlertCircle} tone="neutral" title="Approve on Dograh"
                action={<Button asChild className="w-full"><a href="https://app.dograh.com/connect">Open Dograh</a></Button>}>
                <p>Connection requests are approved on app.dograh.com. Open the link your installation showed you, or enter its code there.</p>
            </Outcome>}
    </Shell>;
}
