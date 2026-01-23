import { NextResponse } from "next/server";

import { healthApiV1HealthGet } from "@/client/sdk.gen";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

export async function GET() {
  const uiVersion = packageJson.version || "dev";

  // Fetch backend version from health endpoint
  let apiVersion = "unknown";
  try {
    const response = await healthApiV1HealthGet();
    if (response.data) {
      apiVersion = (response.data as { version: string }).version;
    }
  } catch {
    // Backend might not be reachable during build or in some deployments
    apiVersion = "unavailable";
  }

  return NextResponse.json({
    ui: uiVersion,
    api: apiVersion,
  });
}
