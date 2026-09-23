import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CallEventsSection } from "./CallEventsSection";

const mocks = vi.hoisted(() => ({
  get: vi.fn(), save: vi.fn(), test: vi.fn(), remove: vi.fn(),
  success: vi.fn(), error: vi.fn(),
  auth: { user: { id: "user-1" }, loading: false },
  org: { orgContext: { organization_id: 7 }, loading: false },
}));

vi.mock("@/client/sdk.gen", () => ({
  getCallEventsSettingsApiV1OrganizationsCallEventsGet: mocks.get,
  saveCallEventsSettingsApiV1OrganizationsCallEventsPut: mocks.save,
  testCallEventsConnectionApiV1OrganizationsCallEventsTestPost: mocks.test,
  deleteCallEventsSettingsApiV1OrganizationsCallEventsDelete: mocks.remove,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => mocks.org }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

const saved = {
  enabled: true,
  sink_type: "bigquery",
  config: { table: "milo-506211.dograh.pipeline_diagnostics", auth_mode: "service_account", client_email: "test@milo-506211.iam.gserviceaccount.com", private_key: "********" },
  deployment_identity_available: false,
  available_sinks: ["bigquery"],
};

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.clearAllMocks();
  mocks.auth.loading = false;
  mocks.org.orgContext.organization_id = 7;
  mocks.get.mockResolvedValue({ data: saved });
  mocks.save.mockResolvedValue({ data: saved });
  mocks.test.mockResolvedValue({ data: { message: "Connection and table schema verified" } });
});

afterEach(() => vi.unstubAllGlobals());

describe("Call event organization settings", () => {
  it("waits for authentication before loading", async () => {
    mocks.auth.loading = true;
    const view = render(<CallEventsSection />);
    expect(mocks.get).not.toHaveBeenCalled();
    mocks.auth.loading = false;
    view.rerender(<CallEventsSection />);
    await screen.findByLabelText("Table");
    expect(mocks.get).toHaveBeenCalledOnce();
  });

  it("saves BigQuery-specific fields and preserves a masked key", async () => {
    render(<CallEventsSection />);
    const table = await screen.findByLabelText("Table");
    fireEvent.change(table, { target: { value: "milo-506211.dograh.new_table" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
    expect(mocks.save.mock.calls[0][0].body).toEqual({
      enabled: true, sink_type: "bigquery", config: { ...saved.config, table: "milo-506211.dograh.new_table" },
    });
    await waitFor(() => expect(mocks.success).toHaveBeenCalled());
  });

  it("checks the connection without saving or enabling settings", async () => {
    render(<CallEventsSection />);
    await screen.findByLabelText("Table");
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(mocks.test).toHaveBeenCalledOnce());
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("surfaces validation errors instead of claiming the save succeeded", async () => {
    mocks.save.mockResolvedValue({ error: { detail: [{ msg: "Invalid table" }] } });
    render(<CallEventsSection />);
    await screen.findByLabelText("Table");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("Invalid table"));
    expect(mocks.success).not.toHaveBeenCalled();
  });

  it("discards the previous organization's draft on an organization switch", async () => {
    const view = render(<CallEventsSection />);
    fireEvent.change(await screen.findByLabelText("Private key"), { target: { value: "unsaved-private-key" } });
    mocks.get.mockResolvedValue({ data: { ...saved, enabled: false, config: {} } });
    mocks.org.orgContext.organization_id = 8;
    view.rerender(<CallEventsSection />);
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((screen.getByLabelText("Private key") as HTMLTextAreaElement).value).toBe(""));
  });
});
