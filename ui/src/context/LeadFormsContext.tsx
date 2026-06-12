"use client";

import posthog from "posthog-js";
import { createContext, type ReactNode,useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { getWorkflowCountApiV1WorkflowCountGet } from "@/client/sdk.gen";
import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { HireExpertModal } from "@/components/lead-forms/HireExpertModal";
import type { LeadSource } from "@/components/lead-forms/leadFieldOptions";
import { OnboardingModal } from "@/components/lead-forms/OnboardingModal";
import { PostHogEvent } from "@/constants/posthog-events";
import { useOnboarding } from "@/context/OnboardingContext";
import { useAuth } from "@/lib/auth";

interface LeadFormsContextValue {
  openHireExpert: (source: LeadSource) => void;
  openEnterprise: (source: LeadSource, prefill?: { company?: string }) => void;
  // True once the hire modal has been opened this session (used to suppress the builder nudge).
  hasOpenedHireRef: React.MutableRefObject<boolean>;
}

const LeadFormsContext = createContext<LeadFormsContextValue | null>(null);

export function LeadFormsProvider({ children }: { children: ReactNode }) {
  const [hireOpen, setHireOpen] = useState(false);
  const [enterpriseOpen, setEnterpriseOpen] = useState(false);
  // Track the originating source so the *_OPENED and submit events agree.
  const [hireSource, setHireSource] = useState<LeadSource>("sidebar");
  const [enterpriseSource, setEnterpriseSource] = useState<LeadSource>("sidebar");
  const [enterprisePrefill, setEnterprisePrefill] = useState<{ company?: string } | undefined>(undefined);
  const hasOpenedHireRef = useRef(false);

  // ---- Post-signup onboarding gate ----
  // Show the onboarding form ONCE per user, and ONLY to genuinely new users:
  //   (a) the completion/skip flag is unset (server-backed onboarding state,
  //       cross-device), AND
  //   (b) the user has zero workflows (grandfathers out all existing users —
  //       they already have workflows, so they never see this modal).
  const { user, loading: authLoading } = useAuth();
  const {
    loading: onboardingLoading,
    onboardingCompletedAt,
    onboardingSkipped,
    markOnboardingCompleted,
  } = useOnboarding();
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  // Guard so the one-time workflow-count check runs at most once per mount.
  const onboardingCheckedRef = useRef(false);
  // Live view of the gate for the post-await re-check below.
  const onboardingDoneRef = useRef(false);
  onboardingDoneRef.current = Boolean(onboardingCompletedAt) || onboardingSkipped;

  useEffect(() => {
    if (authLoading || onboardingLoading || !user || onboardingCheckedRef.current) {
      return;
    }

    onboardingCheckedRef.current = true;
    if (onboardingDoneRef.current) return; // already done — never show

    // Only brand-new users (no workflows yet) see the form. The count is
    // org-scoped (the user's selected organization), so a new user joining an
    // org that already has workflows is correctly grandfathered out. This costs
    // one lightweight count query per session for users whose flag is still
    // unset — an accepted trade for a server-authoritative, cross-device gate.
    (async () => {
      try {
        const res = await getWorkflowCountApiV1WorkflowCountGet();
        // Re-check the flag after the await: a completion elsewhere (another
        // tab) may have stamped it while the count was in flight.
        if (res.data?.total === 0 && !onboardingDoneRef.current) {
          setOnboardingOpen(true);
          posthog.capture(PostHogEvent.ONBOARDING_SHOWN);
        }
      } catch {
        // If the count can't be fetched, do NOT show the modal — fail closed so
        // existing users are never disrupted.
      }
    })();
  }, [authLoading, onboardingLoading, user]);

  const completeOnboarding = useCallback((skipped: boolean) => {
    // Dismiss immediately, then persist the flag through OnboardingContext
    // (optimistic local state closes the gate even if the server write lags;
    // the write itself is best-effort and cross-device).
    setOnboardingOpen(false);
    markOnboardingCompleted({ skipped });
  }, [markOnboardingCompleted]);

  const openHireExpert = useCallback((source: LeadSource) => {
    hasOpenedHireRef.current = true;
    setHireSource(source);
    setHireOpen(true);
    posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source });
  }, []);

  const openEnterprise = useCallback((source: LeadSource, prefill?: { company?: string }) => {
    setEnterpriseSource(source);
    setEnterprisePrefill(prefill);
    setEnterpriseOpen(true);
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source });
  }, []);

  const value = useMemo(
    () => ({ openHireExpert, openEnterprise, hasOpenedHireRef }),
    [openHireExpert, openEnterprise],
  );

  return (
    <LeadFormsContext.Provider value={value}>
      {children}
      <HireExpertModal
        open={hireOpen}
        onOpenChange={setHireOpen}
        source={hireSource}
        onOpenEnterprise={() => openEnterprise("hire_expert")}
      />
      <EnterpriseModal
        open={enterpriseOpen}
        onOpenChange={setEnterpriseOpen}
        source={enterpriseSource}
        prefill={enterprisePrefill}
      />
      <OnboardingModal
        open={onboardingOpen}
        onComplete={completeOnboarding}
      />
    </LeadFormsContext.Provider>
  );
}

export function useLeadForms(): LeadFormsContextValue {
  const ctx = useContext(LeadFormsContext);
  if (!ctx) throw new Error("useLeadForms must be used within a LeadFormsProvider");
  return ctx;
}
