// Thin client for the SEPARATE user_onboarding service (its own base URL).
// Not part of the generated Dograh SDK — a different host. Sends the SAME Dograh
// Bearer token the browser already holds. All calls are BEST-EFFORT: failures are
// swallowed so a down/erroring service never blocks the user from the product.

// Base URL of the user_onboarding service; unset → calls are skipped (no-op).
const BASE_URL = process.env.NEXT_PUBLIC_ONBOARDING_API_URL;

// POST a JSON body to the onboarding service with the Dograh auth token attached.
async function post(path: string, token: string, body: unknown): Promise<void> {
  if (!BASE_URL) return; // service not configured — skip silently
  try {
    await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    // Best-effort: PostHog already captured the event; never block the user.
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
