import { NextRequest, NextResponse } from "next/server";

/**
 * Helper route that receives a refresh token via query parameters, stores it as
 * the regular Stack cookie *for the current sub-domain only* and finally
 * redirects the user to the requested path.
 *
 * Example usage (client side):
 *   /impersonate?refresh_token=<TOKEN>&redirect_path=/workflow/123
 */
export async function GET(request: NextRequest) {
    const { searchParams } = new URL(request.url);

    const refreshToken = searchParams.get("refresh_token");
    const redirectPath = searchParams.get("redirect_path") ?? "/workflow/create";

    if (!refreshToken) {
        return new Response("Missing refresh_token", { status: 400 });
    }

    // Prepare redirect – if the supplied redirect path is an absolute URL we use
    // it as-is, otherwise we resolve it relative to the current request.
    const redirectUrl = redirectPath.startsWith("http")
        ? redirectPath
        : new URL(redirectPath, request.url).toString();

    const response = NextResponse.redirect(redirectUrl);

    // One day in seconds
    const maxAge = 60 * 60 * 24;

    // Store the refresh token cookie without an explicit domain so that it is
    // scoped to the current (sub-)domain. This avoids collisions between the
    // admin (superadmin.*) and the regular app (app.*) domains.
    response.cookies.set(`stack-refresh-${process.env.NEXT_PUBLIC_STACK_PROJECT_ID}` as string, refreshToken, {
        path: "/",
        maxAge,
        secure: true,
        httpOnly: false, // Must be accessible from the browser for Stack SDK
        sameSite: "lax",
    });

    return response;
}
