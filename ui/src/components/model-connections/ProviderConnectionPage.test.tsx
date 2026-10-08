import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createProviderConnection, updateProviderConnection } from "@/client/sdk.gen";
import { useOrgConfig } from "@/context/OrgConfigContext";

import { ProviderConnectionPage } from "./ProviderConnectionPage";
import type { ModelConnectionCatalog, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/client/sdk.gen", () => ({ createProviderConnection: vi.fn(), updateProviderConnection: vi.fn() }));
vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const catalog: ModelConnectionCatalog = { services: { llm: { dograh: { title: "Dograh", credential_fields: { api_key: { title: "API key", anyOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] } }, credential_required: ["api_key"], connection_fields: {}, settings_schema: { properties: {} } } } } };
const connection: ProviderConnection = { uuid: "existing", name: "Dograh", provider: "dograh", is_active: true, revision: 3, connection_settings: {}, configured_credentials: ["api_key"] };
const reload = vi.fn();
const refreshConfig = vi.fn();

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections: [connection], configurations: [], defaultUuid: null, loading: false, error: null, reload });
    vi.mocked(useOrgConfig).mockReturnValue({ refreshConfig } as unknown as ReturnType<typeof useOrgConfig>);
    vi.mocked(createProviderConnection).mockResolvedValue({ data: { ...connection, uuid: "created" } } as never);
    vi.mocked(updateProviderConnection).mockResolvedValue({ data: connection } as never);
});

describe("provider connection pages", () => {
    it("edits in a dedicated page while preserving existing credentials", async () => {
        render(<ProviderConnectionPage connectionUuid="existing" />);
        expect(screen.getByRole("heading", { name: "Edit Provider Connection" })).toBeDefined();
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(screen.getByRole("link", { name: "All provider connections" }).getAttribute("href")).toBe("/provider-connections");
        expect(screen.getByText("Configured")).toBeDefined();
        expect(screen.queryByLabelText("API key")).toBeNull();
        fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Renamed" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());
        expect(updateProviderConnection).toHaveBeenCalledWith({ path: { connection_uuid: "existing" }, body: { name: "Renamed", revision: 3, credentials: {}, connection_settings: {} } });
    });

    it("creates multiple API keys on a page and opens the saved connection route", async () => {
        render(<ProviderConnectionPage />);
        expect(screen.queryByRole("dialog")).toBeNull();
        fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Second account" } });
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "first" } });
        fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
        fireEvent.change(screen.getByLabelText("API key 2"), { target: { value: "second" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(replace).toHaveBeenCalledWith("/provider-connections/created"));
        expect(createProviderConnection).toHaveBeenCalledWith({ body: { provider: "dograh", name: "Second account", credentials: { api_key: ["first", "second"] }, connection_settings: {} } });
    });

    it("shows unavailable connections without offering an empty editor", () => {
        render(<ProviderConnectionPage connectionUuid="missing" />);
        expect(screen.getByRole("alert").textContent).toContain("unavailable");
        expect(screen.queryByRole("button", { name: "Save Connection" })).toBeNull();
    });
});
