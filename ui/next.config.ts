import { withSentryConfig } from "@sentry/nextjs";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  output: 'standalone',
  // Pre-existing lint errors in fork pages must not block deploys (the
  // Docker image build does not lint-block either). Type checking still runs.
  eslint: { ignoreDuringBuilds: true },
  // serverSourceMaps is memory-heavy at build time and was OOM-killing the
  // Vercel builder. It's a debugging aid, not needed for production, so gate
  // it off (re-enable locally via ENABLE_SERVER_SOURCEMAPS=1 when debugging).
  experimental: {
    serverSourceMaps: process.env.ENABLE_SERVER_SOURCEMAPS === '1',
    // Next.js 15 build-memory reduction — trades some build speed for a much
    // lower webpack memory peak. Needed to keep the Vercel builder under its
    // container RAM limit (the compile was OOM/SIGKILL-ing without it).
    webpackMemoryOptimizations: true,
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
  async headers() {
    return [
      {
        // The avatar embed route must be framable on any site. Explicitly
        // allow framing (no X-Frame-Options) and permit any frame-ancestor.
        source: "/embed/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors *" },
        ],
      },
    ];
  },
  // This is required to support PostHog trailing slash API requests
  skipTrailingSlashRedirect: true,
};

// @spatialwalk/avatarkit is ESM-only; next.config.ts is loaded as CJS and the
// loader transpiles even dynamic import() to require(), so the import must be
// constructed at runtime where the transpiler can't rewrite it.
const importEsm = new Function("specifier", "return import(specifier)") as (
    specifier: string,
) => Promise<{ withAvatarkit: (config: NextConfig) => NextConfig }>;

export default async function config() {
    const { withAvatarkit } = await importEsm("@spatialwalk/avatarkit/next");
    const withAvatar = withAvatarkit(nextConfig);
    // The Sentry webpack plugin instruments every module and inflates build
    // memory — set DISABLE_SENTRY=1 to skip it on memory-constrained builders
    // (e.g. Vercel Hobby) where it was causing OOM (SIGKILL) kills.
    if (process.env.DISABLE_SENTRY === '1') {
        return withAvatar;
    }
    return sentryWrapped(withAvatar);
}

const sentryWrapped = (config: NextConfig) => withSentryConfig(config, {
  // For all available options, see:
  // https://www.npmjs.com/package/@sentry/webpack-plugin#options

  org: "dograh",
  project: "javascript-nextjs",

  // Only print logs for uploading source maps in CI
  silent: !process.env.CI,

  // For all available options, see:
  // https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/

  // Disabled: widening the uploaded source-map set inflates build memory and
  // was contributing to OOM kills on the Vercel builder. Standard maps still upload.
  widenClientFileUpload: false,

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
