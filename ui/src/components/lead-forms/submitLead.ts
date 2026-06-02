// Single submission seam for all lead forms.
// Today: fires a PostHog capture. Later: add a POST to the backend
// (MongoDB) endpoint here — no form component will need to change.

import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import type { LeadKind, LeadSource } from "./leadFieldOptions";

const SUBMIT_EVENT: Record<LeadKind, string> = {
  topup: PostHogEvent.TOPUP_REQUESTED,
  hire_expert: PostHogEvent.HIRE_EXPERT_SUBMITTED,
  enterprise: PostHogEvent.ENTERPRISE_LEAD_SUBMITTED,
};

export interface SubmitLeadArgs {
  kind: LeadKind;
  source: LeadSource;
  // Field values, already validated by the caller. Non-sensitive lead data.
  payload: Record<string, unknown>;
}

export async function submitLead({ kind, source, payload }: SubmitLeadArgs): Promise<void> {
  // PostHog capture — the durable record until the backend endpoint lands.
  posthog.capture(SUBMIT_EVENT[kind], { source, ...payload });

  // FUTURE: when the MongoDB endpoint exists, POST here, e.g.
  //   const res = await submitLeadApiV1LeadsPost({ body: { kind, source, ...payload } });
  //   if (res.error) throw new Error(detailFromError(res.error, "Failed to submit"));
}
