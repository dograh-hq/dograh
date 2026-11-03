import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OrganizationPreferencesResponse } from "@/client/types.gen";

import { CallEventsSection } from "./CallEventsSection";

const mocks = vi.hoisted(() => ({
  save: vi.fn(), test: vi.fn(),
  success: vi.fn(), error: vi.fn(),
  auth: { user: { id: "user-1" }, loading: false, provider: "stack" },
  org: {
    orgContext: { organization_id: 7 }, loading: false, error: null as Error | null,
    organizationPreferences: null as OrganizationPreferencesResponse | null,
    refreshConfig: vi.fn(),
  },
}));

vi.mock("@/client/sdk.gen", () => ({
  savePreferencesApiV1OrganizationsPreferencesPut: mocks.save,
  testCallEventsConnectionApiV1OrganizationsCallEventsTestPost: mocks.test,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => mocks.org }));
vi.mock("sonner", () => ({ toast: { success: mocks.success, error: mocks.error } }));

const saved = {
  enabled: true,
  sink_type: "bigquery",
  config: { table: "example-project.analytics.call_events", auth_mode: "service_account", client_email: "test@example-project.iam.gserviceaccount.com", private_key: "********" },
};

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.clearAllMocks();
  mocks.auth.loading = false;
  mocks.org.loading = false;
  mocks.org.error = null;
  mocks.org.orgContext.organization_id = 7;
  mocks.org.organizationPreferences = { timezone: "Europe/Rome", call_events: saved };
  mocks.org.refreshConfig.mockResolvedValue(undefined);
  mocks.save.mockResolvedValue({ data: mocks.org.organizationPreferences });
  mocks.test.mockResolvedValue({ data: { message: "Connection and table schema verified" } });
});

afterEach(() => vi.unstubAllGlobals());

describe("Call event organization settings", () => {
  it("waits for authentication and organization preferences, then uses the context", () => {
    mocks.auth.loading = true;
    const view = render(<CallEventsSection />);
    expect(screen.queryByLabelText("Table")).toBeNull();
    mocks.auth.loading = false;
    mocks.org.loading = true;
    view.rerender(<CallEventsSection />);
    expect(screen.queryByLabelText("Table")).toBeNull();
    mocks.org.loading = false;
    view.rerender(<CallEventsSection />);
    expect((screen.getByLabelText("Table") as HTMLInputElement).value).toBe(saved.config.table);
    expect(mocks.org.refreshConfig).not.toHaveBeenCalled();
  });

  it("saves only call events through preferences and refreshes the context", async () => {
    render(<CallEventsSection />);
    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "example-project.analytics.new_table" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
    expect(mocks.save.mock.calls[0][0].body).toEqual({ call_events: {
      enabled: true, sink_type: "bigquery", config: { ...saved.config, table: "example-project.analytics.new_table" },
    } });
    await waitFor(() => expect(mocks.org.refreshConfig).toHaveBeenCalledOnce());
    expect(mocks.success).toHaveBeenCalledWith("Call event settings saved");
  });

  it("removes the destination through preferences without resetting other settings", async () => {
    mocks.save.mockResolvedValue({ data: { call_events: { enabled: false, sink_type: null, config: {} } } });
    render(<CallEventsSection />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith({ body: { call_events: null } }));
    await waitFor(() => expect(mocks.org.refreshConfig).toHaveBeenCalledOnce());
    expect((screen.getByLabelText("Private key") as HTMLTextAreaElement).value).toBe("");
  });

  it("checks the connection without saving or enabling settings", async () => {
    render(<CallEventsSection />);
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(mocks.test).toHaveBeenCalledOnce());
    expect(mocks.save).not.toHaveBeenCalled();
    expect(mocks.org.refreshConfig).not.toHaveBeenCalled();
  });

  it("surfaces validation errors instead of claiming the save succeeded", async () => {
    mocks.save.mockResolvedValue({ error: { detail: [{ msg: "Invalid table" }] } });
    render(<CallEventsSection />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith("Invalid table"));
    expect(mocks.success).not.toHaveBeenCalled();
    expect(mocks.org.refreshConfig).not.toHaveBeenCalled();
  });

  it("retries failures through the organization context", async () => {
    mocks.org.error = new Error("Preferences unavailable");
    render(<CallEventsSection />);
    expect(screen.getByRole("alert").textContent).toBe("Preferences unavailable");
    expect(screen.queryByLabelText("Private key")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(mocks.org.refreshConfig).toHaveBeenCalledOnce();
  });

  it("discards the previous organization's draft on an organization switch", () => {
    const view = render(<CallEventsSection />);
    fireEvent.change(screen.getByLabelText("Private key"), { target: { value: "unsaved-private-key" } });
    mocks.org.organizationPreferences = { call_events: { enabled: false, config: {} } };
    mocks.org.orgContext.organization_id = 8;
    view.rerender(<CallEventsSection />);
    expect((screen.getByLabelText("Private key") as HTMLTextAreaElement).value).toBe("");
  });
});
