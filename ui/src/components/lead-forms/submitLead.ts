// Single submission seam for all lead forms.
// Fires a PostHog capture (the durable record) and POSTs to the separate, PUBLIC
// user_onboarding service (best-effort — the user is never blocked if it's down).
// No auth token: identity is the email in the payload.

import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import { detectCountry, detectTimezone } from "./detectCountry";
import type { LeadKind, LeadOrigin, LeadSource } from "./leadFieldOptions";
import { type LeadResult, postLeadToService } from "./onboardingServiceClient";

const SUBMIT_EVENT: Record<LeadKind, string> = {
  hire_expert: PostHogEvent.HIRE_EXPERT_SUBMITTED,
  enterprise: PostHogEvent.ENTERPRISE_LEAD_SUBMITTED,
};

export interface SubmitLeadArgs {
  kind: LeadKind;
  source: LeadSource;
  // Deployment provenance (analytics only): "cloud_app" | "oss_app".
  origin: LeadOrigin;
  // Field values, already validated by the caller. Includes the contact email.
  payload: Record<string, unknown>;
}

export async function submitLead({ kind, source, origin, payload }: SubmitLeadArgs): Promise<LeadResult | null> {
  // `country` (name) + `timezone` (raw IANA) are detected silently — no visible fields.
  // Both are neutral analytics in the body; the backend alone decides anything from them
  // . `country` also feeds the email subject.
  const body = { source, origin, country: detectCountry(), timezone: detectTimezone(), ...payload };
  // PostHog capture — the durable record, always fired.
  posthog.capture(SUBMIT_EVENT[kind], body);
  // Persist to the separate user_onboarding service (best-effort, public); return its verdict.
  return postLeadToService(kind, body);
}
