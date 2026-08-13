import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  SipConnectivityDetails,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";

import { SipConnectivityCard } from "./SipConnectivityCard";

const mocks = vi.hoisted(() => ({
  getAccessToken: vi.fn(),
  updateConfiguration: vi.fn(),
}));

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

vi.mock("@/client/sdk.gen", () => ({
  updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut:
    mocks.updateConfiguration,
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ getAccessToken: mocks.getAccessToken }),
}));

vi.mock("@/lib/clipboard", () => ({
  copyTextToClipboard: vi.fn(async () => undefined),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const details: SipConnectivityDetails = {
  provider_display_name: "Cloudonix",
  regions: [
    {
      region: "India",
      inbound_transports: [
        {
          transport: "UDP",
          hostname: "domain.in.dimi.tel",
          port: 9060,
          uri: "domain.in.dimi.tel:9060",
        },
      ],
      outbound_origin_ip: "128.199.27.19",
    },
    {
      region: "UAE",
      inbound_transports: [
        {
          transport: "UDP",
          hostname: "domain.uae.dimi.tel",
          port: 9081,
          uri: "domain.uae.dimi.tel:9081",
        },
      ],
      outbound_origin_ip: "20.233.60.70",
    },
    {
      region: "Global",
      inbound_transports: [
        {
          transport: "UDP",
          hostname: "domain.sip.cloudonix.net",
          port: 5060,
          uri: "domain.sip.cloudonix.net:5060",
        },
      ],
      outbound_origin_ip: "203.0.113.10",
    },
  ],
};

const configuration: TelephonyConfigurationDetail = {
  id: 42,
  name: "Cloudonix production",
  provider: "cloudonix",
  is_default_outbound: true,
  credentials: {
    bearer_token: "********token",
    domain_id: "example.cloudonix.net",
    application_name: "dograh-app",
    outbound_trunks: [
      {
        id: "trunk-1",
        enabled: true,
        name: "existing-trunk",
        region: "Global",
        sip_domain: "sip.example.com",
      },
    ],
  },
  sip_connectivity: details,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

describe("SipConnectivityCard", () => {
  beforeEach(() => {
    mocks.getAccessToken.mockReset();
    mocks.getAccessToken.mockResolvedValue("access-token");
    mocks.updateConfiguration.mockReset();
    mocks.updateConfiguration.mockResolvedValue({ data: configuration });
    vi.mocked(toast.error).mockClear();
  });

  it("shows one inbound endpoint and asks only for the two trunk fields", () => {
    render(
      <SipConnectivityCard
        details={details}
        configuration={configuration}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.queryByRole("heading", { name: "Inbound" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "View details" }));

    expect(screen.getByRole("heading", { name: "Inbound" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Outbound" })).toBeTruthy();
    expect(screen.getByText("domain.sip.cloudonix.net")).toBeTruthy();
    expect(screen.getByText("203.0.113.10")).toBeTruthy();
    expect(
      screen.getByRole("combobox", { name: "SIP region" }).textContent,
    ).toContain("Global");
    expect(
      screen.getByRole("switch", { name: "Enable outbound trunk" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("Trunk Name")).toBeTruthy();
    expect(screen.getByLabelText("SIP Domain")).toBeTruthy();

    // Everything below is derived from the region, so it is not asked for.
    expect(screen.queryByLabelText("Remote SIP Address")).toBeNull();
    expect(screen.queryByLabelText("Remote SIP Port")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Transport" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Advanced" })).toBeNull();
    expect(screen.queryByText("SIP authentication")).toBeNull();
  });

  it("saves the outbound trunk from the SIP connectivity panel", async () => {
    const onSaved = vi.fn();
    render(
      <SipConnectivityCard
        details={details}
        configuration={configuration}
        onSaved={onSaved}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View details" }));

    fireEvent.change(screen.getByLabelText("Trunk Name"), {
      target: { value: "primary-carrier" },
    });
    fireEvent.change(screen.getByLabelText("SIP Domain"), {
      target: { value: "voice.example.net" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save outbound trunk" }));

    await waitFor(() => expect(mocks.updateConfiguration).toHaveBeenCalledOnce());
    expect(mocks.updateConfiguration).toHaveBeenCalledWith({
      headers: { Authorization: "Bearer access-token" },
      path: { config_id: 42 },
      body: {
        config: {
          provider: "cloudonix",
          bearer_token: "********token",
          domain_id: "example.cloudonix.net",
          application_name: "dograh-app",
          // A list so more trunks can be added later; this form owns the
          // first one and round-trips its Dograh id.
          outbound_trunks: [
            {
              id: "trunk-1",
              enabled: true,
              name: "primary-carrier",
              region: "Global",
              sip_domain: "voice.example.net",
            },
          ],
        },
      },
    });
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onSaved).toHaveBeenCalledWith(configuration);
  });

  it("rejects a trunk name containing spaces before calling the API", async () => {
    render(
      <SipConnectivityCard
        details={details}
        configuration={configuration}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View details" }));

    fireEvent.change(screen.getByLabelText("Trunk Name"), {
      target: { value: "primary carrier" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save outbound trunk" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Trunk name may only contain letters, digits and hyphens",
      ),
    );
    expect(mocks.updateConfiguration).not.toHaveBeenCalled();
  });

  it("persists the outbound trunk toggle", async () => {
    render(
      <SipConnectivityCard
        details={details}
        configuration={configuration}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View details" }));

    fireEvent.click(screen.getByRole("switch", { name: "Enable outbound trunk" }));
    fireEvent.click(screen.getByRole("button", { name: "Save outbound trunk" }));

    await waitFor(() => expect(mocks.updateConfiguration).toHaveBeenCalledOnce());
    expect(
      mocks.updateConfiguration.mock.calls[0][0].body.config.outbound_trunks[0]
        .enabled,
    ).toBe(false);
  });
});
