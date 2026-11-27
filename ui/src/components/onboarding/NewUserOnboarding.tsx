'use client';

import posthog from 'posthog-js';
import { useEffect, useRef, useState } from 'react';

import { getWorkflowCountApiV1WorkflowCountGet } from '@/client/sdk.gen';
import { PostHogEvent } from '@/constants/posthog-events';
import { useAppConfig } from '@/context/AppConfigContext';
import { useDograhAccountContext } from '@/context/DograhAccountContext';
import { useOnboarding } from '@/context/OnboardingContext';
import { useAuth } from '@/lib/auth';
import { trackMetaCompleteRegistration } from '@/lib/metaPixel';

import { type ModelSetupChoice, ModelSetupModal } from './ModelSetupModal';

/**
 * First-run handling for brand-new users: the onboarding flag is unset and
 * the organization has no agents yet (an existing user joining an org that
 * has agents is never treated as new).
 *
 * Every new user counts as a completed registration. On a self-hosted
 * installation without a working Dograh key, the user then chooses how the
 * installation gets AI models; everywhere else the flag is stamped at once.
 */
export function NewUserOnboarding() {
    const { user, loading: authLoading } = useAuth();
    const { config } = useAppConfig();
    const { loading: onboardingLoading, onboardingCompletedAt, onboardingSkipped, markOnboardingCompleted } = useOnboarding();
    const dograh = useDograhAccountContext();
    const [newSelfHosted, setNewSelfHosted] = useState(false);
    const [open, setOpen] = useState(false);
    // Guard so the one-time workflow-count check runs at most once per mount.
    const checked = useRef(false);
    // Live view of the flag for the post-await re-check below.
    const done = useRef(false);
    done.current = Boolean(onboardingCompletedAt) || onboardingSkipped;
    const { request: requestDograhStatus } = dograh;

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
                if (config.deploymentMode === 'oss') {
                    setNewSelfHosted(true);
                    requestDograhStatus();
                } else {
                    markOnboardingCompleted();
                }
            } catch {
                // Without the count, assume an existing user: never disrupt them.
            }
        })();
    }, [authLoading, onboardingLoading, user, config, markOnboardingCompleted, requestDograhStatus]);

    useEffect(() => {
        if (!newSelfHosted || open) return;
        if (dograh.status === 'connected') {
            // Already set up (for example an upgraded installation): nothing to ask.
            setNewSelfHosted(false);
            markOnboardingCompleted();
        } else if (dograh.status === 'missing' || dograh.status === 'invalid') {
            setOpen(true);
            posthog.capture(PostHogEvent.MODEL_SETUP_SHOWN, { dograh_status: dograh.status });
        }
        // "unknown" leaves the flag unset, so the next session asks again.
    }, [newSelfHosted, open, dograh.status, markOnboardingCompleted]);

    const finish = (choice: ModelSetupChoice) => {
        setOpen(false);
        setNewSelfHosted(false);
        posthog.capture(PostHogEvent.MODEL_SETUP_COMPLETED, { choice });
        markOnboardingCompleted({ skipped: choice === 'skipped' });
    };

    return <ModelSetupModal open={open} reconnect={dograh.status === 'invalid'} onConnected={dograh.refresh} onDone={finish} />;
}
