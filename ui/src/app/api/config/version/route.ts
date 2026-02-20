import { NextResponse } from "next/server";

import { healthApiV1HealthGet } from "@/client/sdk.gen";
import type { HealthResponse } from "@/client/types.gen";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

// Internal/local URLs that are not reachable from the browser
const INTERNAL_HOST_RE = /^https?:\/\/(localhost|127\.0\.0\.1|api)(:\d+)?(\/|$)/;

function isInternalUrl(url: string | undefined | null): boolean {
  return !url || INTERNAL_HOST_RE.test(url);
}

export async function GET() {
  const uiVersion = packageJson.version || "dev";

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

  // For the API client base URL: prefer BACKEND_URL env, fall back to
  // health endpoint value. Skip internal/Docker-only URLs (e.g. http://api:8000)
  // that aren't reachable from the browser — the client will keep using
  // window.location.origin via the Next.js proxy instead.
  const clientCandidate = process.env.BACKEND_URL || backendApiEndpoint;
  const clientApiBaseUrl = isInternalUrl(clientCandidate) ? 'http://localhost:8000' : clientCandidate;

  return NextResponse.json({
    ui: uiVersion,
    api: apiVersion,
    backendApiEndpoint,
    clientApiBaseUrl,
    deploymentMode,
    authProvider,
  });
}
