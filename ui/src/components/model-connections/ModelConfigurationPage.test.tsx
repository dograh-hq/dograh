import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createNamedModelConfiguration, getDefaultModelConfiguration, getModelConnectionCatalog, getVoicesApiV1UserConfigurationsVoicesProviderGet, listNamedModelConfigurations, listProviderConnections, updateNamedModelConfiguration } from "@/client/sdk.gen";
import { useOrgConfig } from "@/context/OrgConfigContext";

import { LLMConfigurationPage } from "./LLMConfigurationPage";
import { ModelConfigurationPage } from "./ModelConfigurationPage";
import { selectOption } from "./test-helpers";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";

const { replace, push } = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push }) }));
vi.mock("@/client/sdk.gen", () => ({ createNamedModelConfiguration: vi.fn(), updateNamedModelConfiguration: vi.fn(), getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn(), getDefaultModelConfiguration: vi.fn(), getModelConnectionCatalog: vi.fn(), listNamedModelConfigurations: vi.fn(), listProviderConnections: vi.fn() }));
vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const provider = { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {} } };
const catalog: ModelConnectionCatalog = { services: {
    llm: { dograh: provider, openai: provider }, stt: { dograh: provider, openai: provider }, embeddings: { dograh: provider },
    tts: { openai: provider, dograh: { ...provider, settings_schema: { properties: { voice: { type: "string", title: "Voice", default: "alice-id", allow_custom_input: true } } } } },
} };
const connections: ProviderConnection[] = ["Primary", "Secondary"].map(name => ({ uuid: name, name, provider: "dograh", is_active: true, revision: 1, connection_settings: {}, configured_credentials: ["api_key"] }));
const openai: ProviderConnection = { ...connections[0], uuid: "openai", name: "OpenAI", provider: "openai" };
const service = { provider_connection_uuid: "Primary", settings: {} };
const configuration: NamedModelConfiguration = { uuid: "sales", name: "Sales", is_active: true, revision: 4, configuration: { version: 3, mode: "pipeline", llm: service, stt: service, tts: { ...service, settings: { voice: "alice-id" } }, embeddings: service } };
const voices = ["Alice", "Bella"].map(name => ({ voice_id: `${name.toLowerCase()}-id`, name, preview_url: `https://audio.example/${name}.wav`, gender: "female", accent: "us", language: "en" }));
const reload = vi.fn();
const refreshConfig = vi.fn();
const play = vi.fn(async () => undefined);
const pause = vi.fn();
const audio = vi.fn(function () { return { play, pause }; });

beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("Audio", audio);
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections: [...connections, openai], configurations: [configuration], defaultUuid: "sales", loading: false, error: null, reload });
    vi.mocked(useOrgConfig).mockReturnValue({ refreshConfig } as unknown as ReturnType<typeof useOrgConfig>);
    vi.mocked(createNamedModelConfiguration).mockResolvedValue({ data: { ...configuration, uuid: "new-configuration" } } as never);
    vi.mocked(updateNamedModelConfiguration).mockResolvedValue({ data: configuration } as never);
    vi.mocked(getVoicesApiV1UserConfigurationsVoicesProviderGet).mockResolvedValue({ data: { provider: "dograh", voices } } as never);
});

describe("model configuration pages", () => {
    it("keeps the saved configuration editable throughout the organization refresh", async () => {
        const actual = await vi.importActual<typeof import("./useModelConnections")>("./useModelConnections");
        vi.mocked(useModelConnections).mockImplementation(actual.useModelConnections);
        const orgConfig = { orgContext: { organization_id: 7 }, loading: false, refreshConfig } as unknown as ReturnType<typeof useOrgConfig>;
        vi.mocked(useOrgConfig).mockReturnValue(orgConfig);
        vi.mocked(getModelConnectionCatalog).mockResolvedValue({ data: catalog } as never);
        vi.mocked(listProviderConnections).mockResolvedValue({ data: connections } as never);
        vi.mocked(listNamedModelConfigurations).mockResolvedValue({ data: [configuration] } as never);
        vi.mocked(getDefaultModelConfiguration).mockResolvedValue({ data: { model_configuration_uuid: "sales" } } as never);
        const refresh = Promise.withResolvers<void>();
        refreshConfig.mockReturnValueOnce(refresh.promise);
        const page = render(<ModelConfigurationPage configurationUuid="sales" />);
        await screen.findByRole("heading", { name: "Edit Model Configuration" });

        const updated = { ...configuration, name: "Updated sales", revision: configuration.revision + 1 };
        vi.mocked(updateNamedModelConfiguration).mockResolvedValue({ data: updated } as never);
        vi.mocked(listNamedModelConfigurations).mockResolvedValue({ data: [updated] } as never);
        fireEvent.change(screen.getByLabelText("Configuration name"), { target: { value: updated.name } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());

        vi.mocked(useOrgConfig).mockReturnValue({ ...orgConfig, loading: true });
        page.rerender(<ModelConfigurationPage configurationUuid="sales" />);
        expect(screen.queryByText("This model configuration is unavailable.")).toBeNull();
        expect((screen.getByLabelText("Configuration name") as HTMLInputElement).value).toBe(updated.name);

        vi.mocked(useOrgConfig).mockReturnValue(orgConfig);
        page.rerender(<ModelConfigurationPage configurationUuid="sales" />);
        await act(async () => refresh.resolve());
        expect(screen.queryByRole("alert")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(updateNamedModelConfiguration).toHaveBeenCalledTimes(2));
        expect(vi.mocked(updateNamedModelConfiguration).mock.calls[1][0]?.body?.revision).toBe(updated.revision);
    });

    it("edits on a page and previews, cancels, and selects Dograh voices in a single dialog", async () => {
        render(<ModelConfigurationPage configurationUuid="sales" />);
        expect(screen.getByRole("heading", { name: "Edit Model Configuration" })).toBeDefined();
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(screen.getByRole("link", { name: "All model configurations" }).getAttribute("href")).toBe("/model-configurations");
        fireEvent.click(screen.getByLabelText("Voice"));
        const dialog = screen.getByRole("dialog");
        expect(screen.getAllByRole("dialog")).toHaveLength(1);
        fireEvent.click((await within(dialog).findAllByRole("button", { name: "Play preview" }))[0]);
        expect(audio).toHaveBeenCalledWith("https://audio.example/Alice.wav");
        expect(play).toHaveBeenCalledOnce();
        fireEvent.click(within(dialog).getByRole("button", { name: /Bella/ }));
        fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
        expect(pause).toHaveBeenCalledOnce();
        expect(screen.getByLabelText("Voice").textContent).toContain("Alice");
        fireEvent.click(screen.getByLabelText("Voice"));
        fireEvent.click(await screen.findByRole("button", { name: /Bella/ }));
        fireEvent.click(screen.getByRole("button", { name: "Use this voice" }));
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(refreshConfig).toHaveBeenCalledOnce());
        expect(vi.mocked(updateNamedModelConfiguration).mock.calls[0][0]).toMatchObject({ path: { configuration_uuid: "sales" }, body: { revision: 4, configuration: { tts: { settings: { voice: "bella-id" } } } } });
        expect(createNamedModelConfiguration).not.toHaveBeenCalled();
    });

    it.each([undefined, "sales"])("creates a configuration from a page (duplicate: %s) and opens its saved route", async duplicateUuid => {
        render(<ModelConfigurationPage duplicateUuid={duplicateUuid} />);
        expect(screen.queryByRole("dialog")).toBeNull();
        if (duplicateUuid) expect((screen.getByLabelText("Configuration name") as HTMLInputElement).value).toBe("Sales (copy)");
        fireEvent.change(screen.getByLabelText("Configuration name"), { target: { value: "Managed Support" } });
        selectOption("Provider connection", "Secondary");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(replace).toHaveBeenCalledWith("/model-configurations/new-configuration"));
        const body = vi.mocked(createNamedModelConfiguration).mock.calls[0][0]?.body;
        for (const role of ["llm", "stt", "tts", "embeddings"] as const) expect(body?.configuration[role]?.provider_connection_uuid).toBe("Secondary");
        expect(body?.name).toBe("Managed Support");
        expect(updateNamedModelConfiguration).not.toHaveBeenCalled();
    });

    it("does not show an empty editable configuration for an unavailable UUID", () => {
        render(<ModelConfigurationPage configurationUuid="missing" />);
        expect(screen.getByRole("alert").textContent).toContain("unavailable");
        expect(screen.queryByRole("button", { name: "Save Configuration" })).toBeNull();
    });

    it("reports voice catalog failures and still allows a custom voice ID", async () => {
        vi.mocked(getVoicesApiV1UserConfigurationsVoicesProviderGet).mockRejectedValue(new Error("Network error"));
        render(<ModelConfigurationPage configurationUuid="sales" />);
        fireEvent.click(screen.getByLabelText("Voice"));
        expect(await screen.findByText("Failed to load voices")).toBeDefined();
        fireEvent.click(screen.getByRole("button", { name: "Custom voice ID" }));
        fireEvent.change(screen.getByLabelText("Custom voice ID"), { target: { value: "custom-voice" } });
        fireEvent.click(screen.getByRole("button", { name: "Use this voice" }));
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(updateNamedModelConfiguration).toHaveBeenCalledOnce());
        expect(vi.mocked(updateNamedModelConfiguration).mock.calls[0][0]?.body?.configuration?.tts?.settings?.voice).toBe("custom-voice");
    });
});

it("saves the current configuration before opening its fallback URL", async () => {
    render(<ModelConfigurationPage configurationUuid="sales" />);
    expect(screen.queryByRole("button", { name: "Configure fallbacks" })).toBeNull();
    selectOption("Mode", "Cascade");
    fireEvent.change(screen.getByLabelText("Configuration name"), { target: { value: "Updated sales" } });
    fireEvent.click(screen.getByRole("button", { name: "Configure fallbacks" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/model-configurations/sales/llm"));
    expect(vi.mocked(updateNamedModelConfiguration).mock.calls[0][0]?.body?.name).toBe("Updated sales");
});

it.each(["dograh", "cascade", "realtime"] as const)("guards the direct fallback URL in %s mode", mode => {
    const saved: NamedModelConfiguration = { ...configuration, configuration: {
        ...configuration.configuration,
        mode: mode === "realtime" ? "realtime" : "pipeline",
        llm: mode === "dograh" ? service : { provider_connection_uuid: openai.uuid, settings: {} },
    } };
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections: [...connections, openai], configurations: [saved], defaultUuid: "sales", loading: false, error: null, reload });
    render(<LLMConfigurationPage configurationUuid="sales" />);
    expect(screen.getByRole("link", { name: "Back to model configuration" }).getAttribute("href")).toBe("/model-configurations/sales");
    if (mode === "dograh") {
        expect(screen.getByRole("alert").textContent).toContain("unavailable in Dograh mode");
        expect(screen.queryByRole("button", { name: "Save fallbacks" })).toBeNull();
    } else {
        expect(screen.getByRole("button", { name: "Save fallbacks" })).toBeDefined();
        if (mode === "realtime") expect(screen.getByText(/These rules apply to the separate text LLM/)).toBeDefined();
    }
});
