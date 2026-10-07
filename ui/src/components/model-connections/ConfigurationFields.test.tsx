import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { cleanConfiguration, emptyConfiguration } from "./configuration";
import { ConfigurationFields } from "./ConfigurationFields";
import { selectOption, selectOptions } from "./test-helpers";
import type { ConfigurationSpec, ModelConnectionCatalog, ProviderConnection } from "./types";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn(async () => ({ data: { voices: [] } })) }));

const provider = { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
    model: { type: "string", title: "Model", default: "default-model" },
} } };
const catalog: ModelConnectionCatalog = { services: {
    llm: { openai: provider, google: provider, dograh: { ...provider, settings_schema: { properties: { temperature: { type: "number", title: "Temperature", default: 0.1 } } } } },
    stt: { openai: provider, dograh: { ...provider, settings_schema: { properties: { language: { type: "string", title: "Language", default: "en" } } } } },
    tts: { openai: provider, dograh: { ...provider, settings_schema: { properties: { voice: { type: "string", title: "Voice", default: "Alice" }, speed: { type: "number", title: "Speed", default: 1 } } } } },
    embeddings: { openai: provider, dograh: provider },
    realtime: { openai: provider },
} };
const connection = (uuid: string, provider: string): ProviderConnection => ({ uuid, provider, name: uuid, configured_credentials: ["api_key"], connection_settings: {}, is_active: true, revision: 1 });
const connections = [connection("Dograh primary", "dograh"), connection("Dograh secondary", "dograh"), connection("OpenAI", "openai"), connection("Google", "google"), { ...connection("Archived Dograh", "dograh"), is_active: false }];

function Editor({ available = connections }: { available?: ProviderConnection[] }) {
    const base = emptyConfiguration(catalog, available);
    const [configuration, setConfiguration] = useState(base);
    return <ConfigurationFields catalog={catalog} connections={available} configuration={configuration} onChange={setConfiguration} llmActions={<button>Configure fallbacks</button>} />;
}

function providerConnection(role: string) {
    return within(screen.getByRole("region", { name: role })).getByLabelText("Provider connection");
}

describe("configuration modes", () => {
    it("fills required Cascade roles when a saved realtime setup has explicit nulls", () => {
        const onChange = vi.fn();
        render(<ConfigurationFields catalog={catalog} connections={connections} onChange={onChange} configuration={{ version: 3, mode: "realtime", llm: { provider_connection_uuid: "OpenAI", settings: { model: "existing-llm" } }, realtime: { provider_connection_uuid: "OpenAI", settings: {} }, stt: null, tts: null, embeddings: null }} />);
        selectOption("Mode", "Cascade");
        expect(onChange).toHaveBeenCalledOnce();
        expect(onChange.mock.calls[0][0]).toMatchObject({ mode: "pipeline", llm: { settings: { model: "existing-llm" } }, stt: { provider_connection_uuid: "OpenAI" }, tts: { provider_connection_uuid: "OpenAI" }, embeddings: null });
        expect(onChange.mock.calls[0][0]).not.toHaveProperty("realtime");
    });

    it("uses one Dograh account for all services and preserves settings when changing accounts", () => {
        const onChange = vi.fn();
        const configuration: ConfigurationSpec = { ...emptyConfiguration(catalog, connections), llm: { provider_connection_uuid: "Dograh primary", settings: { temperature: 0.8 } } };
        render(<ConfigurationFields catalog={catalog} connections={connections} configuration={configuration} onChange={onChange} />);
        expect(screen.getByLabelText("Mode").textContent).toBe("Dograh");
        expect(screen.getAllByRole("combobox")).toHaveLength(2);
        expect(screen.queryByLabelText("Enable embeddings")).toBeNull();
        for (const label of ["Temperature", "Voice", "Speed", "Language"]) expect(screen.getByLabelText(label)).toBeDefined();
        expect(selectOptions("Provider connection")).toEqual(["Dograh primary", "Dograh secondary"]);
        selectOption("Provider connection", "Dograh secondary");
        const saved = cleanConfiguration(onChange.mock.calls[0][0], catalog, connections);
        for (const role of ["llm", "stt", "tts", "embeddings"] as const) expect(saved[role]?.provider_connection_uuid).toBe("Dograh secondary");
        expect(saved.llm.settings.temperature).toBe(0.8);
        expect(saved.tts?.settings.voice).toBe("Alice");
    });

    it("offers only compatible external connections in Cascade and Realtime", () => {
        render(<Editor />);
        expect(screen.queryByRole("button", { name: "Configure fallbacks" })).toBeNull();
        selectOption("Mode", "Cascade");
        expect(screen.getByRole("button", { name: "Configure fallbacks" })).toBeDefined();
        expect(screen.queryByText(/Dograh manages speech recognition/)).toBeNull();
        const llmOptions = selectOptions(providerConnection("LLM"));
        expect(llmOptions).toEqual(["OpenAI · openai", "Google · google"]);
        for (const role of ["STT", "TTS"]) expect(selectOptions(providerConnection(role))).toEqual(["OpenAI · openai"]);
        expect((screen.getByLabelText("Enable embeddings") as HTMLInputElement).checked).toBe(false);
        fireEvent.click(screen.getByLabelText("Enable embeddings"));
        expect(selectOptions(providerConnection("Embedding"))).toEqual(["OpenAI · openai"]);
        selectOption("Mode", "Realtime");
        expect(screen.getByRole("button", { name: "Configure fallbacks" })).toBeDefined();
        expect(screen.queryByRole("region", { name: "STT" })).toBeNull();
        expect(screen.queryByRole("region", { name: "TTS" })).toBeNull();
        expect(selectOptions(providerConnection("Realtime"))).toEqual(["OpenAI · openai"]);
        selectOption("Mode", "Dograh");
        expect(screen.queryByRole("button", { name: "Configure fallbacks" })).toBeNull();
        expect(screen.queryByLabelText("Enable embeddings")).toBeNull();
        expect(screen.getByLabelText("Provider connection")).toBeDefined();
    });

    it("requires external connections when switching a Dograh configuration to Cascade", () => {
        render(<Editor available={connections.filter(item => item.provider === "dograh")} />);
        selectOption("Mode", "Cascade");
        expect(screen.getByLabelText("Mode").textContent).toBe("Cascade");
        for (const role of ["LLM", "STT", "TTS"]) {
            const select = providerConnection(role);
            expect(select.textContent).toBe("Select a connection");
            expect(select.getAttribute("aria-required")).toBe("true");
        }
        selectOption("Mode", "Dograh");
        expect(screen.getByLabelText("Mode").textContent).toBe("Dograh");
    });

    it("requires a Dograh connection before saving Dograh mode when none exists", () => {
        render(<Editor available={connections.filter(item => item.provider !== "dograh")} />);
        selectOption("Mode", "Dograh");
        expect(screen.getByLabelText("Mode").textContent).toBe("Dograh");
        expect(screen.getByLabelText("Provider connection").getAttribute("aria-required")).toBe("true");
        expect(screen.getByLabelText("Provider connection").textContent).toBe("Select a Dograh connection");
    });

});
