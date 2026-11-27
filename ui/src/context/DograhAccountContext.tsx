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
    // Fetch the status once; later calls reuse it. Pages that never ask cost nothing.
    request: () => void;
    // Fetch again, e.g. right after this installation connected an account.
    refresh: () => Promise<void>;
}

const DograhAccountContext = createContext<DograhAccountContextValue | null>(null);

/**
 * Whether the selected organization has a Dograh key that Dograh accepts:
 * the key the agent builder and Dograh's other features run on. Shared so a
 * connection made in one place (the setup step, the builder, the Models page)
 * updates the others.
 */
export function DograhAccountProvider({ children }: { children: ReactNode }) {
    const { user, loading: authLoading } = useAuth();
    const organization = user ? ('selectedTeam' in user ? user.selectedTeam?.id : 'organizationId' in user ? user.organizationId : '') : '';
    const [status, setStatus] = useState<DograhAccountState>('loading');
    const [requested, setRequested] = useState(false);
    // Only the latest fetch may set the status; an organization switch or a
    // refresh supersedes any fetch still in flight.
    const latest = useRef(0);

    const fetchStatus = useCallback(async () => {
        const id = ++latest.current;
        setStatus('loading');
        const response = await getDograhAccountStatus().catch(() => null);
        if (id !== latest.current) return;
        setStatus(response?.data?.status ?? 'unknown');
    }, []);

    useEffect(() => {
        if (!requested || authLoading || !user) return;
        void fetchStatus();
    // The organization decides which key applies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [requested, authLoading, user?.id, organization, fetchStatus]);

    const request = useCallback(() => setRequested(true), []);
    const refresh = useCallback(async () => {
        setRequested(true);
        await fetchStatus();
    }, [fetchStatus]);

    const value = useMemo(() => ({ status, request, refresh }), [status, request, refresh]);
    return <DograhAccountContext.Provider value={value}>{children}</DograhAccountContext.Provider>;
}

export function useDograhAccountContext(): DograhAccountContextValue {
    const context = useContext(DograhAccountContext);
    if (!context) throw new Error('useDograhAccountContext must be used within a DograhAccountProvider');
    return context;
}

/** The organization's Dograh account status, fetched once the caller is enabled. */
export function useDograhAccount(enabled = true): Omit<DograhAccountContextValue, 'request'> {
    const { status, request, refresh } = useDograhAccountContext();
    useEffect(() => { if (enabled) request(); }, [enabled, request]);
    return { status, refresh };
}
