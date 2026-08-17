import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TelephonyProviderMetadata } from "@/client/types.gen";

import { ConfigFormDialog } from "./ConfigFormDialog";

const mocks = vi.hoisted(() => ({
  getAccessToken: vi.fn(),
  getProviders: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
  getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet:
    mocks.getProviders,
  createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost: vi.fn(),
  updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: "user-1" }, getAccessToken: mocks.getAccessToken }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const papiProvider = {
  provider: "papi_voip",
  display_name: "Papi Voip",
  fields: [
    {
      name: "api_key",
      label: "Instance API Key",
      type: "password",
      required: true,
      sensitive: true,
      description: "The API key for this WhatsApp instance, not the PAPI Cloud admin token.",
    },
    {
      name: "instance_id",
      label: "Instance ID",
      type: "text",
      required: true,
      sensitive: false,
      description: "WhatsApp Instance ID enabled with Voice/Voip",
    },
  ],
} satisfies TelephonyProviderMetadata;

describe("ConfigFormDialog", () => {
  beforeEach(() => {
    mocks.getAccessToken.mockResolvedValue("access-token");
    mocks.getProviders.mockResolvedValue({ data: { providers: [papiProvider] } });
  });

  it("shows the PAPI onboarding details without exposing the base URL", async () => {
    render(<ConfigFormDialog open onOpenChange={vi.fn()} onSaved={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("Get a WhatsApp VoIP number")).toBeTruthy();
    });

    expect(screen.getByText("Need help? Visit papi.api.br")).toBeTruthy();
    expect(screen.getByLabelText("Instance API Key")).toBeTruthy();
    expect(screen.queryByLabelText("API Base URL")).toBeNull();
    expect(screen.getByAltText("PAPI")).toBeTruthy();
  });
});
