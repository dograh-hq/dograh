import { NextRequest, NextResponse } from "next/server";

import { getStackConfig } from "@/lib/auth/config";

/**
 * Helper route that receives a Stack refresh token via query parameters, stores
 * it as the regular Stack SDK cookie *for the current sub-domain only* and finally
 * redirects the user to the requested path.
 *
 * Example usage (client side):
 *   /impersonate?refresh_token=<REFRESH>&redirect_path=/workflow/123
 */
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

    const requestedRedirectUrl = new URL(redirectPath, request.url);
    const fallbackRedirectUrl = new URL("/workflow/create", request.url);
    const redirectUrl =
        requestedRedirectUrl.origin === request.nextUrl.origin
            ? requestedRedirectUrl.toString()
            : fallbackRedirectUrl.toString();

    const response = NextResponse.redirect(redirectUrl);

    const isSecure =
        request.nextUrl.protocol === "https:" ||
        request.headers.get("x-forwarded-proto") === "https";
    const refreshCookieName = `${isSecure ? "__Host-" : ""}hexclave-refresh-${stackConfig.projectId}--default`;
    const accessCookieName = "hexclave-access";
    const refreshMaxAge = 60 * 60 * 24 * 365;

    // Store the refresh token using the cookie name/shape Stack's current
    // nextjs-cookie token store reads. The old route only set the legacy
    // refresh cookie, which let a stale access cookie keep the browser on the
    // previous app-domain session.
    response.cookies.set(
        refreshCookieName,
        JSON.stringify({
            refresh_token: refreshToken,
            updated_at_millis: Date.now(),
        }),
        {
            path: "/",
            maxAge: refreshMaxAge,
            secure: isSecure,
            httpOnly: false, // Must be accessible from the browser for Stack SDK
            sameSite: "lax",
        },
    );

    const staleCookieNames = new Set([
        accessCookieName,
        `stack-refresh-${stackConfig.projectId}`,
        "stack-refresh",
        `stack-refresh-${stackConfig.projectId}--default`,
        `__Host-stack-refresh-${stackConfig.projectId}--default`,
        `hexclave-refresh-${stackConfig.projectId}--default`,
        `__Host-hexclave-refresh-${stackConfig.projectId}--default`,
        "stack-access",
    ]);
    staleCookieNames.delete(refreshCookieName);

    for (const cookie of request.cookies.getAll()) {
        const name = cookie.name;
        if (name !== refreshCookieName && (
            name.startsWith(`hexclave-refresh-${stackConfig.projectId}--custom-`) ||
            name.startsWith(`stack-refresh-${stackConfig.projectId}--custom-`) ||
            name.startsWith(`__Host-hexclave-refresh-${stackConfig.projectId}--`) ||
            name.startsWith(`__Host-stack-refresh-${stackConfig.projectId}--`)
        )) {
            staleCookieNames.add(name);
        }
    }

    for (const name of staleCookieNames) {
        response.cookies.set(name, "", {
            path: "/",
            maxAge: 0,
            secure: name.startsWith("__Host-") || isSecure,
            httpOnly: false,
            sameSite: "lax",
        });
    }

    // Keep writing the legacy project refresh cookie for compatibility with
    // older Stack SDK builds, but the structured Hexclave cookies above are the
    // source of truth for the current app.
    response.cookies.set(`stack-refresh-${stackConfig.projectId}`, refreshToken, {
        path: "/",
        maxAge: refreshMaxAge,
        secure: isSecure,
        httpOnly: false, // Must be accessible from the browser for Stack SDK
        sameSite: "lax",
    });

    return response;
}
