import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createNamedModelConfiguration, getVoicesApiV1UserConfigurationsVoicesProviderGet, updateNamedModelConfiguration } from "@/client/sdk.gen";
import { useOrgConfig } from "@/context/OrgConfigContext";

import { ModelConfigurationPage } from "./ModelConfigurationPage";
import { selectOption } from "./test-helpers";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/client/sdk.gen", () => ({ createNamedModelConfiguration: vi.fn(), updateNamedModelConfiguration: vi.fn(), getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn() }));
vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const provider = { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {} } };
const catalog: ModelConnectionCatalog = { services: {
    llm: { dograh: provider }, stt: { dograh: provider }, embeddings: { dograh: provider },
    tts: { dograh: { ...provider, settings_schema: { properties: { voice: { type: "string", title: "Voice", default: "alice-id", allow_custom_input: true } } } } },
} };
const connections: ProviderConnection[] = ["Primary", "Secondary"].map(name => ({ uuid: name, name, provider: "dograh", is_active: true, revision: 1, connection_settings: {}, configured_credentials: ["api_key"] }));
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
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations: [configuration], defaultUuid: "sales", loading: false, error: null, reload });
    vi.mocked(useOrgConfig).mockReturnValue({ refreshConfig } as unknown as ReturnType<typeof useOrgConfig>);
    vi.mocked(createNamedModelConfiguration).mockResolvedValue({ data: { ...configuration, uuid: "new-configuration" } } as never);
    vi.mocked(updateNamedModelConfiguration).mockResolvedValue({ data: configuration } as never);
    vi.mocked(getVoicesApiV1UserConfigurationsVoicesProviderGet).mockResolvedValue({ data: { provider: "dograh", voices } } as never);
});

describe("model configuration pages", () => {
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
        selectOption("Dograh provider connection", "Secondary");
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
