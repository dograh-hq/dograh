/**
 * Central white-label brand configuration.
 *
 * Every client-visible brand string/asset must come from here instead of
 * hardcoding the upstream "Dograh" branding. All values are driven by
 * NEXT_PUBLIC_* env vars (inlined at build time), so the deployment owner
 * can rebrand without code changes:
 *
 *   NEXT_PUBLIC_BRAND_NAME         - product name shown to clients (default: "VoiceAI Platform")
 *   NEXT_PUBLIC_BRAND_LOGO         - optional logo image URL/path (e.g. /brand-logo.png)
 *   NEXT_PUBLIC_BRAND_TAGLINE      - optional product tagline/description
 *   NEXT_PUBLIC_BRAND_DOCS_URL     - optional docs site base URL. Unset -> "Learn more"
 *                                    links fall back to the upstream public docs.
 *   NEXT_PUBLIC_BRAND_PRIVACY_URL  - optional privacy policy URL. Unset -> link hidden.
 *   NEXT_PUBLIC_BRAND_TERMS_URL    - optional terms of service URL. Unset -> link hidden.
 *   NEXT_PUBLIC_BRAND_COMMUNITY    - "true" to show upstream community links
 *                                    (GitHub star badge, Slack invite). Default hidden.
 *   NEXT_PUBLIC_CLIENT_MODE        - "true" to force the client-safe UI (hide model/provider/
 *                                    API-key/engine settings) for ALL users regardless of role.
 *
 * NOTE: process.env.NEXT_PUBLIC_* must be referenced statically (property
 * access on process.env) for Next.js to inline them — do not loop/index.
 */

export const BRAND = {
    name: process.env.NEXT_PUBLIC_BRAND_NAME || "VoiceAI Platform",
    logoUrl: process.env.NEXT_PUBLIC_BRAND_LOGO || "",
    tagline:
        process.env.NEXT_PUBLIC_BRAND_TAGLINE ||
        "Build and deploy AI voice agents",
    /** Docs base URL. Empty string -> doc links fall back to upstream docs. */
    docsUrl: process.env.NEXT_PUBLIC_BRAND_DOCS_URL || "",
    privacyUrl: process.env.NEXT_PUBLIC_BRAND_PRIVACY_URL || "",
    termsUrl: process.env.NEXT_PUBLIC_BRAND_TERMS_URL || "",
    /** Show upstream OSS community links (GitHub/Slack). Off by default. */
    showCommunityLinks: process.env.NEXT_PUBLIC_BRAND_COMMUNITY === "true",
} as const;

/**
 * When true, the UI behaves as a pure client deployment: all model/provider/
 * API-key/engine configuration surfaces are hidden for every user, even org
 * admins. Useful when a deployment is exclusively client-facing.
 */
export const CLIENT_MODE = process.env.NEXT_PUBLIC_CLIENT_MODE === "true";
