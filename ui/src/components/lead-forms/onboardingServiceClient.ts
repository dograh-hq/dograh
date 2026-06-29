// Thin client for THIS deployment's own lead-capture backend (FastAPI).
// Submissions go to /api/v1/leads/* on our backend, which emails the deployment
// owner (default destination configured server-side via LEAD_NOTIFICATION_EMAIL).
//
// These endpoints are PUBLIC (no auth token): identity is the email carried in
// the body. Every call is BEST-EFFORT — failures are swallowed so a down/erroring
// backend never blocks the user's form submission.

import { resolveBrowserBackendUrl } from "@/lib/apiClient";

// Base URL of our backend. Prefer NEXT_PUBLIC_BACKEND_URL / same-origin (the
// normal app API host). NEXT_PUBLIC_ONBOARDING_API_URL stays supported as an
// optional override for operators who want to route leads elsewhere.
function baseUrl(): string {
  return process.env.NEXT_PUBLIC_ONBOARDING_API_URL || resolveBrowserBackendUrl();
}

// Bound every call so a slow/hung backend can never freeze the UI. Best-effort:
// failures are surfaced via console.error (Sentry breadcrumbs) but never thrown.
const TIMEOUT_MS = 6000;

// POST a JSON body to our lead backend (public — no auth header).
async function post(path: string, body: unknown): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${baseUrl()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    // fetch does not reject on 4xx/5xx — check explicitly so dropped leads are
    // at least observable.
    if (!res.ok) {
      console.error(`[leads] POST ${path} failed with HTTP ${res.status}`);
    }
  } catch (err) {
    // Network error, or the timeout aborted the request. Never block the user.
    console.error(`[leads] POST ${path} did not complete:`, err);
  } finally {
    clearTimeout(timer);
  }
}

// Map a lead kind to its endpoint path on our backend.
const LEAD_PATH: Record<"hire_expert" | "enterprise", string> = {
  hire_expert: "/api/v1/leads/hire-expert",
  enterprise: "/api/v1/leads/enterprise",
};

// Persist a lead submission (hire-expert / enterprise). Email is in the body.
export async function postLeadToService(
  kind: "hire_expert" | "enterprise",
  body: Record<string, unknown>,
): Promise<void> {
  await post(LEAD_PATH[kind], body);
}

// Persist an onboarding submission.
export async function postOnboardingToService(body: Record<string, unknown>): Promise<void> {
  await post("/api/v1/leads/onboarding", body);
}
