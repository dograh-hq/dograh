import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SipConnectivityDetails } from "@/client/types.gen";

import { SipConnectivityCard } from "./SipConnectivityCard";

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

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
        {
          transport: "TCP",
          hostname: "domain.in.dimi.tel",
          port: 9060,
          uri: "domain.in.dimi.tel:9060",
        },
      ],
      outbound_origin_ip: "128.199.27.19",
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

describe("SipConnectivityCard", () => {
  it("keeps the endpoints folded away until asked for", () => {
    render(<SipConnectivityCard details={details} />);

    expect(screen.queryByRole("heading", { name: "Inbound" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "View details" }));

    expect(screen.getByRole("heading", { name: "Inbound" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Outbound" })).toBeTruthy();
  });

  it("shows the Global region's endpoints by default", () => {
    render(<SipConnectivityCard details={details} defaultOpen />);

    expect(
      screen.getByRole("combobox", { name: "SIP region" }).textContent,
    ).toContain("Global");
    expect(screen.getByText("domain.sip.cloudonix.net")).toBeTruthy();
    expect(screen.getByText("203.0.113.10")).toBeTruthy();
  });

  it("collapses transports that share a port into one badge", () => {
    const indiaOnly = { ...details, regions: details.regions.slice(0, 1) };
    render(<SipConnectivityCard details={indiaOnly} defaultOpen />);

    // UDP and TCP both answer on 9060, so that is one endpoint, not two rows.
    expect(screen.getByText("UDP/TCP 9060")).toBeTruthy();
  });

  it("no longer edits trunks — those live in their own card", () => {
    render(<SipConnectivityCard details={details} defaultOpen />);

    expect(screen.queryByLabelText("SIP domain")).toBeNull();
    expect(screen.queryByRole("button", { name: /trunk/i })).toBeNull();
  });
});
