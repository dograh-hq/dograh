import { StackHandler, StackTheme } from "@stackframe/stack";

import { getAuthProvider } from "@/lib/auth/config";

import { AuthEnterpriseCTA } from "./AuthEnterpriseCTA";
import { AuthShell } from "./AuthShell";
import { BackButton } from "./BackButton";
import { stackAuthDarkTheme } from "./stack-theme";

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

  return (
    <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
      <BackButton />
      <StackTheme theme={stackAuthDarkTheme}>
        <StackHandler fullPage={false} app={app!} routeProps={props} />
      </StackTheme>
    </AuthShell>
  );
}
