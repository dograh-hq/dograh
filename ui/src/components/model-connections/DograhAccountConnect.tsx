"use client";

import { PageSection } from "@/components/layout/PageLayout";
import { useAuth } from "@/lib/auth";

import { EmailDograhAccountConnect } from "./EmailDograhAccountConnect";

type ConnectProps = {
    onConnected: () => Promise<void> | void;
    // True while email verification or account confirmation is in progress.
    onPendingChange?: (pending: boolean) => void;
};

/** The Models page section for connecting (or reconnecting) a Dograh account. */
export function DograhAccountConnect({ onConnected, reconnect = false }: { onConnected: ConnectProps["onConnected"]; reconnect?: boolean }) {
    return <PageSection title="Dograh-managed models"
        description={reconnect
            ? "Verify your email to reconnect Dograh models."
            : "Verify your email to connect hosted AI services to this self-hosted installation."}>
        <DograhAccountConnectFlow onConnected={onConnected} />
    </PageSection>;
}

/**
 * Reset email verification when the local user or organization changes.
 * Service keys are collected and stored by the backend.
 */
export function DograhAccountConnectFlow({ onConnected, onPendingChange }: ConnectProps) {
    const { user, loading } = useAuth();
    const organization = user && ('selectedTeam' in user ? user.selectedTeam?.id : 'organizationId' in user ? user.organizationId : '');
    const email = user && ('primaryEmail' in user ? user.primaryEmail : 'email' in user ? user.email : '');
    return <div className="space-y-3">
        {!loading && user && !organization && <p role="status" className="text-sm text-muted-foreground">Select an organization to connect Dograh models.</p>}
        <EmailDograhAccountConnect key={`${user?.id}:${organization}`} initialEmail={email || ''} ready={!loading && Boolean(user && organization)} onConnected={onConnected} onPendingChange={onPendingChange} />
    </div>;
}
