'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

import {
    getUserOnboardingStateApiV1UserOnboardingStateGet,
    updateUserOnboardingStateApiV1UserOnboardingStatePut,
} from '@/client/sdk.gen';
import type { OnboardingStateUpdate } from '@/client/types.gen';
import { useAuth } from '@/lib/auth';

export type TooltipKey = 'web_call' | 'customize_workflow';
export type OnboardingActionKey = 'web_call_started';

// Server-backed onboarding state (GET/PUT /user/onboarding-state), stored
// per-user under the ONBOARDING user-configuration key — deliberately
// independent of the AI model configuration. Replaces the old
// localStorage-only store so one-time UI (post-signup gate, tooltips,
// milestone actions) holds across devices and browsers.
interface OnboardingState {
    completed_at: string | null;
    skipped: boolean;
    seen_tooltips: string[];
    completed_actions: string[];
}

interface OnboardingContextType {
    // True until the server state has been fetched. While loading, the
    // has* checks report "already seen/done" so one-time UI never flashes
    // for users who have in fact seen it.
    loading: boolean;
    // Post-signup onboarding form gate (set once on submit/skip).
    onboardingCompletedAt: string | null;
    onboardingSkipped: boolean;
    markOnboardingCompleted: (opts?: { skipped?: boolean }) => void;
    hasSeenTooltip: (key: TooltipKey) => boolean;
    markTooltipSeen: (key: TooltipKey) => void;
    hasCompletedAction: (key: OnboardingActionKey) => boolean;
    markActionCompleted: (key: OnboardingActionKey) => void;
}

const defaultState: OnboardingState = {
    completed_at: null,
    skipped: false,
    seen_tooltips: [],
    completed_actions: [],
};

const union = (a: string[], b: string[] | null | undefined) =>
    [...a, ...(b ?? []).filter((item) => !a.includes(item))];

// Merge a server response into local state monotonically: flags only ever
// advance, so a response that raced a newer optimistic mark can't revert it.
const absorb = (prev: OnboardingState, server: Partial<OnboardingState>): OnboardingState => ({
    completed_at: prev.completed_at ?? server.completed_at ?? null,
    skipped: prev.skipped || Boolean(server.skipped),
    seen_tooltips: union(prev.seen_tooltips, server.seen_tooltips),
    completed_actions: union(prev.completed_actions, server.completed_actions),
});

const OnboardingContext = createContext<OnboardingContextType | undefined>(undefined);

export const OnboardingProvider = ({ children }: { children: React.ReactNode }) => {
    const [state, setState] = useState<OnboardingState>(defaultState);
    const [loaded, setLoaded] = useState(false);

    const auth = useAuth();
    const authRef = useRef(auth);
    authRef.current = auth;
    const hasFetched = useRef(false);

    useEffect(() => {
        if (auth.loading || hasFetched.current) return;
        if (!auth.isAuthenticated) {
            // Unauthenticated pages (login/signup) have no onboarding state;
            // unblock consumers with defaults.
            setLoaded(true);
            return;
        }
        hasFetched.current = true;

        (async () => {
            const res = await getUserOnboardingStateApiV1UserOnboardingStateGet().catch(() => null);
            if (res?.data) {
                const data = res.data as Partial<OnboardingState>;
                setState((prev) => absorb(prev, data));
                setLoaded(true);
            } else {
                // Fetch failed: stay in loading so one-time UI stays suppressed
                // (fail closed — never re-show onboarding to an onboarded user).
                console.error('[onboarding] failed to fetch onboarding state', res?.error);
            }
        })();
    }, [auth.loading, auth.isAuthenticated]);

    // Best-effort server write. Only the delta is sent; the server unions list
    // fields into the stored state, so concurrent tabs don't drop each other's
    // updates. The response is the merged state — use it to reconcile.
    const persist = useCallback((update: OnboardingStateUpdate) => {
        if (!authRef.current.isAuthenticated) return;
        void updateUserOnboardingStateApiV1UserOnboardingStatePut({ body: update })
            .then((res) => {
                if (res.error) {
                    console.error('[onboarding] failed to persist onboarding state', res.error);
                } else if (res.data) {
                    const data = res.data as Partial<OnboardingState>;
                    setState((prev) => absorb(prev, data));
                }
            })
            .catch(() => {
                console.error('[onboarding] failed to persist onboarding state');
            });
    }, []);

    const markOnboardingCompleted = useCallback((opts?: { skipped?: boolean }) => {
        const skipped = opts?.skipped ?? false;
        const completedAt = new Date().toISOString();
        // Optimistic: the gate must close immediately and never re-open.
        setState((prev) => ({
            ...prev,
            skipped: prev.skipped || skipped,
            completed_at: prev.completed_at ?? (skipped ? null : completedAt),
        }));
        persist(skipped ? { skipped: true } : { completed_at: completedAt });
    }, [persist]);

    const hasSeenTooltip = useCallback(
        (key: TooltipKey) => !loaded || state.seen_tooltips.includes(key),
        [loaded, state.seen_tooltips],
    );

    const markTooltipSeen = useCallback((key: TooltipKey) => {
        setState((prev) =>
            prev.seen_tooltips.includes(key)
                ? prev
                : { ...prev, seen_tooltips: [...prev.seen_tooltips, key] }
        );
        persist({ seen_tooltips: [key] });
    }, [persist]);

    const hasCompletedAction = useCallback(
        (key: OnboardingActionKey) => !loaded || state.completed_actions.includes(key),
        [loaded, state.completed_actions],
    );

    const markActionCompleted = useCallback((key: OnboardingActionKey) => {
        setState((prev) =>
            prev.completed_actions.includes(key)
                ? prev
                : { ...prev, completed_actions: [...prev.completed_actions, key] }
        );
        persist({ completed_actions: [key] });
    }, [persist]);

    return (
        <OnboardingContext.Provider
            value={{
                loading: !loaded,
                onboardingCompletedAt: state.completed_at,
                onboardingSkipped: state.skipped,
                markOnboardingCompleted,
                hasSeenTooltip,
                markTooltipSeen,
                hasCompletedAction,
                markActionCompleted,
            }}
        >
            {children}
        </OnboardingContext.Provider>
    );
};

export const useOnboarding = () => {
    const context = useContext(OnboardingContext);
    if (!context) {
        throw new Error('useOnboarding must be used within an OnboardingProvider');
    }
    return context;
};
