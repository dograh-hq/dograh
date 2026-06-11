// Thin client for the SEPARATE user_onboarding service (its own base URL).
// Not part of the generated Dograh SDK — a different host. Sends the SAME Dograh
// Bearer token the browser already holds. All calls are BEST-EFFORT: failures are
// swallowed so a down/erroring service never blocks the user from the product.

// Base URL of the user_onboarding service; unset → calls are skipped (no-op).
const BASE_URL = process.env.NEXT_PUBLIC_ONBOARDING_API_URL;

// Bound every call so a slow/hung service can never freeze the UI (the onboarding
// modal used to await this with no timeout). Best-effort: failures are surfaced
// via console.error (captured as Sentry breadcrumbs) but never thrown.
const TIMEOUT_MS = 6000;

// POST a JSON body to the onboarding service with the Dograh auth token attached.
async function post(path: string, token: string, body: unknown): Promise<void> {
  if (!BASE_URL) {
    // Misconfig would otherwise be invisible: a token-bearing submit dropped on
    // the floor while PostHog still records the event as "submitted".
    if (token) {
      console.error(
        `[onboarding] NEXT_PUBLIC_ONBOARDING_API_URL is unset — "${path}" not persisted to the onboarding service`,
      );
    }
    return;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
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

// Persist a lead submission (hire-expert / enterprise).
export async function postLeadToService(
  kind: "hire_expert" | "enterprise",
  token: string,
  body: Record<string, unknown>,
): Promise<void> {
  await post(LEAD_PATH[kind], token, body);
}

// Persist an onboarding submission (or skip — body carries `skipped`).
export async function postOnboardingToService(
  token: string,
  body: Record<string, unknown>,
): Promise<void> {
  await post("/api/v1/onboarding", token, body);
}
