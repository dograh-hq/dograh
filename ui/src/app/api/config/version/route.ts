import { NextResponse } from "next/server";

import { healthApiV1HealthGet } from "@/client/sdk.gen";
import type { HealthResponse } from "@/client/types.gen";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

export async function GET() {
  const uiVersion = packageJson.version || "dev";

  // Fetch backend version and config from health endpoint
  let apiVersion = "unknown";
  let backendApiEndpoint: string | null = null;

  try {
    const response = await healthApiV1HealthGet();
    if (response.data) {
      const data = response.data as HealthResponse;
      apiVersion = data.version;
      backendApiEndpoint = data.backend_api_endpoint;
    }
  } catch {
    // Backend might not be reachable during build or in some deployments
    apiVersion = "unavailable";
  }

  return NextResponse.json({
    ui: uiVersion,
    api: apiVersion,
    backendApiEndpoint,
  });
}
