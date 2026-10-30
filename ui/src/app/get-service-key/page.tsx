'use client';

import { useStackApp, useUser } from '@stackframe/stack';
import Link from 'next/link';
import { useRef, useState } from 'react';

import { createServiceKeyApiV1UserServiceKeysPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { copyTextToClipboard } from '@/lib/clipboard';

function VerifiedServiceKeyForm() {
    const app = useStackApp();
    const user = useUser();
    const auth = useAuth();
    const [email, setEmail] = useState('');
    const [sent, setSent] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [key, setKey] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const pending = useRef(false);
    const verified = Boolean(user?.primaryEmail && user.primaryEmailVerified);

    async function sendEmail(event: React.FormEvent) {
        event.preventDefault();
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        setError(null);
        try {
            const result = await app.sendMagicLinkEmail(email.trim(), {
                callbackUrl: `${window.location.origin}/handler/magic-link-callback?after_auth_return_to=%2Fget-service-key`,
            });
            if (result.status === 'error') throw new Error('Unable to send the email. Please try again shortly.');
            setSent(true);
        } catch {
            setError('Unable to send the email. Please try again shortly.');
        } finally {
            pending.current = false;
            setBusy(false);
        }
    }

    async function createKey() {
        if (pending.current || key || auth.loading || !auth.isAuthenticated || !verified) return;
        pending.current = true;
        setBusy(true);
        setError(null);
        try {
            const token = await auth.getAccessToken();
            const result = await createServiceKeyApiV1UserServiceKeysPost({
                body: { name: 'OSS Dograh provider' },
                headers: { Authorization: `Bearer ${token}` },
            });
            if (result.error) throw new Error(detailFromError(result.error, 'Unable to create a service key.'));
            if (!result.data?.service_key) throw new Error('Unable to create a service key.');
            setKey(result.data.service_key);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : 'Unable to create a service key.');
        } finally {
            pending.current = false;
            setBusy(false);
        }
    }

    return <div className="space-y-6">
        <div className="space-y-2">
            <h1 className="text-2xl font-semibold">Get a Dograh service key</h1>
            <p className="text-sm text-muted-foreground">Use Dograh models in your self-hosted installation. Verify your email to get an account-backed service key.</p>
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {key ? <div className="space-y-3 ph-no-capture" data-private>
            <Label htmlFor="service-key">Your service key</Label>
            <Input id="service-key" readOnly value={key} autoComplete="off" className="font-mono" />
            <Button onClick={async () => {
                try { await copyTextToClipboard(key); setCopied(true); }
                catch { setError('Copy the key from the field above.'); }
            }}>{copied ? 'Copied' : 'Copy key'}</Button>
            <p className="text-sm">Save this key now; it is only shown once. In your OSS installation, add a <strong>Dograh</strong> provider, paste the key, and select that provider in your model configuration.</p>
            <p className="text-sm text-muted-foreground">Keys share your team’s billing balance. Creating another key does not add trial credits.</p>
        </div> : verified ? <div className="space-y-3">
            <p className="text-sm">Signed in as {user?.primaryEmail}.</p>
            {user?.selectedTeam ? <>
                <p className="text-sm">Billing account: {user.selectedTeam.displayName}</p>
                <Button onClick={createKey} disabled={busy || auth.loading || !auth.isAuthenticated}>{busy ? 'Creating key…' : 'Create service key'}</Button>
            </> : <p className="text-sm">Select or create a team in <Link href="/handler/account-settings" className="underline">your Dograh account</Link>, then return here.</p>}
            <p className="text-sm text-muted-foreground">Eligible verified accounts receive a one-time trial grant. Existing balances are retained.</p>
        </div> : user ? <p className="text-sm">
            Verify your email in <Link href="/handler/account-settings" className="underline">your Dograh account settings</Link>, then return here to create a service key.
        </p> : sent ? <div role="status" className="space-y-3">
            <p>Check your email for a sign-in link. Follow it to verify your email and create your service key.</p>
            <Button variant="outline" onClick={() => setSent(false)}>Send another link</Button>
        </div> : <form onSubmit={sendEmail} className="space-y-3">
            <Label htmlFor="key-email">Email</Label>
            <Input id="key-email" type="email" required autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} />
            <Button type="submit" disabled={busy}>{busy ? 'Sending…' : 'Email me a sign-in link'}</Button>
        </form>}
    </div>;
}

export default function GetServiceKeyPage() {
    const auth = useAuth();
    return <main className="mx-auto max-w-lg px-6 py-16">
        {auth.loading ? <p>Loading…</p> : auth.provider === 'stack' ? <VerifiedServiceKeyForm /> : <p>
            Get your service key at <a className="underline" href="https://app.dograh.com/get-service-key">Dograh</a>, then add a Dograh provider in this installation.
        </p>}
    </main>;
}
