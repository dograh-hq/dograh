import { NextResponse } from "next/server";

import type { HealthResponse } from "@/client/types.gen";
import { getServerBackendUrl } from "@/lib/apiClient";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

const HEALTHCHECK_TIMEOUT_MS = 3000;

function trimTrailingSlash(url: string) {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function getHealthcheckFailureMessage(error: unknown, backendUrl: string) {
  const errorName =
    error && typeof error === "object" && "name" in error
      ? String((error as { name?: unknown }).name)
      : "";

  if (errorName === "AbortError" || errorName === "TimeoutError") {
    return `Backend health check timed out after ${HEALTHCHECK_TIMEOUT_MS}ms while trying to reach ${backendUrl}.`;
  }

  return `Backend is not reachable at ${backendUrl}.`;
}

export async function GET() {
  const uiVersion = packageJson.version || "dev";
  const backendUrl = trimTrailingSlash(getServerBackendUrl());
  const healthcheckUrl = `${backendUrl}/api/v1/health`;

  let apiVersion = "unknown";
  let deploymentMode = "oss";
  let authProvider = "local";
  let turnEnabled = false;
  let forceTurnRelay = false;
  let tunnelUrl: string | null = null;
  let backendApiEndpoint: string | null = null;
  let backendStatus: "reachable" | "unreachable" = "unreachable";
  let backendMessage: string | null = `Backend is not reachable at ${backendUrl}.`;

  try {
    const response = await fetch(healthcheckUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(HEALTHCHECK_TIMEOUT_MS),
    });

    if (!response.ok) {
      backendMessage = `Backend health check at ${healthcheckUrl} returned HTTP ${response.status}.`;
    } else {
      const data = (await response.json()) as HealthResponse;
      apiVersion = data.version;
      deploymentMode = data.deployment_mode;
      authProvider = data.auth_provider;
      turnEnabled = Boolean(data.turn_enabled);
      forceTurnRelay = Boolean(data.force_turn_relay);
      tunnelUrl = data.tunnel_url ?? null;
      // A localhost / 127.0.0.1 endpoint is the server's own loopback and is
      // NOT reachable from a browser. Treat it as unset so the client falls
      // back to same-origin (which reaches the backend via the reverse proxy).
      // Without this, a deployment where BACKEND_API_ENDPOINT defaults to
      // http://localhost:8000 breaks browser login and avatar loads.
      const rawEndpoint =
        typeof data.backend_api_endpoint === "string"
          ? data.backend_api_endpoint.trim()
          : "";
      const isLoopbackEndpoint = /^https?:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/i.test(
        rawEndpoint,
      );
      backendApiEndpoint =
        rawEndpoint.length > 0 && !isLoopbackEndpoint
          ? trimTrailingSlash(rawEndpoint)
          : null;
      backendStatus = "reachable";
      backendMessage = null;
    }
  } catch (error) {
    apiVersion = "unavailable";
    backendMessage = getHealthcheckFailureMessage(error, backendUrl);
  }

  return NextResponse.json({
    ui: uiVersion,
    api: apiVersion,
    deploymentMode,
    authProvider,
    turnEnabled,
    forceTurnRelay,
    tunnelUrl,
    backendApiEndpoint,
    backend: {
      status: backendStatus,
      url: backendUrl,
      healthcheckUrl,
      message: backendMessage,
    },
  });
}
