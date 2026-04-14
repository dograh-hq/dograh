import { NextResponse } from "next/server";

import { healthApiV1HealthGet } from "@/client/sdk.gen";
import type { HealthResponse } from "@/client/types.gen";

// Import version from package.json at build time
import packageJson from "../../../../../package.json";

export async function GET() {
  const uiVersion = packageJson.version || "dev";

  let apiVersion = "unknown";
  let deploymentMode = "oss";
  let authProvider = "local";

  try {
    const response = await healthApiV1HealthGet();
    if (response.data) {
      const data = response.data as HealthResponse;
      apiVersion = data.version;
      deploymentMode = data.deployment_mode;
      authProvider = data.auth_provider;
    }
  } catch {
    apiVersion = "unavailable";
  }

  return NextResponse.json({
    ui: uiVersion,
    api: apiVersion,
    deploymentMode,
    authProvider,
  });
}
