import { NextRequest, NextResponse } from "next/server";

import { getStackConfig } from "@/lib/auth/config";

/**
 * Helper route that receives a Stack refresh token via query parameters, wipes
 * every Stack SDK session cookie the browser presented, stores the impersonated
 * session as a fresh cookie and finally redirects to the requested path.
 *
 * On HTTPS Chrome the Stack SDK writes its cookies into the CHIPS-partitioned
 * jar (`Secure; SameSite=None; Partitioned`), which coexists with the regular
 * jar under the same cookie name. A Set-Cookie without the `Partitioned`
 * attribute can neither delete nor overwrite the partitioned copy, and the
 * SDK's own cookie parsing is first-occurrence-wins — so a leftover partitioned
 * cookie from a previous session silently keeps winning over anything this
 * route sets in the regular jar, resurfacing the old user. Deletions below are
 * therefore emitted for BOTH jars (and for possible parent-domain scopes),
 * which is also why headers are appended manually: `response.cookies.set`
 * dedupes Set-Cookie by name.
 *
 * The fresh cookie is deliberately written only ONCE, with the `Partitioned`
 * attribute: CHIPS browsers store it in the partitioned jar and non-CHIPS
 * browsers ignore the attribute and store it in the regular jar — in both
 * cases exactly the jar the SDK's own writes will later overwrite. Writing
 * both jars instead would plant a copy the SDK never updates, recreating the
 * stale-session bug this route exists to fix.
 *
 * Example usage (client side):
 *   /impersonate?refresh_token=<REFRESH>&redirect_path=/workflow/123
 */

// Stack SDK cookies that hold session identity: hexclave/stack access cookies
// and every refresh-cookie variant (bare legacy, project-scoped legacy,
// --default, --custom-<domain>, __Host- prefixed). Deliberately excludes
// non-identity SDK cookies (is-https flags, in-flight OAuth state) so an
// impersonation redirect can't abort an unrelated concurrent sign-in.
const SESSION_COOKIE_RE = /^(?:__Host-)?(?:stack|hexclave)-(?:access|refresh)(?:-|$)/;

/**
 * Domains a cookie could have been scoped to from this host, e.g.
 * "app.dograh.com" -> ["app.dograh.com", "dograh.com"]. Returns [] for
 * localhost / IP hosts. Stops before the last label, which over-generates for
 * multi-label public suffixes (app.example.co.uk also yields co.uk) — the
 * browser just rejects those deletions, so the cost is a wasted header.
 */
function parentDomains(hostname: string): string[] {
    if (!hostname.includes(".") || /^[\d.]+$/.test(hostname)) {
        return [];
    }
    const parts = hostname.split(".");
    const domains: string[] = [];
    for (let i = 0; i + 2 <= parts.length; i++) {
        domains.push(parts.slice(i).join("."));
    }
    return domains;
}

interface SetCookieAttrs {
    maxAge: number;
    secure?: boolean;
    domain?: string;
    partitioned?: boolean;
}

// No HttpOnly: the Stack SDK reads these cookies from document.cookie.
function serializeSetCookie(
    name: string,
    value: string,
    attrs: SetCookieAttrs,
): string {
    const parts = [
        `${name}=${encodeURIComponent(value)}`,
        "Path=/",
        `Max-Age=${attrs.maxAge}`,
    ];
    if (attrs.domain) {
        parts.push(`Domain=${attrs.domain}`);
    }
    if (attrs.partitioned) {
        // CHIPS requires Secure and SameSite=None.
        parts.push("Secure", "SameSite=None", "Partitioned");
    } else {
        if (attrs.secure) {
            parts.push("Secure");
        }
        parts.push("SameSite=Lax");
    }
    return parts.join("; ");
}

export async function GET(request: NextRequest) {
    const { searchParams } = new URL(request.url);

    const refreshToken = searchParams.get("refresh_token");
    const redirectPath = searchParams.get("redirect_path") ?? "/workflow/create";

    if (!refreshToken) {
        return new Response("Missing refresh_token", { status: 400 });
    }

    // The project id comes from the backend at runtime, so no inlined
    // NEXT_PUBLIC_* is needed.
    const stackConfig = await getStackConfig();
    if (!stackConfig) {
        return new Response("Stack auth is not configured", { status: 400 });
    }

    const fallbackRedirectUrl = new URL("/workflow/create", request.url);
    let redirectUrl = fallbackRedirectUrl.toString();
    try {
        const requestedRedirectUrl = new URL(redirectPath, request.url);
        if (requestedRedirectUrl.origin === request.nextUrl.origin) {
            redirectUrl = requestedRedirectUrl.toString();
        }
    } catch {
        // Malformed redirect_path (e.g. "https://") — keep the fallback.
    }

    const response = NextResponse.redirect(redirectUrl);

    const forwardedProto = request.headers
        .get("x-forwarded-proto")
        ?.split(",")[0]
        ?.trim()
        .toLowerCase();
    const isSecure =
        request.nextUrl.protocol === "https:" || forwardedProto === "https";

    // Every scope a stale SDK cookie may live in: host-only plus each parent
    // domain, each in the regular jar and (on https) its partitioned twin. The
    // request's Cookie header is the complete list of names to clear: the SDK
    // only sets Lax or None+Partitioned cookies, both of which the browser
    // attaches to this top-level navigation.
    const domains: (string | undefined)[] = [
        undefined,
        ...parentDomains(request.nextUrl.hostname),
    ];
    const jars = isSecure ? [false, true] : [false];

    const setCookieHeaders: string[] = [];
    for (const cookie of request.cookies.getAll()) {
        if (!SESSION_COOKIE_RE.test(cookie.name)) {
            continue;
        }
        const isHostPrefixed = cookie.name.startsWith("__Host-");
        for (const partitioned of jars) {
            for (const domain of domains) {
                if (isHostPrefixed && domain) {
                    continue; // __Host- cookies never have a Domain attribute
                }
                setCookieHeaders.push(
                    serializeSetCookie(cookie.name, "", {
                        maxAge: 0,
                        secure: isHostPrefixed || isSecure,
                        domain,
                        partitioned,
                    }),
                );
            }
        }
    }

    // Fresh impersonated session, written AFTER the deletions so it survives
    // them, in the name/shape Stack's nextjs-cookie token store reads. Single
    // write with Partitioned (see the header comment for why).
    const refreshCookieName = `${isSecure ? "__Host-" : ""}hexclave-refresh-${stackConfig.projectId}--default`;
    const refreshCookieValue = JSON.stringify({
        refresh_token: refreshToken,
        updated_at_millis: Date.now(),
    });
    setCookieHeaders.push(
        serializeSetCookie(refreshCookieName, refreshCookieValue, {
            maxAge: 60 * 60 * 24 * 365,
            secure: isSecure,
            partitioned: isSecure,
        }),
    );

    for (const header of setCookieHeaders) {
        response.headers.append("set-cookie", header);
    }

    return response;
}
