import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { archiveNamedModelConfiguration, archiveProviderConnection, restoreNamedModelConfiguration, restoreProviderConnection, setDefaultModelConfiguration } from "@/client/sdk.gen";
import { useOrgConfig } from "@/context/OrgConfigContext";

import ModelConfigurationsManager from "./ModelConfigurationsManager";
import ProviderConnectionsManager from "./ProviderConnectionsManager";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/client/sdk.gen", () => ({ archiveNamedModelConfiguration: vi.fn(), archiveProviderConnection: vi.fn(), restoreNamedModelConfiguration: vi.fn(), restoreProviderConnection: vi.fn(), setDefaultModelConfiguration: vi.fn() }));
vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const provider = { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {} } };
const catalog: ModelConnectionCatalog = { services: { llm: { dograh: provider }, stt: { dograh: provider }, tts: { dograh: provider }, embeddings: { dograh: provider } } };
const connections: ProviderConnection[] = ["Primary", "Secondary"].map(name => ({ uuid: name, name, provider: "dograh", is_active: true, revision: 1, connection_settings: {}, configured_credentials: ["api_key"] }));
const service = { provider_connection_uuid: "Primary", settings: {} };
const configurations: NamedModelConfiguration[] = ["Sales", "Support"].map(name => ({ uuid: name, name, is_active: true, revision: 1, configuration: { version: 3, mode: "pipeline", llm: service, stt: service, tts: service, embeddings: service } }));
const reload = vi.fn();
const refreshConfig = vi.fn();

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations, defaultUuid: "Sales", loading: false, error: null, reload });
    vi.mocked(useOrgConfig).mockReturnValue({ refreshConfig } as unknown as ReturnType<typeof useOrgConfig>);
    vi.mocked(setDefaultModelConfiguration).mockResolvedValue({ data: {} } as never);
    vi.mocked(restoreNamedModelConfiguration).mockResolvedValue({ data: {} } as never);
    vi.mocked(restoreProviderConnection).mockResolvedValue({ data: {} } as never);
    reload.mockReset();
});

describe("provider connection and model configuration lists", () => {
    it("sets the organization default directly from the configuration row", async () => {
        render(<ModelConfigurationsManager />);
        expect(screen.getByText("Named Model Configurations")).toBeDefined();
        expect(screen.queryByText("Organization Default")).toBeNull();
        expect(screen.queryByRole("combobox")).toBeNull();
        expect(screen.getByText("Org default").parentElement?.textContent).toContain("Sales");
        fireEvent.click(screen.getByRole("button", { name: "Make org default" }));
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());
        expect(setDefaultModelConfiguration).toHaveBeenCalledWith({ body: { model_configuration_uuid: "Support" } });
        expect(reload).toHaveBeenCalledOnce();
        expect(push).not.toHaveBeenCalled();
    });

    it("shows default selection errors without reporting a successful change", async () => {
        vi.mocked(setDefaultModelConfiguration).mockResolvedValue({ error: { detail: "Configuration is unavailable" } } as never);
        render(<ModelConfigurationsManager />);
        fireEvent.click(screen.getByRole("button", { name: "Make org default" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Configuration is unavailable");
        expect(reload).not.toHaveBeenCalled();
        expect(screen.getByText("Org default").parentElement?.textContent).toContain("Sales");
    });

    it("opens configuration rows by mouse or keyboard and links to create, edit, and duplicate pages", () => {
        render(<ModelConfigurationsManager />);
        expect(screen.getByRole("link", { name: "Add Configuration" }).getAttribute("href")).toBe("/model-configurations/new");
        expect(screen.getAllByRole("link", { name: "Edit" })[0].getAttribute("href")).toBe("/model-configurations/Sales");
        expect(screen.getAllByRole("link", { name: "Duplicate" })[0].getAttribute("href")).toBe("/model-configurations/new?duplicate=Sales");
        expect(screen.queryByRole("dialog")).toBeNull();
        const row = screen.getByRole("link", { name: "Edit Sales" });
        fireEvent.click(screen.getByRole("heading", { name: /^Sales/ }));
        fireEvent.keyDown(row, { key: "Enter" });
        fireEvent.keyDown(row, { key: " " });
        expect(push).toHaveBeenCalledTimes(3);
        expect(push).toHaveBeenLastCalledWith("/model-configurations/Sales");
        fireEvent.keyDown(screen.getAllByRole("link", { name: "Duplicate" })[0], { key: "Enter" });
        expect(push).toHaveBeenCalledTimes(3);
    });

    it("opens provider rows by mouse or keyboard and links to add and edit pages", () => {
        render(<ProviderConnectionsManager />);
        expect(screen.getByRole("link", { name: "Add Provider" }).getAttribute("href")).toBe("/provider-connections/new");
        expect(screen.getAllByRole("link", { name: "Edit" })[0].getAttribute("href")).toBe("/provider-connections/Primary");
        expect(screen.queryByRole("dialog")).toBeNull();
        const row = screen.getByRole("link", { name: "Edit Primary" });
        fireEvent.click(screen.getByRole("heading", { name: "Primary" }));
        fireEvent.keyDown(row, { key: "Enter" });
        fireEvent.keyDown(row, { key: " " });
        expect(push).toHaveBeenCalledTimes(3);
        expect(push).toHaveBeenLastCalledWith("/provider-connections/Primary");
        fireEvent.keyDown(screen.getAllByRole("button", { name: "Archive" })[0], { key: "Enter" });
        expect(push).toHaveBeenCalledTimes(3);
    });

    it("hides the archive section when there are no archived items", () => {
        render(<ModelConfigurationsManager />);
        expect(screen.queryByRole("button", { name: "Toggle Archived" })).toBeNull();
        expect(useModelConnections).toHaveBeenCalledWith({ includeArchived: true });
    });

    it.each(["models", "providers"] as const)("shows collapsed archived %s and restores them to the active list", async view => {
        const activeState = vi.mocked(useModelConnections).getMockImplementation()!();
        const archived = view === "models"
            ? { ...configurations[1], uuid: "archived", name: "Archived item", is_active: false }
            : { ...connections[1], uuid: "archived", name: "Archived item", is_active: false };
        const key = view === "models" ? "configurations" : "connections";
        const archivedState = { ...activeState, [key]: [...activeState[key], archived] };
        vi.mocked(useModelConnections).mockReturnValue(archivedState);
        reload.mockImplementation(async () => {
            vi.mocked(useModelConnections).mockReturnValue({ ...activeState, [key]: [...activeState[key], { ...archived, is_active: true }] });
        });
        render(view === "models" ? <ModelConfigurationsManager /> : <ProviderConnectionsManager />);
        expect(screen.queryByText("Archived item")).toBeNull();
        const toggle = screen.getByRole("button", { name: "Toggle Archived" });
        expect(toggle.textContent).toContain("1");
        expect(toggle.getAttribute("aria-expanded")).toBe("false");
        fireEvent.click(toggle);
        expect(screen.getByText("Archived item")).toBeDefined();
        expect(screen.getAllByRole("link", { name: "Edit" })).toHaveLength(2);
        fireEvent.click(screen.getByRole("button", { name: "Restore" }));
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());
        expect(view === "models" ? restoreNamedModelConfiguration : restoreProviderConnection).toHaveBeenCalledWith({ path: { [view === "models" ? "configuration_uuid" : "connection_uuid"]: "archived" } });
        await waitFor(() => expect(screen.queryByRole("button", { name: "Toggle Archived" })).toBeNull());
        expect(screen.getByText("Archived item")).toBeDefined();
        expect(screen.getAllByRole("link", { name: "Edit" })).toHaveLength(3);
        expect(setDefaultModelConfiguration).not.toHaveBeenCalled();
    });

    it("keeps a failed restore in the archive and shows the server error", async () => {
        const state = vi.mocked(useModelConnections).getMockImplementation()!();
        vi.mocked(useModelConnections).mockReturnValue({ ...state, configurations: [{ ...configurations[1], is_active: false }] });
        vi.mocked(restoreNamedModelConfiguration).mockResolvedValue({ error: { detail: "Provider connection not found" } } as never);
        render(<ModelConfigurationsManager />);
        fireEvent.click(screen.getByRole("button", { name: "Toggle Archived" }));
        fireEvent.click(screen.getByRole("button", { name: "Restore" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Provider connection not found");
        expect(reload).not.toHaveBeenCalled();
        expect(screen.getByText("Support")).toBeDefined();
    });

    it("asks users to restore archived provider dependencies before restoring a model", () => {
        const state = vi.mocked(useModelConnections).getMockImplementation()!();
        vi.mocked(useModelConnections).mockReturnValue({ ...state, connections: connections.map(connection => ({ ...connection, is_active: false })), configurations: [{ ...configurations[1], is_active: false }] });
        render(<ModelConfigurationsManager />);
        fireEvent.click(screen.getByRole("button", { name: "Toggle Archived" }));
        expect((screen.getByRole("button", { name: "Restore" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText(/Restore this configuration/).textContent).toContain("provider connections");
        expect(screen.getByText("Dograh · Primary")).toBeDefined();
    });

    it.each(["models", "providers"] as const)("shows newly archived %s in the archive section", async view => {
        const state = vi.mocked(useModelConnections).getMockImplementation()!();
        const key = view === "models" ? "configurations" : "connections";
        const item = state[key][1];
        const archiveItem = view === "models" ? archiveNamedModelConfiguration : archiveProviderConnection;
        vi.mocked(archiveItem).mockResolvedValue({ data: undefined } as never);
        reload.mockImplementation(async () => {
            vi.mocked(useModelConnections).mockReturnValue({ ...state, [key]: state[key].map(current => ({ ...current, is_active: current.uuid !== item.uuid })) });
        });
        render(view === "models" ? <ModelConfigurationsManager /> : <ProviderConnectionsManager />);
        fireEvent.click(screen.getAllByRole("button", { name: "Archive" })[1]);
        expect(push).not.toHaveBeenCalled();
        fireEvent.click(screen.getAllByRole("button", { name: "Archive" }).at(-1)!);
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());
        expect(archiveItem).toHaveBeenCalledWith({ path: { [view === "models" ? "configuration_uuid" : "connection_uuid"]: item.uuid } });
        expect(screen.queryByText(item.name)).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Toggle Archived" }));
        expect(screen.getByText(item.name)).toBeDefined();
    });
});
