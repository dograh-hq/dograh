'use client';

import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { getTelephonyConfigWarningsApiV1OrganizationsTelephonyConfigWarningsGet } from '@/client/sdk.gen';
import { useAuth } from '@/lib/auth';

interface TelephonyConfigWarningsContextType {
    telnyxMissingWebhookPublicKeyCount: number;
    vonageMissingSignatureSecretCount: number;
    refresh: () => Promise<void>;
    loading: boolean;
}

const TelephonyConfigWarningsContext = createContext<TelephonyConfigWarningsContextType>({
    telnyxMissingWebhookPublicKeyCount: 0,
    vonageMissingSignatureSecretCount: 0,
    refresh: async () => { },
    loading: false,
});

// One-shot fetch on first authenticated load. The state is cheap to compute
// server-side (one indexed JSONB query) but rendering it in both the page
// banner and the nav badge means we don't want to refetch on every route
// change. Page-level callers invalidate via refresh() after a save.
export function TelephonyConfigWarningsProvider({ children }: { children: ReactNode }) {
    const auth = useAuth();
    const [telnyxCount, setTelnyxCount] = useState(0);
    const [vonageCount, setVonageCount] = useState(0);
    const [loading, setLoading] = useState(false);
    const hasFetched = useRef(false);

    const doFetch = useCallback(async () => {
        setLoading(true);
        try {
            const res = await getTelephonyConfigWarningsApiV1OrganizationsTelephonyConfigWarningsGet();
            setTelnyxCount(res.data?.telnyx_missing_webhook_public_key_count ?? 0);
            setVonageCount(res.data?.vonage_missing_signature_secret_count ?? 0);
        } catch {
            setTelnyxCount(0);
            setVonageCount(0);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (auth.loading || !auth.isAuthenticated || hasFetched.current) return;
        hasFetched.current = true;
        doFetch();
    }, [auth.loading, auth.isAuthenticated, doFetch]);

    const refresh = useCallback(async () => {
        if (!auth.isAuthenticated) return;
        await doFetch();
    }, [auth.isAuthenticated, doFetch]);

    return (
        <TelephonyConfigWarningsContext.Provider
            value={{
                telnyxMissingWebhookPublicKeyCount: telnyxCount,
                vonageMissingSignatureSecretCount: vonageCount,
                refresh,
                loading,
            }}
        >
            {children}
        </TelephonyConfigWarningsContext.Provider>
    );
}

export function useTelephonyConfigWarnings() {
    return useContext(TelephonyConfigWarningsContext);
}
