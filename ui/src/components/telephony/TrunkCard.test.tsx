import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  PhoneNumberResponse,
  SipConnectivityDetails,
  TelephonyConfigurationDetail,
  TrunkResponse,
} from "@/client/types.gen";

import { TrunkCard } from "./TrunkCard";

const mocks = vi.hoisted(() => ({
  createTrunk: vi.fn(),
  updateTrunk: vi.fn(),
  deleteTrunk: vi.fn(),
}));

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

vi.mock("@/client", () => ({
  createTelephonyTrunkApiV1OrganizationsTelephonyConfigsConfigIdTrunksPost:
    mocks.createTrunk,
  updateTelephonyTrunkApiV1OrganizationsTelephonyConfigsConfigIdTrunksTrunkIdPut:
    mocks.updateTrunk,
  deleteTelephonyTrunkApiV1OrganizationsTelephonyConfigsConfigIdTrunksTrunkIdDelete:
    mocks.deleteTrunk,
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

// Deliberately not Global: the card's own default is Global, so a trunk that
// disagrees is the only way to catch settings leaking in from elsewhere.
const indiaTrunk: TrunkResponse = {
  id: 7,
  name: "india-carrier",
  enabled: true,
  settings: { region: "India", sip_domain: "sip.example.in" },
  phone_number_count: 1,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

function makeNumber(overrides: Partial<PhoneNumberResponse>): PhoneNumberResponse {
  return {
    id: 1,
    address: "+14155551234",
    address_type: "pstn",
    country_code: null,
    label: null,
    is_active: true,
    is_default_caller_id: false,
    inbound_workflow_id: null,
    inbound_workflow_name: null,
    telephony_trunk_id: null,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z",
    ...overrides,
  } as PhoneNumberResponse;
}

function makeConfiguration(
  trunks: TrunkResponse[] = [],
): TelephonyConfigurationDetail {
  return {
    id: 42,
    name: "Cloudonix production",
    provider: "cloudonix",
    is_default_outbound: true,
    credentials: { domain_id: "example.cloudonix.net" },
    sip_connectivity: details,
    supports_trunks: true,
    trunks,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z",
  };
}

function renderCard(
  configuration: TelephonyConfigurationDetail,
  phoneNumbers: PhoneNumberResponse[] = [],
  handlers: {
    onChanged?: () => void;
    onAddPhoneNumber?: (trunk: TrunkResponse) => void;
    onEditPhoneNumber?: (number: PhoneNumberResponse) => void;
  } = {},
) {
  return render(
    <TrunkCard
      configuration={configuration}
      phoneNumbers={phoneNumbers}
      onChanged={handlers.onChanged ?? vi.fn()}
      onAddPhoneNumber={handlers.onAddPhoneNumber ?? vi.fn()}
      onEditPhoneNumber={handlers.onEditPhoneNumber ?? vi.fn()}
    />,
  );
}

describe("TrunkCard", () => {
  beforeEach(() => {
    mocks.createTrunk.mockReset();
    mocks.createTrunk.mockResolvedValue({ data: indiaTrunk });
    mocks.updateTrunk.mockReset();
    mocks.updateTrunk.mockResolvedValue({ data: indiaTrunk });
    mocks.deleteTrunk.mockReset();
    mocks.deleteTrunk.mockResolvedValue({ data: { success: true } });
    vi.mocked(toast.error).mockClear();
  });

  it("invites the first trunk when there are none", () => {
    renderCard(makeConfiguration());

    expect(screen.getByText(/No trunks yet/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Add trunk/ })).toBeTruthy();
  });

  it("creates a trunk with the settings entered in the dialog", async () => {
    const onChanged = vi.fn();
    renderCard(makeConfiguration(), [], { onChanged });

    fireEvent.click(screen.getByRole("button", { name: /Add trunk/ }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "primary-carrier" },
    });
    fireEvent.change(screen.getByLabelText("SIP domain"), {
      target: { value: "voice.example.net" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save trunk" }));

    await waitFor(() => expect(mocks.createTrunk).toHaveBeenCalledOnce());
    expect(mocks.createTrunk).toHaveBeenCalledWith({
      path: { config_id: 42 },
      body: {
        name: "primary-carrier",
        enabled: true,
        settings: { region: "Global", sip_domain: "voice.example.net" },
      },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  });

  it("keeps a trunk's own region when it is edited", async () => {
    renderCard(makeConfiguration([indiaTrunk]), []);

    fireEvent.click(screen.getByRole("button", { name: "Edit trunk india-carrier" }));

    // The form is seeded from this trunk, not from any card-level default.
    expect(
      screen.getByRole("combobox", { name: "Trunk region" }).textContent,
    ).toContain("India");

    fireEvent.click(screen.getByRole("button", { name: "Save trunk" }));

    await waitFor(() => expect(mocks.updateTrunk).toHaveBeenCalledOnce());
    const call = mocks.updateTrunk.mock.calls[0][0];
    expect(call.path).toEqual({ config_id: 42, trunk_id: 7 });
    // Saving without touching the region must not silently move the trunk to
    // another region — that changes the remote peer Dograh dials.
    expect(call.body.settings).toEqual({
      region: "India",
      sip_domain: "sip.example.in",
    });
    expect(mocks.createTrunk).not.toHaveBeenCalled();
  });

  it("lists the numbers that dial out on each trunk", () => {
    const onAddPhoneNumber = vi.fn();
    renderCard(
      makeConfiguration([indiaTrunk]),
      [
        makeNumber({ id: 1, address: "+919000000001", telephony_trunk_id: 7 }),
        makeNumber({ id: 2, address: "+14155559999", telephony_trunk_id: null }),
      ],
      { onAddPhoneNumber },
    );

    expect(screen.getByText("+919000000001")).toBeTruthy();
    // A number on no trunk belongs to the flat table further down the page.
    expect(screen.queryByText("+14155559999")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Add number/ }));
    expect(onAddPhoneNumber).toHaveBeenCalledWith(indiaTrunk);
  });

  it("rejects a trunk name containing spaces before calling the API", async () => {
    renderCard(makeConfiguration());

    fireEvent.click(screen.getByRole("button", { name: /Add trunk/ }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "primary carrier" },
    });
    fireEvent.change(screen.getByLabelText("SIP domain"), {
      target: { value: "voice.example.net" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save trunk" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Trunk name may only contain letters, digits and hyphens",
      ),
    );
    expect(mocks.createTrunk).not.toHaveBeenCalled();
  });

  it("surfaces the refusal when a trunk still carries numbers", async () => {
    mocks.deleteTrunk.mockResolvedValue({
      error: {
        detail:
          "1 phone number(s) dial out over 'india-carrier'. Move them to another trunk before deleting it.",
      },
    });
    renderCard(makeConfiguration([indiaTrunk]));

    fireEvent.click(
      screen.getByRole("button", { name: "Delete trunk india-carrier" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("1 phone number(s)"),
      ),
    );
  });
});
