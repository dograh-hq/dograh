/**
 * Public base URL that external callers (telephony providers, webhook senders)
 * should use to reach this deployment's API.
 *
 * Prefers the Cloudflare tunnel URL the backend reports via /health (set when the
 * host has no public IP, so `window.location.origin` would be a private/LAN
 * address an external caller can't reach), then a build-time configured backend
 * URL, then the current origin (correct for a same-origin public deployment).
 */
export function resolveWebhookBaseUrl(tunnelUrl?: string | null): string {
  return (
    tunnelUrl ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    (typeof window !== "undefined" ? window.location.origin : "")
  );
}
