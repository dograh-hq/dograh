import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createProviderConnection } from "@/client/sdk.gen";

import { cleanConfiguration, emptyConfiguration } from "./configuration";
import { ConfigurationFields } from "./ConfigurationFields";
import { chooseMode, providerSelect, selectedMode, selectOption, selectOptions, servicePanel } from "./test-helpers";
import type { ConfigurationSpec, ModelConnectionCatalog, ProviderConnection } from "./types";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn(async () => ({ data: { voices: [] } })), createProviderConnection: vi.fn(), updateProviderConnection: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const provider = { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
    model: { type: "string", title: "Model", default: "default-model" },
} } };
const elevenlabs = { ...provider, title: "ElevenLabs", connection_fields: { region: { type: "string", title: "Region" } } };
const catalog: ModelConnectionCatalog = { services: {
    llm: { openai: provider, google: provider, dograh: { ...provider, settings_schema: { properties: { temperature: { type: "number", title: "Temperature", default: 0.1 } } } } },
    stt: { openai: provider, dograh: { ...provider, settings_schema: { properties: { language: { type: "string", title: "Language", default: "en" } } } } },
    tts: { openai: provider, elevenlabs, dograh: { ...provider, settings_schema: { properties: { voice: { type: "string", title: "Voice", default: "Alice" }, speed: { type: "number", title: "Speed", default: 1 } } } } },
    embeddings: { openai: provider, dograh: provider },
    realtime: { openai: provider },
} };
const connection = (uuid: string, provider: string): ProviderConnection => ({ uuid, provider, name: uuid, configured_credentials: ["api_key"], connection_settings: {}, is_active: true, revision: 1 });
const connections = [connection("Dograh primary", "dograh"), connection("Dograh secondary", "dograh"), connection("OpenAI", "openai"), connection("Google", "google"), { ...connection("Archived Dograh", "dograh"), is_active: false }];

function Editor({ available = connections }: { available?: ProviderConnection[] }) {
    const [current, setCurrent] = useState(available);
    const [configuration, setConfiguration] = useState(() => emptyConfiguration(catalog, available));
    return <form onSubmit={event => { event.preventDefault(); onSubmit(configuration); }}>
        <ConfigurationFields catalog={catalog} connections={current} configuration={configuration} onChange={setConfiguration}
            onConnectionsChange={async () => setCurrent(previous => [...previous, created])} llmActions={<button type="button">Configure fallbacks</button>} />
        <button type="submit">Save</button>
    </form>;
}
const onSubmit = vi.fn();
const created = { ...connection("Studio voice", "elevenlabs"), configured_credentials: [] };

beforeEach(() => vi.clearAllMocks());

describe("configuration modes", () => {
    it("offers the three modes as one choice, Dograh selected when an account exists", () => {
        render(<Editor />);
        expect(screen.getAllByRole("radio").map(item => item.getAttribute("aria-labelledby") && document.getElementById(item.getAttribute("aria-labelledby")!)?.textContent))
            .toEqual(["Realtime", "Dograh", "BYOK"]);
        expect(selectedMode()).toBe("Dograh");
        expect(screen.queryByRole("tab")).toBeNull();
    });

    it("fills required BYOK roles when a saved realtime setup has explicit nulls", () => {
        const onChange = vi.fn();
        render(<ConfigurationFields catalog={catalog} connections={connections} onChange={onChange} configuration={{ version: 3, mode: "realtime", llm: { provider_connection_uuid: "OpenAI", settings: { model: "existing-llm" } }, realtime: { provider_connection_uuid: "OpenAI", settings: {} }, stt: null, tts: null, embeddings: null }} />);
        expect(selectedMode()).toBe("Realtime");
        chooseMode("BYOK");
        expect(onChange).toHaveBeenCalledOnce();
        expect(onChange.mock.calls[0][0]).toMatchObject({ mode: "pipeline", llm: { settings: { model: "existing-llm" } }, stt: { provider_connection_uuid: "OpenAI" }, tts: { provider_connection_uuid: "OpenAI" }, embeddings: null });
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("realtime");
    });

    it("uses one Dograh account for all services and preserves settings when changing accounts", () => {
        const onChange = vi.fn();
        const configuration: ConfigurationSpec = { ...emptyConfiguration(catalog, connections), llm: { provider_connection_uuid: "Dograh primary", settings: { temperature: 0.8 } } };
        render(<ConfigurationFields catalog={catalog} connections={connections} configuration={configuration} onChange={onChange} />);
        expect(selectedMode()).toBe("Dograh");
        expect(screen.getAllByRole("combobox")).toHaveLength(1);
        expect(screen.queryByLabelText("Enable embeddings")).toBeNull();
        for (const label of ["Temperature", "Voice", "Speed", "Language"]) expect(screen.getByLabelText(label)).toBeDefined();
        expect(selectOptions("Dograh account")).toEqual(["Dograh primary", "Dograh secondary", "Add a Dograh account"]);
        selectOption("Dograh account", "Dograh secondary");
        const saved = cleanConfiguration(onChange.mock.calls[0][0], catalog, connections);
        for (const role of ["llm", "stt", "tts", "embeddings"] as const) expect(saved[role]?.provider_connection_uuid).toBe("Dograh secondary");
        expect(saved.llm.settings.temperature).toBe(0.8);
        expect(saved.tts?.settings.voice).toBe("Alice");
    });

    it("shows one tab per service, naming its provider, with only compatible external connections", () => {
        render(<Editor />);
        expect(screen.queryByRole("button", { name: "Configure fallbacks" })).toBeNull();
        chooseMode("BYOK");
        expect(screen.getAllByRole("tab").map(item => item.getAttribute("aria-labelledby") && document.getElementById(item.getAttribute("aria-labelledby")!)?.textContent))
            .toEqual(["LLM", "STT", "TTS", "Embedding"]);
        expect(screen.getByRole("tab", { name: "LLM" }).getAttribute("aria-selected")).toBe("true");
        expect(screen.getByRole("tab", { name: "Embedding" }).textContent).toContain("Off");
        expect(within(servicePanel("LLM")).getByRole("button", { name: "Configure fallbacks" })).toBeDefined();
        expect(selectOptions(providerSelect("LLM"))).toEqual(["openai", "google", "Add a provider"]);
        expect(selectOptions(providerSelect("STT"))).toEqual(["openai", "Add a provider"]);
        // Providers not yet connected are named under the add entry.
        expect(selectOptions(providerSelect("TTS"))).toEqual(["openai", "Add a providerElevenLabs"]);
        const embeddings = servicePanel("Embedding");
        expect((within(embeddings).getByLabelText("Enable embeddings") as HTMLInputElement).checked).toBe(false);
        fireEvent.click(within(embeddings).getByLabelText("Enable embeddings"));
        expect(selectOptions(providerSelect("Embedding"))).toEqual(["openai", "Add a provider"]);
        expect(screen.getByRole("tab", { name: "Embedding" }).textContent).toContain("openai");
        chooseMode("Realtime");
        expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["Realtime openai", "LLM openai", "Embedding openai"]);
        expect(selectOptions(providerSelect("Realtime"))).toEqual(["openai", "Add a provider"]);
        expect(within(servicePanel("LLM")).getByText(/This text LLM handles variable extraction/)).toBeDefined();
        chooseMode("Dograh");
        expect(screen.queryByRole("button", { name: "Configure fallbacks" })).toBeNull();
        expect(screen.queryByLabelText("Enable embeddings")).toBeNull();
        expect(screen.getByLabelText("Dograh account")).toBeDefined();
    });

    it("marks services without a provider when switching a Dograh configuration to BYOK", () => {
        render(<Editor available={connections.filter(item => item.provider === "dograh")} />);
        chooseMode("BYOK");
        expect(selectedMode()).toBe("BYOK");
        for (const role of ["LLM", "STT", "TTS"]) {
            expect(screen.getByRole("tab", { name: role }).textContent).toBe(`${role} Choose a provider`);
            const select = providerSelect(role);
            expect(select.textContent).toBe("Choose a provider");
            expect(select.getAttribute("aria-required")).toBe("true");
            const options = selectOptions(select);
            expect(options).toHaveLength(1);
            expect(options[0]).toMatch(/^Add a provider/);
        }
        chooseMode("Dograh");
        expect(selectedMode()).toBe("Dograh");
    });

    it("requires a Dograh account before saving Dograh mode when none exists", () => {
        render(<Editor available={connections.filter(item => item.provider !== "dograh")} />);
        chooseMode("Dograh");
        expect(selectedMode()).toBe("Dograh");
        expect(screen.getByLabelText("Dograh account").getAttribute("aria-required")).toBe("true");
        expect(screen.getByLabelText("Dograh account").textContent).toBe("Choose an account");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSubmit).not.toHaveBeenCalled();
    });

    it("opens the tab of a service that still needs a provider when the form is submitted", async () => {
        render(<Editor available={[connection("Google", "google")]} />);
        expect(selectedMode()).toBe("BYOK");
        expect(screen.getByRole("tab", { name: "LLM" }).getAttribute("aria-selected")).toBe("true");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSubmit).not.toHaveBeenCalled();
        await waitFor(() => expect(screen.getByRole("tab", { name: "STT" }).getAttribute("aria-selected")).toBe("true"));
    });
});

describe("adding a provider", () => {
    it("connects a provider from a service's list and chooses it there, without submitting the configuration", async () => {
        vi.mocked(createProviderConnection).mockResolvedValue({ data: created } as never);
        render(<Editor />);
        chooseMode("BYOK");
        selectOption(providerSelect("TTS"), /Add a provider/);
        const dialog = screen.getByRole("dialog", { name: "Add a TTS provider" });
        // Only TTS providers, the ones not connected yet first.
        expect(within(dialog).getByLabelText("Provider").textContent).toBe("ElevenLabs");
        fireEvent.keyDown(within(dialog).getByLabelText("Provider"), { key: "ArrowDown" });
        expect(screen.getAllByRole("option").map(item => item.textContent)).toEqual(["ElevenLabs", "openai"]);
        fireEvent.click(screen.getByRole("option", { name: "ElevenLabs" }));
        expect(within(dialog).getByLabelText("Region")).toBeDefined();
        fireEvent.change(within(dialog).getByLabelText("Connection name"), { target: { value: "Studio voice" } });
        fireEvent.click(within(dialog).getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        expect(vi.mocked(createProviderConnection).mock.calls[0][0]?.body).toMatchObject({ provider: "elevenlabs", name: "Studio voice" });
        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.getByRole("tab", { name: "TTS" }).textContent).toBe("TTS ElevenLabs");
        expect(providerSelect("TTS").textContent).toBe("ElevenLabs Studio voice");
    });
});

describe("compact density", () => {
    function CompactEditor() {
        const [configuration, setConfiguration] = useState(emptyConfiguration(catalog, connections));
        return <ConfigurationFields catalog={catalog} connections={connections} configuration={configuration} onChange={setConfiguration} density="compact" fields={["model", "voice"]} />;
    }

    it("shows only the quick fields and leaves out embeddings", () => {
        render(<CompactEditor />);
        expect(selectedMode()).toBe("Dograh");
        expect(screen.getByLabelText("Voice")).toBeDefined();
        expect(screen.queryByLabelText("Speed")).toBeNull();
        expect(screen.queryByLabelText("Temperature")).toBeNull();
        chooseMode("BYOK");
        expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["LLM openai", "STT openai", "TTS openai"]);
        expect(screen.queryByLabelText("Enable embeddings")).toBeNull();
        expect(selectOptions(providerSelect("LLM"))).toEqual(["openai", "google", "Add a provider"]);
        expect(within(servicePanel("LLM")).getByLabelText("Model")).toBeDefined();
    });
});
