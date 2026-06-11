'use client';

import { useRouter } from 'next/navigation';
import { type ReactNode, useEffect } from 'react';

import SpinLoader from '@/components/SpinLoader';
import { useUserConfig } from '@/context/UserConfigContext';

/**
 * Wraps superuser-only surfaces (admin Clients view). Stricter than
 * AdminGuard: org admins are NOT enough — only UserModel.is_superuser
 * (the deployment owner, promoted via ADMIN_EMAILS) passes. Everyone
 * else is redirected to the overview page.
 */
export function SuperuserGuard({ children }: { children: ReactNode }) {
    const { isSuperuser, superuserLoaded } = useUserConfig();
    const router = useRouter();

    useEffect(() => {
        if (superuserLoaded && !isSuperuser) {
            router.replace('/overview');
        }
    }, [superuserLoaded, isSuperuser, router]);

    if (!superuserLoaded || !isSuperuser) {
        return <SpinLoader />;
    }

    return <>{children}</>;
}
