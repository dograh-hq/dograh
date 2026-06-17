// Thin client for the SEPARATE user_onboarding service (its own base URL).
// Not part of the generated Dograh SDK — a different host. All endpoints are PUBLIC
// (no auth token); identity is the email carried in the body. Every call is
// BEST-EFFORT: failures are swallowed so a down/erroring service never blocks the user.

// Base URL of the user_onboarding service. Unset (the default for self-hosted OSS —
// .env.example ships this commented out) → fall back to our cloud leads backend so we
// still receive OSS form submissions. Override the env var to point elsewhere (or to a
// local backend) to stop sending leads to us.
const BASE_URL = process.env.NEXT_PUBLIC_ONBOARDING_API_URL || "https://api-leads.dograh.com";

// Bound every call so a slow/hung service can never freeze the UI. Best-effort:
// failures are surfaced via console.error (Sentry breadcrumbs) but never thrown.
const TIMEOUT_MS = 6000;

// POST a JSON body to the onboarding service (public — no auth header).
async function post(path: string, body: unknown): Promise<void> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    // fetch does not reject on 4xx/5xx — check explicitly so dropped leads are
    // at least observable.
    if (!res.ok) {
      console.error(`[onboarding] POST ${path} failed with HTTP ${res.status}`);
    }
  } catch (err) {
    // Network error, or the timeout aborted the request. Never block the user.
    console.error(`[onboarding] POST ${path} did not complete:`, err);
  } finally {
    clearTimeout(timer);
  }
}

// Map a lead kind to its endpoint path on the onboarding service.
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

// Persist an onboarding submission (or skip — body carries `skipped`).
export async function postOnboardingToService(body: Record<string, unknown>): Promise<void> {
  await post("/api/v1/onboarding", body);
}
