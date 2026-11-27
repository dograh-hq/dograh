'use client';

import posthog from 'posthog-js';
import { useCallback, useEffect, useRef, useState } from 'react';

import { PostHogEvent } from '@/constants/posthog-events';
import { useAppConfig } from '@/context/AppConfigContext';
import { useDograhAccount } from '@/context/DograhAccountContext';
import { useOnboarding } from '@/context/OnboardingContext';
import { useAuth } from '@/lib/auth';

import { type ModelSetupChoice, ModelSetupModal } from './ModelSetupModal';

/** Pause an explicit action for setup, retaining the caller's input until it resumes. */
export function useModelSetup(source: 'builder' | 'call', enabled = true) {
    const { user, loading: authLoading } = useAuth();
    const organization = user && ('selectedTeam' in user ? user.selectedTeam?.id : 'organizationId' in user ? user.organizationId : '');
    const scope = user ? `${user.id}:${organization}` : null;
    const { config } = useAppConfig();
    const selfHosted = config?.deploymentMode === 'oss';
    const dograh = useDograhAccount(enabled && (selfHosted || source === 'builder'));
    const { loading: onboardingLoading, hasCompletedAction, markActionCompleted } = useOnboarding();
    const [open, setOpen] = useState(false);
    const resolve = useRef<((proceed: boolean) => void) | null>(null);
    const dismissed = hasCompletedAction('model_setup_dismissed');
    const needsSetup = selfHosted && (dograh.status === 'missing' || dograh.status === 'invalid');
    const checking = authLoading || !user || !config || ((selfHosted || source === 'builder') && dograh.status === 'loading') || (needsSetup && source === 'call' && onboardingLoading);

    // Closing the caller, navigating away, or switching organizations must
    // never resume an abandoned request or call under a different account.
    useEffect(() => {
        setOpen(false);
        return () => { resolve.current?.(false); resolve.current = null; };
    }, [enabled, scope]);

    const ensureSetup = useCallback(async () => {
        if (!enabled || checking || resolve.current) return false;
        if (!needsSetup || (source === 'call' && dismissed)) return true;
        posthog.capture(PostHogEvent.MODEL_SETUP_SHOWN, { dograh_status: dograh.status, source });
        setOpen(true);
        return new Promise<boolean>(done => { resolve.current = done; });
    }, [enabled, checking, needsSetup, source, dismissed, dograh.status]);

    const close = (proceed: boolean) => {
        setOpen(false);
        resolve.current?.(proceed);
        resolve.current = null;
    };

    const finish = (choice: ModelSetupChoice) => {
        posthog.capture(PostHogEvent.MODEL_SETUP_COMPLETED, { choice, source });
        if (choice === 'skipped' || choice === 'own_keys') markActionCompleted('model_setup_dismissed');
        // BYOK can run calls, but the builder always needs a Dograh account.
        close(choice === 'dograh' || (source === 'call' && choice === 'skipped'));
    };

    return {
        dograh,
        selfHosted,
        checking,
        ensureSetup,
        setupModal: open ? <ModelSetupModal open reconnect={dograh.status === 'invalid'} onConnected={dograh.refresh} onDone={finish} onCancel={() => close(false)} /> : null,
    };
}
