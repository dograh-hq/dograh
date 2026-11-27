'use client';

import { useEffect, useRef } from 'react';

import { getWorkflowCountApiV1WorkflowCountGet } from '@/client/sdk.gen';
import { useAppConfig } from '@/context/AppConfigContext';
import { useOnboarding } from '@/context/OnboardingContext';
import { useAuth } from '@/lib/auth';
import { trackMetaCompleteRegistration } from '@/lib/metaPixel';

/**
 * First-run handling for brand-new users: the onboarding flag is unset and
 * the organization has no agents yet (an existing user joining an org that
 * has agents is never treated as new).
 *
 * Record registration without interrupting exploration. Model setup is
 * offered when the user submits a builder request or attempts a call.
 */
export function NewUserOnboarding() {
    const { user, loading: authLoading } = useAuth();
    const { config } = useAppConfig();
    const { loading: onboardingLoading, onboardingCompletedAt, onboardingSkipped, markOnboardingCompleted } = useOnboarding();
    // Guard so the one-time workflow-count check runs at most once per mount.
    const checked = useRef(false);
    // Live view of the flag for the post-await re-check below.
    const done = useRef(false);
    done.current = Boolean(onboardingCompletedAt) || onboardingSkipped;

    useEffect(() => {
        if (authLoading || onboardingLoading || !user || !config || checked.current) return;
        checked.current = true;
        if (done.current) return;
        void (async () => {
            try {
                const response = await getWorkflowCountApiV1WorkflowCountGet();
                // Re-check after the await: another tab may have stamped the flag.
                if (response.data?.total !== 0 || done.current) return;
                trackMetaCompleteRegistration();
                markOnboardingCompleted();
            } catch {
                // Without the count, assume an existing user: never disrupt them.
            }
        })();
    }, [authLoading, onboardingLoading, user, config, markOnboardingCompleted]);

    return null;
}
