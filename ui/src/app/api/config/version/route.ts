import { NextRequest, NextResponse } from "next/server";

import { healthApiV1HealthGet } from "@/client/sdk.gen";
import type { HealthResponse } from "@/client/types.gen";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

// Internal/local URLs that are not reachable from the browser.
const INTERNAL_HOST_RE = /^https?:\/\/(localhost|127\.0\.0\.1|api)(:\d+)?(\/|$)/;

function isInternalUrl(url: string | undefined | null): boolean {
  return !url || INTERNAL_HOST_RE.test(url);
}

export async function GET(request: NextRequest) {
  const uiVersion = packageJson.version || "dev";
  const browserOrigin = request.nextUrl.origin;

  // Fetch backend version and config from health endpoint
  let apiVersion = "unknown";
  let backendApiEndpoint: string | null = null;
  let deploymentMode = "oss";
  let authProvider = "local";

  try {
    const response = await healthApiV1HealthGet();
    if (response.data) {
      const data = response.data as HealthResponse;
      apiVersion = data.version;
      // Pass through the backend's own endpoint for display purposes
      backendApiEndpoint = data.backend_api_endpoint;
      deploymentMode = data.deployment_mode;
      authProvider = data.auth_provider;
    }
  } catch {
    // Backend might not be reachable during build or in some deployments
    apiVersion = "unavailable";
  }

  // Browser-facing URLs must always be reachable from the public origin.
  // If the backend health endpoint is unavailable or only advertises an
  // internal service name, keep the browser on the public app origin instead
  // of leaking localhost/internal cluster addresses into the client SDK.
  const configuredBackendUrl = process.env.BACKEND_URL;
  const clientCandidate = !isInternalUrl(configuredBackendUrl)
    ? configuredBackendUrl
    : backendApiEndpoint;
  const clientApiBaseUrl = isInternalUrl(clientCandidate) ? browserOrigin : clientCandidate;

  return NextResponse.json(
    {
      ui: uiVersion,
      api: apiVersion,
      backendApiEndpoint,
      clientApiBaseUrl,
      deploymentMode,
      authProvider,
    },
    {
      headers: {
        "Cache-Control": "no-store, max-age=0",
      },
    }
  );
}
