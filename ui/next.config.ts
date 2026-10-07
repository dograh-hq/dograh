import { withSentryConfig } from "@sentry/nextjs";
import type { NextConfig } from "next";

// Type-checking and linting inside `next build` are what push the Docker image
// build over its memory budget, so they are skipped *there* — and only there.
//
// This used to be unconditional, on the stated grounds that CI ran
// `tsc --noEmit` separately. It does not: no workflow in .github/workflows/
// type-checks the UI, so every other build silently accepted type errors too.
// Gating on the flag the Docker build sets means a local or CI `next build`
// fails on them again, which is the only enforcement that currently exists.
const skipBuildChecks = process.env.NEXT_SKIP_BUILD_CHECKS === '1';

const nextConfig: NextConfig = {
  /* config options here */
  output: 'standalone',
  typescript: {
    ignoreBuildErrors: skipBuildChecks,
  },
  eslint: {
    ignoreDuringBuilds: skipBuildChecks,
  },
  experimental: {
    serverSourceMaps: true,
    cpus: 1,
  },
  async rewrites() {
    return [
      {
        source: "/ingest/static/:path*",
        destination: "https://us-assets.i.posthog.com/static/:path*",
      },
      {
        source: "/ingest/:path*",
        destination: "https://us.i.posthog.com/:path*",
      },
      {
        source: "/ingest/decide",
        destination: "https://us.i.posthog.com/decide",
      },
    ];
  },
  // This is required to support PostHog trailing slash API requests
  skipTrailingSlashRedirect: true,
};

export default withSentryConfig(nextConfig, {
  // For all available options, see:
  // https://www.npmjs.com/package/@sentry/webpack-plugin#options

  org: "dograh",
  project: "javascript-nextjs",

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // For all available options, see:
  // https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/

  // Upload a larger set of source maps for prettier stack traces (increases build time)
  widenClientFileUpload: true,

  // Route browser requests to Sentry through a Next.js rewrite to circumvent ad-blockers.
  // This can increase your server load as well as your hosting bill.
  // Note: Check that the configured route will not match with your Next.js middleware, otherwise reporting of client-
  // side errors will fail.
  tunnelRoute: "/monitoring",

  webpack: {
    // Automatically tree-shake Sentry logger statements to reduce bundle size
    treeshake: {
      removeDebugLogging: true,
    },

    // Enables automatic instrumentation of Vercel Cron Monitors. (Does not yet work with App Router route handlers.)
    // See the following for more information:
    // https://docs.sentry.io/product/crons/
    // https://vercel.com/docs/cron-jobs
    automaticVercelMonitors: true,
  },
});
