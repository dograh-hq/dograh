'use client';

import { useUserConfig } from '@/context/UserConfigContext';
import { CLIENT_MODE } from '@/lib/brand';

/**
 * Role check built on the platform's real permission mechanism:
 *
 * - Stack Auth deployments: the selected team's permissions are fetched via
 *   `listPermissions(selectedTeam)`; org admins carry the `admin` permission
 *   (the same check `getRedirectUrl` in lib/utils.ts already uses).
 * - Local/OSS deployments: upstream grants every user `[{ id: 'admin' }]`.
 *
 * `NEXT_PUBLIC_CLIENT_MODE=true` overrides both and forces the client-safe
 * UI (no model/provider/API-key/engine settings) for everyone.
 */
export function useIsAdmin(): { isAdmin: boolean; isLoaded: boolean } {
    const { permissions, permissionsLoaded } = useUserConfig();

    if (CLIENT_MODE) {
        return { isAdmin: false, isLoaded: true };
    }

    return {
        isAdmin: permissions.some((p) => p.id === 'admin'),
        isLoaded: permissionsLoaded,
    };
}
