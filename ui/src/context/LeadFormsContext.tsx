"use client";

import posthog from "posthog-js";
import { createContext, type ReactNode,useCallback, useContext, useMemo, useRef, useState } from "react";

import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { HireExpertModal } from "@/components/lead-forms/HireExpertModal";
import type { LeadSource } from "@/components/lead-forms/leadFieldOptions";
import { TopUpModal } from "@/components/lead-forms/TopUpModal";
import { PostHogEvent } from "@/constants/posthog-events";

interface LeadFormsContextValue {
  openHireExpert: (source: LeadSource) => void;
  openTopUp: (source: LeadSource) => void;
  openEnterprise: (source: LeadSource) => void;
  // True once the hire modal has been opened this session (used to suppress the builder nudge).
  hasOpenedHireRef: React.MutableRefObject<boolean>;
}

const LeadFormsContext = createContext<LeadFormsContextValue | null>(null);

export function LeadFormsProvider({ children }: { children: ReactNode }) {
  const [hireOpen, setHireOpen] = useState(false);
  const [topUpOpen, setTopUpOpen] = useState(false);
  const [enterpriseOpen, setEnterpriseOpen] = useState(false);
  // Track the originating source so the *_OPENED and submit events agree.
  const [hireSource, setHireSource] = useState<LeadSource>("sidebar");
  const [topUpSource, setTopUpSource] = useState<LeadSource>("billing_card");
  const [enterpriseSource, setEnterpriseSource] = useState<LeadSource>("topup");
  const hasOpenedHireRef = useRef(false);

  const openHireExpert = useCallback((source: LeadSource) => {
    hasOpenedHireRef.current = true;
    setHireSource(source);
    setHireOpen(true);
    posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source });
  }, []);

  const openTopUp = useCallback((source: LeadSource) => {
    setTopUpSource(source);
    setTopUpOpen(true);
    posthog.capture(PostHogEvent.TOPUP_REQUEST_OPENED, { source });
  }, []);

  const openEnterprise = useCallback((source: LeadSource) => {
    setEnterpriseSource(source);
    setEnterpriseOpen(true);
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source });
  }, []);

  const value = useMemo(
    () => ({ openHireExpert, openTopUp, openEnterprise, hasOpenedHireRef }),
    [openHireExpert, openTopUp, openEnterprise],
  );

  return (
    <LeadFormsContext.Provider value={value}>
      {children}
      <TopUpModal
        open={topUpOpen}
        onOpenChange={setTopUpOpen}
        source={topUpSource}
        onOpenEnterprise={() => openEnterprise("topup")}
      />
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
      />
    </LeadFormsContext.Provider>
  );
}

export function useLeadForms(): LeadFormsContextValue {
  const ctx = useContext(LeadFormsContext);
  if (!ctx) throw new Error("useLeadForms must be used within a LeadFormsProvider");
  return ctx;
}
