'use client';

import { useUserConfig } from '@/context/UserConfigContext';

/**
 * Plan-tier feature gate.
 *
 * - The deployment owner (superuser) always passes.
 * - Otherwise the org's plan must include the feature:
 *     api → REST API keys / Developers (Growth & Scale)
 *     mcp → MCP server (Scale only)
 *
 * `loaded` is false until the plan fetch resolves — gate UI on it to avoid
 * flashing a surface the org can't use.
 */
export function useFeature(feature: 'api' | 'mcp'): { enabled: boolean; loaded: boolean } {
    const { isSuperuser, planFeatures, planLoaded } = useUserConfig();
    return { enabled: isSuperuser || planFeatures[feature], loaded: planLoaded };
}
