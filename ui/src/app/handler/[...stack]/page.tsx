import { StackHandler } from "@stackframe/stack";

import { getAuthProvider } from "@/lib/auth/config";

export default async function Handler(props: unknown) {
  const authProvider = await getAuthProvider();

  if (authProvider === "local") {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <h1>Local Auth Mode</h1>
        <p>Stack Auth handler is disabled when using local authentication.</p>
      </div>
    );
  }

  // Lazily import the real StackServerApp only when needed
  const { getStackServerApp } = await import("@/lib/auth/server");
  const app = await getStackServerApp();

  return <StackHandler
    fullPage
    app={app!}
    routeProps={props}
  />;
}
