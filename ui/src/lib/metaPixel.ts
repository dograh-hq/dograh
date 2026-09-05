/**
 * Meta (Facebook) Pixel — thin, guarded tracking seam.
 *
 * This repo calls PostHog directly (see constants/posthog-events.ts); there is no
 * central analytics wrapper. We mirror that convention: call these helpers at the
 * same sites we already `posthog.capture(...)`.
 *
 * Every helper is a no-op unless the Pixel is actually loaded (i.e. `window.fbq`
 * exists). The Pixel only loads on Cloud, where NEXT_PUBLIC_META_PIXEL_ID is set
 * at build time (see components/MetaPixel.tsx + app/layout.tsx). OSS self-hosters
 * and local dev leave the var blank, so `fbq` never loads and nothing is sent.
 */

declare global {
  interface Window {
    fbq?: (...args: unknown[]) => void;
  }
}

type MetaEventParams = Record<string, unknown>;

/** Fire a Meta standard event, guarded on the Pixel being loaded. */
function metaTrack(event: string, params?: MetaEventParams): void {
  if (typeof window === "undefined" || typeof window.fbq !== "function") return;
  if (params) {
    window.fbq("track", event, params);
  } else {
    window.fbq("track", event);
  }
}

export function trackMetaPageView(): void {
  metaTrack("PageView");
}

export function trackMetaCompleteRegistration(params?: MetaEventParams): void {
  metaTrack("CompleteRegistration", params);
}

export function trackMetaLead(params?: MetaEventParams): void {
  metaTrack("Lead", params);
}

export function trackMetaInitiateCheckout(params?: MetaEventParams): void {
  metaTrack("InitiateCheckout", params);
}
