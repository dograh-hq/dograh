// This file configures the initialization of Sentry on the server.
// The config you add here will be used whenever the server handles a request.
// https://docs.sentry.io/platforms/javascript/guides/nextjs/

import * as Sentry from "@sentry/nextjs";

// Only initialize Sentry if explicitly enabled and DSN is provided
const enableSentry = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (enableSentry) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,

    // Setting this option to true will print useful information to the console while you're setting up Sentry.
    debug: false,
    enabled: process.env.NEXT_PUBLIC_NODE_ENV === 'production'
  });
  console.log('Sentry initialized for server-side error tracking');
} else {
  console.log('Sentry disabled on server (NEXT_PUBLIC_ENABLE_SENTRY=false or DSN not configured)');
}
