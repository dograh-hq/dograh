"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { confirmDograhEmailConnect, startDograhEmailConnect, verifyDograhEmailConnect } from "@/client/sdk.gen";
import type { AccountConnectPoll, EmailConnectStart } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";

type Props = {
    initialEmail: string;
    ready: boolean;
    onConnected: () => Promise<void> | void;
    onPendingChange?: (pending: boolean) => void;
};

export function EmailDograhAccountConnect({ initialEmail, ready, onConnected, onPendingChange }: Props) {
    const [email, setEmail] = useState(initialEmail);
    const [code, setCode] = useState("");
    const [step, setStep] = useState<"email" | "code" | "connected">("email");
    const [request, setRequest] = useState<EmailConnectStart | null>(null);
    const [verifiedTeam, setVerifiedTeam] = useState<string | null>(null);
    const [result, setResult] = useState<AccountConnectPoll | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [resendAt, setResendAt] = useState(0);
    const [expiresAt, setExpiresAt] = useState(0);
    const [now, setNow] = useState(Date.now());
    const generation = useRef(0);
    const abort = useRef<AbortController | null>(null);
    const pendingCallback = useRef(onPendingChange);
    pendingCallback.current = onPendingChange;
    const pending = busy || step === "code";

    useEffect(() => {
        pendingCallback.current?.(pending);
    }, [pending]);
    useEffect(() => () => { generation.current++; abort.current?.abort(); }, []);
    useEffect(() => {
        if (!request) return;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [request]);
    useEffect(() => {
        if (!expiresAt || now < expiresAt || step === "connected") return;
        generation.current++;
        abort.current?.abort();
        setBusy(false);
        setRequest(null);
        setVerifiedTeam(null);
        setExpiresAt(0);
        setStep("email");
        setError("Your verification code has expired. Request a new code.");
    }, [expiresAt, now, step]);

    const reset = () => {
        generation.current++;
        abort.current?.abort();
        setBusy(false);
        setRequest(null);
        setVerifiedTeam(null);
        setCode("");
        setExpiresAt(0);
        setError(null);
        setStep("email");
    };

    const run = async (action: (id: number, signal: AbortSignal) => Promise<void>) => {
        if (busy || !ready) return;
        const id = ++generation.current;
        abort.current = new AbortController();
        setBusy(true);
        setError(null);
        try { await action(id, abort.current.signal); }
        catch (cause) {
            if (id === generation.current) setError(cause instanceof Error ? cause.message : "Unable to connect. Please retry.");
        } finally {
            if (id === generation.current) setBusy(false);
        }
    };

    const send = () => run(async (id, signal) => {
        const response = await startDograhEmailConnect({ body: { email: email.trim(), install_url: window.location.origin }, signal });
        if (id !== generation.current) return;
        if (response.error || !response.data) throw new Error(detailFromError(response.error, "Unable to send a verification code. Please try again."));
        setRequest(response.data);
        setVerifiedTeam(null);
        setCode("");
        setResendAt(Date.now() + 60000);
        setExpiresAt(Date.now() + response.data.expires_in * 1000);
        setNow(Date.now());
        setStep("code");
    });

    const verify = () => run(async (id, signal) => {
        if (!request) return;
        let teamId = verifiedTeam;
        if (!teamId) {
            const verification = await verifyDograhEmailConnect({ body: { device_code: request.device_code, code: code.trim() }, signal });
            if (id !== generation.current) return;
            if (verification.error || !verification.data) throw new Error(detailFromError(verification.error, "Unable to verify the code."));
            teamId = verification.data.selected_team?.id ?? null;
            if (!teamId) throw new Error("Unable to find your selected Dograh account. Please try again.");
            setVerifiedTeam(teamId);
        }
        const response = await confirmDograhEmailConnect({ body: { device_code: request.device_code, team_id: teamId }, signal });
        if (id !== generation.current) return;
        if (response.error || !response.data) throw new Error(detailFromError(response.error, "Unable to finish connecting. Please try again."));
        if (response.data.status !== "connected") throw new Error("This connection request has ended. Request a new code.");
        setResult(response.data);
        setRequest(null);
        setStep("connected");
        await onConnected();
    });

    if (step === "connected") return <p role="status" className="text-sm">Connected to {result?.team_name || "your Dograh account"}. Dograh models will use this account&apos;s credits.</p>;

    return <div className="space-y-4 rounded-lg border p-4">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {step === "email" && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void send(); }}>
            <div className="space-y-2"><Label htmlFor="dograh-service-email">Email address</Label><Input id="dograh-service-email" type="email" required maxLength={254} autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} disabled={busy || !ready} /></div>
            <Button type="submit" disabled={busy || !ready}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}Send verification code</Button>
        </form>}
        {step === "code" && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void verify(); }}>
            <p className="text-sm">{verifiedTeam
                ? busy ? "Email verified. Connecting your Dograh account…" : "Your email is verified. Retry to finish connecting."
                : <>Enter the 6-digit code we sent to {email.trim()}.</>}</p>
            {!verifiedTeam && <div className="space-y-2"><Label htmlFor="dograh-email-code">Verification code</Label><Input id="dograh-email-code" autoComplete="one-time-code" inputMode="numeric" autoFocus maxLength={6} pattern="[0-9]{6}" required value={code} onChange={event => setCode(event.target.value)} disabled={busy} className="font-mono tracking-widest" /></div>}
            <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || code.length !== 6}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}{verifiedTeam ? busy ? "Connecting…" : "Retry connection" : "Verify email"}</Button>
                {!verifiedTeam && <Button type="button" variant="outline" disabled={busy || now < resendAt} onClick={() => void send()}>Resend code{now < resendAt ? ` (${Math.ceil((resendAt - now) / 1000)}s)` : ""}</Button>}
                <Button type="button" variant="ghost" onClick={reset}>Change email</Button></div>
        </form>}
        {step !== "email" && <Button variant="ghost" onClick={reset}>Cancel</Button>}
    </div>;
}
