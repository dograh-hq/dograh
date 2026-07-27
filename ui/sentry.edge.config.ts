// This file configures the initialization of Sentry for edge features (middleware, edge routes, and so on).
// The config you add here will be used whenever one of the edge features is loaded.
// Note that this config is unrelated to the Vercel Edge Runtime and is also required when running locally.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

// Only initialize support diagnostics if enabled and a DSN is provided.
const supportDiagnosticsSetting =
  process.env.ENABLE_SUPPORT_DIAGNOSTICS ??
  process.env.ENABLE_TELEMETRY ??
  'true';
const supportDiagnosticsEnabled =
  supportDiagnosticsSetting.toLowerCase() === 'true';
const sentryDsn = process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN;

if (supportDiagnosticsEnabled && sentryDsn) {
  Sentry.init({
    dsn: sentryDsn,

    // Setting this option to true will print useful information to the console while you're setting up Sentry.
    debug: false,
    enabled: true
  });
  console.log('Support diagnostics initialized for edge runtime error tracking');
} else {
  console.log('Support diagnostics disabled on edge runtime (ENABLE_SUPPORT_DIAGNOSTICS=false or DSN not configured)');
}
