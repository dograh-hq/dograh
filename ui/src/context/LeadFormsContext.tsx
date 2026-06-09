"use client";

import posthog from "posthog-js";
import { createContext, type ReactNode,useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { getWorkflowCountApiV1WorkflowCountGet } from "@/client/sdk.gen";
import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { HireExpertModal } from "@/components/lead-forms/HireExpertModal";
import type { LeadSource } from "@/components/lead-forms/leadFieldOptions";
import { OnboardingModal } from "@/components/lead-forms/OnboardingModal";
import { PostHogEvent } from "@/constants/posthog-events";
import { useUserConfig } from "@/context/UserConfigContext";

// The onboarding flag fields live on the Dograh user-config JSON blob. The
// generated client type may not include them until `npm run generate-client`
// is re-run against the updated backend, so read them through this shape.
type OnboardingFlags = {
  onboarding_completed_at?: string | null;
  onboarding_skipped?: boolean | null;
};

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
  //   (a) the completion flag is unset (server-side, cross-device), AND
  //   (b) the user has zero workflows (grandfathers out all existing users —
  //       they already have workflows, so they never see this modal).
  const { userConfig, loading: userConfigLoading, user, saveUserConfig } = useUserConfig();
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  // Guard so the one-time workflow-count check runs at most once per mount.
  const onboardingCheckedRef = useRef(false);

  useEffect(() => {
    if (userConfigLoading || !user || onboardingCheckedRef.current) return;

    const flags = userConfig as OnboardingFlags | null;
    const completed = Boolean(flags?.onboarding_completed_at) || Boolean(flags?.onboarding_skipped);
    if (completed) {
      onboardingCheckedRef.current = true; // already done — never show
      return;
    }

    onboardingCheckedRef.current = true;
    // Only brand-new users (no workflows yet) see the form. The count is
    // org-scoped (the user's selected organization), so a new user joining an
    // org that already has workflows is correctly grandfathered out. This costs
    // one lightweight count query per session for users whose flag is still
    // unset — an accepted trade for a server-authoritative, cross-device gate.
    (async () => {
      try {
        const res = await getWorkflowCountApiV1WorkflowCountGet();
        // Re-read the flag after the await: a config save elsewhere may have
        // stamped completion while the count was in flight.
        const latest = userConfig as OnboardingFlags | null;
        const stillPending =
          !latest?.onboarding_completed_at && !latest?.onboarding_skipped;
        if (res.data?.total === 0 && stillPending) {
          setOnboardingOpen(true);
          posthog.capture(PostHogEvent.ONBOARDING_SHOWN);
        }
      } catch {
        // If the count can't be fetched, do NOT show the modal — fail closed so
        // existing users are never disrupted.
      }
    })();
  }, [userConfigLoading, user, userConfig]);

  const completeOnboarding = useCallback(() => {
    // Dismiss immediately; stamp the server flag best-effort so it never re-shows
    // (cross-device, and before the user has built a workflow). saveUserConfig
    // already merges with the existing config, so only the new field is needed.
    setOnboardingOpen(false);
    void saveUserConfig({
      onboarding_completed_at: new Date().toISOString(),
    } as Parameters<typeof saveUserConfig>[0]).catch(() => {
      // Best-effort: the user is already past the form; a failed stamp only risks
      // a re-prompt on another device, which is acceptable.
    });
  }, [saveUserConfig]);

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
        onOpenEnterprise={(prefill) => openEnterprise("onboarding", prefill)}
      />
    </LeadFormsContext.Provider>
  );
}

export function useLeadForms(): LeadFormsContextValue {
  const ctx = useContext(LeadFormsContext);
  if (!ctx) throw new Error("useLeadForms must be used within a LeadFormsProvider");
  return ctx;
}
