import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getVoicesApiV1UserConfigurationsVoicesProviderGet } from "@/client/sdk.gen";
import type { VoiceInfo } from "@/client/types.gen";

import { ConfigurationFields } from "./model-connections/ConfigurationFields";
import { SchemaFields } from "./model-connections/SchemaFields";
import { selectOption, servicePanel } from "./model-connections/test-helpers";
import type { ConfigurationSpec, ModelConnectionCatalog, ProviderConnection } from "./model-connections/types";
import { clearVoiceCache, VoiceSelector } from "./VoiceSelector";

const auth = vi.hoisted(() => ({ loading: false, user: { id: "user-1" } }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client/sdk.gen", () => ({ getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn(), createProviderConnection: vi.fn(), updateProviderConnection: vi.fn() }));

const fetchVoices = vi.mocked(getVoicesApiV1UserConfigurationsVoicesProviderGet);
const sofie: VoiceInfo = { voice_id: "sofie-id", name: "Sofie", language: "da" };
const props = { provider: "elevenlabs", providerConnectionUuid: "account-1", model: "eleven_v4_turbo", value: "saved-voice", onChange: vi.fn() };

beforeEach(() => {
    vi.clearAllMocks();
    clearVoiceCache();
    auth.loading = false;
    fetchVoices.mockResolvedValue({ data: { provider: "elevenlabs", voices: [sofie] } } as never);
});

describe("Provider connection voices", () => {
    it("loads the saved connection and selected model through the configuration editor", async () => {
        const catalog: ModelConnectionCatalog = { services: {
            llm: { openai: { title: "OpenAI", credential_fields: {}, connection_fields: {}, settings_schema: { properties: {} } } },
            tts: { elevenlabs: { title: "ElevenLabs", credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
                voice: { type: "string", title: "Voice", default: "saved-voice", voice_catalog: "dropdown" },
                model: { type: "string", title: "Model", default: "eleven_v4_turbo", examples: ["eleven_v4_turbo", "eleven_flash_v2_5"] },
            } } } },
        } };
        const connections: ProviderConnection[] = [
            { uuid: "llm", provider: "openai", name: "OpenAI", configured_credentials: ["api_key"], connection_settings: {}, revision: 1, is_active: true },
            { uuid: "account-1", provider: "elevenlabs", name: "Studio", configured_credentials: ["api_key"], connection_settings: {}, revision: 1, is_active: true },
        ];
        function Editor() {
            const [configuration, setConfiguration] = useState<ConfigurationSpec>({ version: 3, mode: "pipeline", llm: { provider_connection_uuid: "llm", settings: {} }, tts: { provider_connection_uuid: "account-1", settings: { model: "eleven_v4_turbo", voice: "saved-voice" } } });
            return <ConfigurationFields catalog={catalog} connections={connections} configuration={configuration} onChange={setConfiguration} />;
        }
        render(<Editor />);
        const panel = servicePanel("TTS");
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledWith(expect.objectContaining({ path: { provider: "elevenlabs" }, query: { model: "eleven_v4_turbo", provider_connection_uuid: "account-1" } })));
        const trigger = within(panel).getByLabelText("Voice") as HTMLButtonElement;
        await waitFor(() => expect(trigger.disabled).toBe(false));
        // An old voice ID must not replace the dropdown with a manual input.
        expect(trigger.getAttribute("role")).toBe("combobox");
        expect(within(panel).queryByLabelText("Enter Custom Value")).toBeNull();
        fireEvent.click(trigger);
        fireEvent.click(await screen.findByText("Sofie"));
        expect(trigger.textContent).toContain("Sofie");
        selectOption("Model", "eleven_flash_v2_5");
        await waitFor(() => expect(fetchVoices).toHaveBeenLastCalledWith(expect.objectContaining({ query: { model: "eleven_flash_v2_5", provider_connection_uuid: "account-1" } })));
    });

    it.each(["elevenlabs", "cartesia", "deepgram", "rime"])("waits for authentication and requires a saved %s connection", async (provider) => {
        auth.loading = true;
        const { rerender } = render(<VoiceSelector {...props} provider={provider} />);
        expect(fetchVoices).not.toHaveBeenCalled();
        auth.loading = false;
        rerender(<VoiceSelector {...props} provider={provider} providerConnectionUuid={undefined} />);
        expect(fetchVoices).not.toHaveBeenCalled();
        rerender(<VoiceSelector {...props} provider={provider} language="da" />);
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledWith(expect.objectContaining({ path: { provider }, query: { provider_connection_uuid: "account-1", model: "eleven_v4_turbo", language: "da" } })));
    });

    it("discards late responses after switching accounts or models", async () => {
        let resolveOld: (value: unknown) => void = () => {};
        fetchVoices.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }) as never);
        const { rerender } = render(<VoiceSelector {...props} />);
        const firstSignal = fetchVoices.mock.calls[0][0]?.signal;
        rerender(<VoiceSelector {...props} providerConnectionUuid="account-2" model="eleven_v3" />);
        await waitFor(() => expect((screen.getByRole("combobox") as HTMLButtonElement).disabled).toBe(false));
        expect(firstSignal?.aborted).toBe(true);
        await act(async () => resolveOld({ data: { voices: [{ voice_id: "old", name: "Wrong account voice" }] } }));
        fireEvent.click(screen.getByRole("combobox"));
        expect(await screen.findByText("Sofie")).toBeDefined();
        expect(screen.queryByText("Wrong account voice")).toBeNull();
        expect(props.onChange).not.toHaveBeenCalled();
    });

    it("shows provider errors, supports retry and keeps manual voice entry available", async () => {
        fetchVoices.mockResolvedValueOnce({ error: { detail: "Check your Voices read permission" } } as never);
        function Editor() {
            const [value, setValue] = useState("saved-voice");
            return <SchemaFields provider="elevenlabs" providerConnectionUuid="account-1" role="tts"
                schema={{ properties: { voice: { type: "string", title: "Voice", voice_catalog: "dropdown", allow_custom_input: true } } }}
                values={{ voice: value }} onChange={(_, next) => { setValue(String(next)); props.onChange(String(next)); }} />;
        }
        render(<Editor />);
        await waitFor(() => expect((screen.getByRole("combobox") as HTMLButtonElement).disabled).toBe(false));
        fireEvent.click(screen.getByRole("combobox"));
        expect((await screen.findByRole("alert")).textContent).toContain("Voices read permission");
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(await screen.findByText("Sofie")).toBeDefined();
        fireEvent.click(screen.getByLabelText("Enter Custom Value"));
        fireEvent.change(screen.getByLabelText("Voice"), { target: { value: "custom-voice" } });
        expect(props.onChange).toHaveBeenCalledWith("custom-voice");
        props.onChange.mockClear();
        fireEvent.click(screen.getByLabelText("Enter Custom Value"));
        await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain("custom-voice"));
        expect(props.onChange).not.toHaveBeenCalled();
        expect(fetchVoices).toHaveBeenCalledTimes(2);
    });

    it("renders Sarvam static voices client-side with 0ms load, search, and no refresh button", async () => {
        function Editor() {
            const [value, setValue] = useState("shubh_enhi_ads");
            return <SchemaFields provider="sarvam" providerConnectionUuid="sarvam-conn" role="tts"
                schema={{ properties: { voice: { type: "string", title: "Voice", voice_catalog: "dropdown", examples: ["shubh_enhi_ads", "aditi_hi_news"], allow_custom_input: true } } }}
                values={{ voice: value }} onChange={(_, next) => setValue(String(next))} />;
        }
        render(<Editor />);
        const trigger = screen.getByRole("combobox") as HTMLButtonElement;
        expect(trigger.disabled).toBe(false);
        expect(trigger.textContent).toContain("Shubh");
        expect(fetchVoices).not.toHaveBeenCalled();

        fireEvent.click(trigger);
        expect(screen.getByText("English-Hindi • Ads • shubh_enhi_ads")).toBeDefined();
        expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();

        const searchInput = screen.getByPlaceholderText("Search voices...");
        fireEvent.change(searchInput, { target: { value: "aditi" } });
        expect(screen.queryByText("English-Hindi • Ads • shubh_enhi_ads")).toBeNull();
        expect(screen.getByText("Hindi • News • aditi_hi_news")).toBeDefined();
    });

    it("does not offer static voices when selected model has no model_options entry", async () => {
        function Editor() {
            const [value, setValue] = useState("shubh_enhi_ads");
            return <SchemaFields provider="sarvam" providerConnectionUuid="sarvam-conn" role="tts"
                schema={{ properties: { voice: { type: "string", title: "Voice", voice_catalog: "dropdown", examples: ["shubh_enhi_ads"], model_options: { "bulbul:v4-flash": ["shubh_enhi_ads"] }, allow_custom_input: true } } }}
                values={{ model: "bulbul:v2", voice: value }} onChange={(_, next) => setValue(String(next))} />;
        }
        render(<Editor />);
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledWith(expect.objectContaining({ path: { provider: "sarvam" } })));
    });

    it("refetches voices when connection revision changes or cache is cleared for connection", async () => {
        const { rerender, unmount } = render(<VoiceSelector {...props} connectionRevision={1} />);
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledTimes(1));

        // Same revision does not refetch (cache hit)
        rerender(<VoiceSelector {...props} connectionRevision={1} />);
        expect(fetchVoices).toHaveBeenCalledTimes(1);

        // Updated connection revision triggers a fresh fetch
        fetchVoices.mockResolvedValueOnce({ data: { provider: "elevenlabs", voices: [{ voice_id: "new-acc", name: "New Account Voice" }] } } as never);
        rerender(<VoiceSelector {...props} connectionRevision={2} />);
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledTimes(2));

        unmount();

        // Remounting at the same revision without clearing hits the cache (no new fetch)
        const { unmount: unmountAgain } = render(<VoiceSelector {...props} connectionRevision={2} />);
        expect(fetchVoices).toHaveBeenCalledTimes(2);
        unmountAgain();

        // Clearing an unrelated connection leaves account-1 cached
        clearVoiceCache("other-account");
        const { unmount: unmountThird } = render(<VoiceSelector {...props} connectionRevision={2} />);
        expect(fetchVoices).toHaveBeenCalledTimes(2);
        unmountThird();

        // Clearing voice cache for account-1 removes its cached entry and triggers a new fetch on remount
        clearVoiceCache("account-1");
        render(<VoiceSelector {...props} connectionRevision={2} />);
        await waitFor(() => expect(fetchVoices).toHaveBeenCalledTimes(3));
    });
});
