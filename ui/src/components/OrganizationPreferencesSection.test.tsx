import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OrganizationPreferences } from "@/client/types.gen";

import { OrganizationPreferencesSection } from "./OrganizationPreferencesSection";

const mocks = vi.hoisted(() => ({
  getPreferences: vi.fn(),
  savePreferences: vi.fn(),
  refreshConfig: vi.fn(),
  systemCodes: ["do_not_call"],
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
  getPreferencesApiV1OrganizationsPreferencesGet: mocks.getPreferences,
  savePreferencesApiV1OrganizationsPreferencesPut: mocks.savePreferences,
}));

vi.mock("@/context/UserConfigContext", () => ({
  useUserConfig: () => ({ refreshConfig: mocks.refreshConfig }),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div role="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));

vi.mock("@/hooks/useDispositionCodes", () => ({
  useDispositionCodes: () => ({
    codes: mocks.systemCodes,
    endTaskReasonCodes: [],
    systemCodes: mocks.systemCodes,
    isLoading: false,
  }),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: 1 }, loading: false }),
}));

vi.mock("react-timezone-select", () => ({
  default: () => <div data-testid="timezone-select" />,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const preferences: OrganizationPreferences = {
  test_phone_number: null,
  timezone: "UTC",
  external_pbx_integrations_enabled: false,
  disposition_mapping_enabled: true,
  disposition_mapping: {},
};

describe("OrganizationPreferencesSection disposition mapping", () => {
  beforeEach(() => {
    mocks.getPreferences.mockReset();
    mocks.getPreferences.mockResolvedValue({ data: preferences });
    mocks.savePreferences.mockReset();
    mocks.savePreferences.mockImplementation(async ({ body }) => ({
      data: body,
    }));
    mocks.refreshConfig.mockReset();
    mocks.refreshConfig.mockResolvedValue(undefined);
    vi.mocked(toast.success).mockClear();
    vi.mocked(toast.error).mockClear();
  });

  it("persists a mapping from the modal without another page-level save", async () => {
    render(<OrganizationPreferencesSection />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Configure mapping" }),
    );
    fireEvent.change(screen.getByLabelText("Code for do_not_call"), {
      target: { value: "DNC" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save mapping" }));

    await waitFor(() => expect(mocks.savePreferences).toHaveBeenCalledOnce());
    expect(mocks.savePreferences).toHaveBeenCalledWith({
      body: {
        test_phone_number: null,
        timezone: "UTC",
        external_pbx_integrations_enabled: false,
        disposition_mapping_enabled: true,
        disposition_mapping: { do_not_call: "DNC" },
      },
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Disposition mapping saved"),
    );
    expect(
      screen.queryByRole("heading", { name: "Configure disposition mapping" }),
    ).toBeNull();
  });

  it("keeps the modal open when persistence fails", async () => {
    mocks.savePreferences.mockResolvedValue({
      error: { detail: "Could not save mapping" },
    });
    render(<OrganizationPreferencesSection />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Configure mapping" }),
    );
    fireEvent.change(screen.getByLabelText("Code for do_not_call"), {
      target: { value: "DNC" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save mapping" }));

    await waitFor(() => expect(mocks.savePreferences).toHaveBeenCalledOnce());
    expect(
      await screen.findByRole("button", { name: "Save mapping" }),
    ).toBeTruthy();
    expect(toast.error).toHaveBeenCalled();
  });
});
