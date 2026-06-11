'use client';

import { useUserConfig } from '@/context/UserConfigContext';
import { CLIENT_MODE } from '@/lib/brand';

/**
 * Role check built on the platform's real permission mechanism:
 *
 * - Superusers (UserModel.is_superuser on the backend, surfaced via
 *   /user/auth/user) are ALWAYS admins — even in CLIENT_MODE. This is the
 *   deployment owner; ADMIN_EMAILS on the API promotes them automatically.
 * - Stack Auth deployments: the selected team's permissions are fetched via
 *   `listPermissions(selectedTeam)`; org admins carry the `admin` permission
 *   (the same check `getRedirectUrl` in lib/utils.ts already uses).
 * - Local/OSS deployments: upstream grants every user `[{ id: 'admin' }]`.
 *
 * `NEXT_PUBLIC_CLIENT_MODE=true` forces the client-safe UI (no model/
 * provider/API-key/engine settings) for everyone EXCEPT superusers.
 */
export function useIsAdmin(): { isAdmin: boolean; isLoaded: boolean } {
    const { permissions, permissionsLoaded, isSuperuser, superuserLoaded } = useUserConfig();

    if (CLIENT_MODE) {
        // Only the superuser (deployment owner) keeps admin surfaces.
        return { isAdmin: isSuperuser, isLoaded: superuserLoaded };
    }

    return {
        isAdmin: isSuperuser || permissions.some((p) => p.id === 'admin'),
        isLoaded: permissionsLoaded && superuserLoaded,
    };
}
