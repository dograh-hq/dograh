import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    outbound_trunk: {
      enabled: true,
      name: "existing-trunk",
      ip: "sip.example.com",
      port: 5060,
      transport: "udp",
      prefix: "+",
      profile: {
        hostname: "border.example.com",
        authentication: {
          username: "carrier-user",
          password: "********password",
          overwrite_from: false,
        },
      },
    },
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
  });

  it("separates inbound and outbound details and keeps advanced fields hidden", () => {
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
    expect(screen.getByLabelText("Remote SIP Address")).toBeTruthy();
    expect(screen.getByLabelText("Remote SIP Port")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Transport" })).toBeTruthy();
    expect(screen.queryByLabelText("Technical Prefix")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Advanced" }));

    expect(screen.getByLabelText("Technical Prefix")).toBeTruthy();
    expect(screen.getByLabelText("Cloudonix Border Gateway")).toBeTruthy();
    expect(screen.getByText("SIP authentication")).toBeTruthy();
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
    fireEvent.change(screen.getByLabelText("Remote SIP Address"), {
      target: { value: "voice.example.net" },
    });
    fireEvent.change(screen.getByLabelText("Remote SIP Port"), {
      target: { value: "5080" },
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
          outbound_trunk: {
            enabled: true,
            name: "primary-carrier",
            ip: "voice.example.net",
            port: 5080,
            transport: "udp",
            prefix: "+",
            profile: {
              hostname: "border.example.com",
              authentication: {
                username: "carrier-user",
                password: "********password",
                overwrite_from: false,
              },
            },
          },
        },
      },
    });
    expect(onSaved).toHaveBeenCalledOnce();
    expect(onSaved).toHaveBeenCalledWith(configuration);
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
      mocks.updateConfiguration.mock.calls[0][0].body.config.outbound_trunk.enabled,
    ).toBe(false);
  });
});
