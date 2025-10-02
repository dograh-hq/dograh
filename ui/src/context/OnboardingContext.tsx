'use client';

import { createContext, useContext, useEffect, useState } from 'react';

export type TooltipKey = 'web_call'; // Add more tooltip keys as needed

interface OnboardingState {
    seenTooltips: TooltipKey[];
}

interface OnboardingContextType {
    hasSeenTooltip: (key: TooltipKey) => boolean;
    markTooltipSeen: (key: TooltipKey) => void;
    resetOnboarding: () => void;
}

const ONBOARDING_STORAGE_KEY = 'dograh_onboarding_state';

const defaultState: OnboardingState = {
    seenTooltips: [],
};

const OnboardingContext = createContext<OnboardingContextType | undefined>(undefined);

export const OnboardingProvider = ({ children }: { children: React.ReactNode }) => {
    const [onboardingState, setOnboardingState] = useState<OnboardingState>(() => {
        // Initialize state from localStorage on first render
        if (typeof window !== 'undefined') {
            const savedState = localStorage.getItem(ONBOARDING_STORAGE_KEY);
            if (savedState) {
                try {
                    const parsed = JSON.parse(savedState);
                    return { ...defaultState, ...parsed };
                } catch (error) {
                    console.error('Failed to parse onboarding state:', error);
                }
            }
        }
        return defaultState;
    });

    // Save state to localStorage whenever it changes
    useEffect(() => {
        if (typeof window !== 'undefined') {
            localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(onboardingState));
        }
    }, [onboardingState]);

    const hasSeenTooltip = (key: TooltipKey): boolean => {
        return onboardingState.seenTooltips.includes(key);
    };

    const markTooltipSeen = (key: TooltipKey) => {
        setOnboardingState(prev => ({
            ...prev,
            seenTooltips: prev.seenTooltips.includes(key)
                ? prev.seenTooltips
                : [...prev.seenTooltips, key]
        }));
    };

    const resetOnboarding = () => {
        setOnboardingState(defaultState);
        localStorage.removeItem(ONBOARDING_STORAGE_KEY);
    };

    return (
        <OnboardingContext.Provider
            value={{
                hasSeenTooltip,
                markTooltipSeen,
                resetOnboarding
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
