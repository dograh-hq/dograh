"use client";

// Enterprise call-to-action rendered inside the auth brand panel. Opens the
// SAME in-app Enterprise lead modal used post-login (not the marketing site's
// /contact page). The visitor is typically NOT authenticated here: the modal
// requires a work email in that case, and submitLead persists the lead through
// the user_onboarding service's public contact-sales endpoint instead of the
// token-gated /leads/enterprise. Shared by the Stack Auth handler and the
// local/OSS auth pages.

import posthog from "posthog-js";
import { useState } from "react";

import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { Button } from "@/components/ui/button";
import { PostHogEvent } from "@/constants/posthog-events";

export function AuthEnterpriseCTA() {
  const [open, setOpen] = useState(false);

  const openModal = () => {
    setOpen(true);
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source: "auth_page" });
  };

  return (
    <>
      <Button
        variant="outline"
        onClick={openModal}
        className="w-full border-white/20 bg-white/5 text-zinc-100 hover:bg-white/10 hover:text-white"
      >
        Enterprise Enquiry
      </Button>
      <EnterpriseModal open={open} onOpenChange={setOpen} source="auth_page" />
    </>
  );
}
