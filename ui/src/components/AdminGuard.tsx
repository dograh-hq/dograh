'use client';

import { useRouter } from 'next/navigation';
import { type ReactNode, useEffect } from 'react';

import SpinLoader from '@/components/SpinLoader';
import { useIsAdmin } from '@/hooks/useIsAdmin';

/**
 * Wraps admin-only configuration surfaces (models/providers, API keys,
 * telephony, platform settings). Non-admin users are redirected to the
 * home page instead of seeing the page content.
 *
 * Intended to be used from a route `layout.tsx` so upstream page components
 * stay untouched (keeps the fork mergeable).
 */
export function AdminGuard({ children }: { children: ReactNode }) {
    const { isAdmin, isLoaded } = useIsAdmin();
    const router = useRouter();

    useEffect(() => {
        if (isLoaded && !isAdmin) {
            router.replace('/home');
        }
    }, [isLoaded, isAdmin, router]);

    if (!isLoaded || !isAdmin) {
        return <SpinLoader />;
    }

    return <>{children}</>;
}
