'use client';

import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { getDograhAccountStatus } from '@/client/sdk.gen';
import type { DograhAccountStatus } from '@/client/types.gen';
import { useAuth } from '@/lib/auth';

// connected / missing / invalid come from the server. "unknown" means the
// status could not be read: callers must not block on it (the server still
// enforces), and must not offer to connect either.
export type DograhAccountState = DograhAccountStatus['status'] | 'loading' | 'unknown';

interface DograhAccountContextValue {
    status: DograhAccountState;
    // Read the status for a caller that just mounted. Pages that never ask cost nothing.
    request: () => void;
    // Read it again, e.g. right after this installation connected an account.
    refresh: () => Promise<void>;
}

const DograhAccountContext = createContext<DograhAccountContextValue | null>(null);

/**
 * Whether the selected organization has a Dograh key that Dograh accepts:
 * the key the agent builder and Dograh's other features run on. Shared so a
 * connection made in one place (the setup step, the builder, the Models page)
 * updates the others.
 *
 * Each caller that mounts reads the status again, because a key can be added
 * or replaced anywhere (a provider connection, another tab). The last status
 * stays visible while it is re-read, except one that blocks the caller
 * (missing or invalid) and may be stale: that shows as loading instead.
 */
export function DograhAccountProvider({ children }: { children: ReactNode }) {
    const { user, loading: authLoading } = useAuth();
    const organization = user ? ('selectedTeam' in user ? user.selectedTeam?.id : 'organizationId' in user ? user.organizationId : '') : '';
    const scope = user ? `${user.id}:${organization}` : null;
    const [status, setStatus] = useState<DograhAccountState>('loading');
    const [requests, setRequests] = useState(0);
    // Whose status is showing; another user's or organization's is never kept.
    const shownScope = useRef<string | null>(null);
    // Only the latest read may set the status; an organization switch or a
    // refresh supersedes any read still in flight.
    const latest = useRef(0);

    const read = useCallback(async (whileReading: (previous: DograhAccountState) => DograhAccountState) => {
        const id = ++latest.current;
        setStatus(whileReading);
        const response = await getDograhAccountStatus().catch(() => null);
        if (id !== latest.current) return;
        setStatus(response?.data?.status ?? 'unknown');
    }, []);

    useEffect(() => {
        if (!requests || authLoading || !scope) return;
        const sameScope = shownScope.current === scope;
        shownScope.current = scope;
        void read(previous => sameScope && previous === 'connected' ? previous : 'loading');
    }, [requests, authLoading, scope, read]);

    const request = useCallback(() => setRequests(count => count + 1), []);
    // The caller just changed the key and shows its own result meanwhile.
    const refresh = useCallback(() => read(previous => previous), [read]);

    const value = useMemo(() => ({ status, request, refresh }), [status, request, refresh]);
    return <DograhAccountContext.Provider value={value}>{children}</DograhAccountContext.Provider>;
}

export function useDograhAccountContext(): DograhAccountContextValue {
    const context = useContext(DograhAccountContext);
    if (!context) throw new Error('useDograhAccountContext must be used within a DograhAccountProvider');
    return context;
}

/** The organization's Dograh account status, read when the caller mounts enabled. */
export function useDograhAccount(enabled = true): Omit<DograhAccountContextValue, 'request'> {
    const { status, request, refresh } = useDograhAccountContext();
    useEffect(() => { if (enabled) request(); }, [enabled, request]);
    return { status, refresh };
}
