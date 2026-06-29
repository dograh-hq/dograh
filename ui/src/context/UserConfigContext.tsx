'use client';

import { useEffect, useSyncExternalStore } from 'react';

import { getAuthUserApiV1UserAuthUserGet } from '@/client/sdk.gen';
import { useAuth } from '@/lib/auth';

import { OrgConfigProvider, useOrgConfig } from './OrgConfigContext';

// Upstream (dograh-v1.38.0) refactored the old inline UserConfigContext into
// `OrgConfigContext`, which now owns the org-context + user-config + pricing +
// permissions state, and turned this module into a thin re-export. The SaaS
// fork layers additional state on top — the platform superuser flag and the
// org's plan tier / feature flags — that the upstream OrgConfig context does
// not carry.
//
// Reconciliation: we keep upstream's OrgConfig provider as the single source
// for the shared org/user state (it is the provider mounted in the app tree),
// and re-export it under the legacy `UserConfigProvider` name. The SaaS-only
// fields are sourced from a small shared store (fetched once via the same
// `/user/auth/user` endpoint the fork has always used) and merged into the
// `useUserConfig()` return value, so every consumer keeps seeing the full
// union shape: OrgConfig's fields PLUS plan/superuser/feature flags.

export { OrgConfigProvider as UserConfigProvider } from './OrgConfigContext';

/** Plan-tier feature flags (see api/services/plans.py). */
export interface PlanFeatures {
    api: boolean;
    mcp: boolean;
}

// SaaS-only state layered on top of upstream's OrgConfig context.
interface SaasUserState {
    /** Platform-level superuser flag (UserModel.is_superuser on the backend). */
    isSuperuser: boolean;
    superuserLoaded: boolean;
    /** Org plan tier ("trial" | "starter" | "growth" | "scale"). */
    plan: string;
    /** Feature flags for the org's plan tier. */
    planFeatures: PlanFeatures;
    /** True once the plan/superuser fetch has resolved (same request). */
    planLoaded: boolean;
    /**
     * True once role information is available. Kept for the legacy
     * `useUserConfig()` shape (useIsAdmin gates on it). It resolves together
     * with the superuser/plan fetch, which becomes ready on the same auth tick
     * as OrgConfig's permission fetch.
     */
    permissionsLoaded: boolean;
}

const INITIAL_SAAS_STATE: SaasUserState = {
    isSuperuser: false,
    superuserLoaded: false,
    plan: 'trial',
    planFeatures: { api: false, mcp: false },
    planLoaded: false,
    permissionsLoaded: false,
};

// Module-level shared store so the SaaS fields are fetched once and shared
// across every `useUserConfig()` consumer, independent of which provider the
// app tree mounts (upstream mounts `OrgConfigProvider`, which has no place for
// these fields).
let saasState: SaasUserState = INITIAL_SAAS_STATE;
let saasFetchStarted = false;
const saasListeners = new Set<() => void>();

function setSaasState(next: Partial<SaasUserState>) {
    saasState = { ...saasState, ...next };
    saasListeners.forEach((listener) => listener());
}

function subscribeSaasState(listener: () => void) {
    saasListeners.add(listener);
    return () => {
        saasListeners.delete(listener);
    };
}

function getSaasSnapshot() {
    return saasState;
}

function useSaasUserState(): SaasUserState {
    const auth = useAuth();
    const state = useSyncExternalStore(subscribeSaasState, getSaasSnapshot, getSaasSnapshot);

    useEffect(() => {
        // Fetch the platform superuser flag + plan/features once, after auth is
        // ready. Mirrors the fork's original behaviour (single fetch guarded by
        // a ref) using a module-level guard so it stays a single request.
        if (auth.loading || saasFetchStarted) {
            return;
        }
        saasFetchStarted = true;

        const fetchSaasState = async () => {
            if (!auth.isAuthenticated) {
                setSaasState({ superuserLoaded: true, planLoaded: true, permissionsLoaded: true });
                return;
            }
            try {
                const response = await getAuthUserApiV1UserAuthUserGet();
                // `plan`/`features` are served by the same endpoint; the generated
                // SDK type predates them, so read through a widened shape.
                const data = response.data as
                    | { is_superuser?: boolean; plan?: string; features?: Partial<PlanFeatures> }
                    | undefined;
                setSaasState({
                    isSuperuser: !!data?.is_superuser,
                    plan: data?.plan ?? 'trial',
                    planFeatures: { api: !!data?.features?.api, mcp: !!data?.features?.mcp },
                    superuserLoaded: true,
                    planLoaded: true,
                    permissionsLoaded: true,
                });
            } catch {
                setSaasState({
                    isSuperuser: false,
                    plan: 'trial',
                    planFeatures: { api: false, mcp: false },
                    superuserLoaded: true,
                    planLoaded: true,
                    permissionsLoaded: true,
                });
            }
        };

        fetchSaasState();
    }, [auth.loading, auth.isAuthenticated]);

    return state;
}

/**
 * Legacy SaaS hook. Returns the full union shape: every field exposed by
 * upstream's `useOrgConfig()` (orgContext, userConfig, saveUserConfig, loading,
 * error, refreshConfig, permissions, user, organizationPricing) PLUS the
 * SaaS-only plan/superuser/feature fields.
 */
export function useUserConfig() {
    const orgConfig = useOrgConfig();
    const saasState = useSaasUserState();

    return {
        ...orgConfig,
        ...saasState,
    };
}

// Re-export so existing imports of the upstream hook keep resolving.
export { useOrgConfig } from './OrgConfigContext';
