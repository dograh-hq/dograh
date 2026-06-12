// Single submission seam for all lead forms.
// Fires a PostHog capture, and (when a token is supplied) POSTs to the separate
// user_onboarding service. The service call is best-effort — PostHog is the
// durable record and the user is never blocked if the service is down.

import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import type { LeadKind, LeadSource } from "./leadFieldOptions";
import { postContactSalesToService, postLeadToService } from "./onboardingServiceClient";

const SUBMIT_EVENT: Record<LeadKind, string> = {
  hire_expert: PostHogEvent.HIRE_EXPERT_SUBMITTED,
  enterprise: PostHogEvent.ENTERPRISE_LEAD_SUBMITTED,
};

export interface SubmitLeadArgs {
  kind: LeadKind;
  source: LeadSource;
  // Field values, already validated by the caller. Non-sensitive lead data.
  payload: Record<string, unknown>;
  // Dograh auth token; when present the lead is also persisted to the service.
  token?: string;
}

export async function submitLead({ kind, source, payload, token }: SubmitLeadArgs): Promise<void> {
  // PostHog capture — the durable record, always fired.
  posthog.capture(SUBMIT_EVENT[kind], { source, ...payload });

  // Persist to the separate user_onboarding service (best-effort).
  if (token) {
    await postLeadToService(kind, token, { source, ...payload });
  } else if (kind === "enterprise") {
    // Logged-out visitor (e.g. the auth-page Enterprise Enquiry CTA): the
    // public contact-sales endpoint persists the lead and runs the same
    // unified enterprise flow server-side, keyed off `workEmail` (which the
    // form requires when unauthenticated).
    await postContactSalesToService({ source, ...payload });
  }
}
