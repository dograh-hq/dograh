"use client";

import * as Sentry from "@sentry/nextjs";
import { ReactNode } from "react";

// Wraps the app tree so render errors are caught with React's componentStack
// attached. Sentry's LinkedErrors integration picks up the componentStack from
// error.cause, so events arrive in Sentry tagged with the component path
// instead of collapsing into opaque React-internal frames.
export function SentryErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <Sentry.ErrorBoundary
      fallback={({ error, resetError }) => (
        <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-6 text-center">
          <h1 className="text-2xl font-semibold">Something went wrong</h1>
          <p className="text-sm text-muted-foreground max-w-md">
            {error instanceof Error ? error.message : String(error)}
          </p>
          <button
            onClick={resetError}
            className="px-4 py-2 rounded-md border bg-background hover:bg-accent text-sm"
          >
            Try again
          </button>
        </div>
      )}
    >
      {children}
    </Sentry.ErrorBoundary>
  );
}
