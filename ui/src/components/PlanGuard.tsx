'use client';

import { useRouter } from 'next/navigation';
import { type ReactNode, useEffect } from 'react';

import SpinLoader from '@/components/SpinLoader';
import { useFeature } from '@/hooks/useFeature';

/**
 * Wraps a surface that requires a paid plan feature (e.g. API keys → "api").
 * Orgs whose plan doesn't include the feature are redirected to /credits
 * (where they can see their plan + upgrade) instead of seeing the page.
 *
 * Used from a route `layout.tsx` so page components stay untouched.
 */
export function PlanGuard({
    feature,
    children,
}: {
    feature: 'api' | 'mcp';
    children: ReactNode;
}) {
    const { enabled, loaded } = useFeature(feature);
    const router = useRouter();

    useEffect(() => {
        if (loaded && !enabled) {
            router.replace('/credits');
        }
    }, [loaded, enabled, router]);

    if (!loaded || !enabled) {
        return <SpinLoader />;
    }

    return <>{children}</>;
}
