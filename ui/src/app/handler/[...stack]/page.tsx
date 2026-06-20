import { StackHandler, StackTheme } from "@stackframe/stack";

import { AuthEnterpriseCTA } from "@/components/auth/AuthEnterpriseCTA";
import { AuthShell } from "@/components/auth/AuthShell";
import { getAuthProvider } from "@/lib/auth/config";

import { BackButton } from "./BackButton";
import { stackAuthDarkTheme } from "./stack-theme";

// Stack Auth serves every auth page from this one catch-all. We give the brand
// split-screen shell to the user-facing FORM routes and render only the wide /
// interstitial "machine" routes full-page (so account-settings etc. aren't
// cramped into the narrow auth card). This is a BLOCKLIST, not an allowlist, so
// new or aliased form routes — Stack's `log-in`/`register` aliases, case/dash
// variants, email-verification, mfa, team-invitation — get the shell by default.
// Matching is normalized (lowercase, dashes stripped) to mirror Stack's own
// case- and dash-insensitive route resolution.
const FULL_PAGE_ROUTES = new Set([
  "accountsettings",
  "oauthcallback",
  "magiclinkcallback",
  "signout",
  "error",
]);

export default async function Handler(props: unknown) {
  const authProvider = await getAuthProvider();

  if (authProvider === "local") {
    return (
      <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
        <div className="space-y-2 text-center text-zinc-200">
          <h1 className="text-xl font-semibold">Local Auth Mode</h1>
          <p className="text-sm text-muted-foreground">
            Stack Auth handler is disabled when using local authentication.
          </p>
        </div>
      </AuthShell>
    );
  }

  // Lazily import the real StackServerApp only when needed
  const { getStackServerApp } = await import("@/lib/auth/server");
  const app = await getStackServerApp();

  // Resolve the first route segment to decide layout. `params` is async in
  // Next 15; awaiting it here does not consume it for StackHandler below.
  let segment = "";
  try {
    const { params } = props as { params?: Promise<{ stack?: string[] }> };
    const resolved = params ? await params : undefined;
    segment = resolved?.stack?.[0] ?? "";
  } catch {
    segment = "";
  }
  const normalizedSegment = segment.toLowerCase().replace(/-/g, "");
  const isAuthForm = segment !== "" && !FULL_PAGE_ROUTES.has(normalizedSegment);
  const showBackButton = !new Set(["signin", "login"]).has(normalizedSegment);

  const handler = (
    <StackTheme theme={stackAuthDarkTheme}>
      <StackHandler fullPage={!isAuthForm} app={app!} routeProps={props} />
    </StackTheme>
  );

  if (isAuthForm) {
    return (
      <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
        {showBackButton && <BackButton />}
        {handler}
      </AuthShell>
    );
  }

  // account-settings and machine routes render full-page (Stack's own layout).
  return handler;
}
