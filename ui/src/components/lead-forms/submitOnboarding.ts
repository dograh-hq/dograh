// Submission seam for the post-signup onboarding form.
// Fires a PostHog capture (submit or skip) AND, when a token is supplied, POSTs
// the answers to the separate user_onboarding service (best-effort). The "show
// once per user" flag itself is stamped on the Dograh user-config by the caller
// (LeadFormsContext.completeOnboarding), not here — that needs the saveUserConfig hook.

import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import { postOnboardingToService } from "./onboardingServiceClient";

export interface OnboardingAnswers {
  companyName?: string;
  usageContext?: string;
  persona?: string;
  // Only present when persona unlocks the on-prem question.
  onPremNeed?: string;
}

export async function submitOnboarding(answers: OnboardingAnswers, token?: string): Promise<void> {
  posthog.capture(PostHogEvent.ONBOARDING_SUBMITTED, { ...answers });
  if (token) {
    await postOnboardingToService(token, { source: "onboarding", ...answers, skipped: false });
  }
}

export async function skipOnboarding(answers: OnboardingAnswers, token?: string): Promise<void> {
  // Skipping is itself signal — capture whatever was filled before the skip.
  posthog.capture(PostHogEvent.ONBOARDING_SKIPPED, { ...answers });
  if (token) {
    await postOnboardingToService(token, { source: "onboarding", ...answers, skipped: true });
  }
}
