import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { type ProviderSchema, type ServiceConfigurationDefaults, ServiceConfigurationForm } from "./ServiceConfigurationForm";

vi.mock("@/client/sdk.gen", () => ({
    getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet: vi.fn(),
}));
vi.mock("@/context/UserConfigContext", () => ({ useUserConfig: () => ({ userConfig: null }) }));
vi.mock("@/components/VoiceSelector", () => ({ VoiceSelector: () => null }));
vi.mock("@/components/ui/select", () => ({
    Select: ({ value, onValueChange, children }: { value: string; onValueChange: (value: string) => void; children: ReactNode }) => (
        <select value={value} onChange={event => onValueChange(event.target.value)}>{children}</select>
    ),
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => <option value={value}>{children}</option>,
}));
vi.mock("@/components/ui/switch", () => ({
    Switch: ({ checked, onCheckedChange, id }: { checked: boolean; onCheckedChange: (checked: boolean) => void; id?: string }) => (
        <input type="checkbox" id={id} checked={checked} onChange={event => onCheckedChange(event.target.checked)} />
    ),
}));
vi.mock("@/components/ui/checkbox", () => ({
    Checkbox: ({ checked, onCheckedChange, id }: { checked: boolean; onCheckedChange: (checked: boolean) => void; id?: string }) => (
        <input type="checkbox" id={id} checked={checked} onChange={event => onCheckedChange(event.target.checked)} />
    ),
}));

const defaults: ServiceConfigurationDefaults = {
    llm: {}, tts: {}, stt: {}, embeddings: {},
    default_providers: { realtime: "openai_realtime" },
    realtime: {
        openai_realtime: {
            title: "OpenAI",
            properties: {
                provider: { default: "openai_realtime" },
                model: { default: "gpt-realtime-2", examples: ["gpt-live-1", "gpt-realtime-2.1", "gpt-realtime-2"] },
                voice: { default: "alloy", examples: ["alloy", "marin", "cedar"], model_options: { "gpt-live-1": ["marin", "cedar"] } },
                language: { default: "en", examples: ["en", "fr"] },
                backend_model: { default: "gpt-5.6-luna", examples: ["gpt-5.6-luna"], visible_for_models: ["gpt-live-1"] },
                reasoning_effort: { default: "low", enum: ["none", "low", "medium", "high", "xhigh", "max"], visible_for_models: ["gpt-live-1"] },
                web_search: { type: "boolean", default: false, visible_for_models: ["gpt-live-1"] },
                api_key: { type: "string" },
            },
        },
        google_realtime: {
            title: "Google Gemini",
            properties: {
                provider: { default: "google_realtime" },
                model: { default: "gemini-3.1-flash-live-preview", examples: ["gemini-3.1-flash-live-preview"] },
                voice: { default: "Puck", examples: ["Puck", "Charon", "Kore", "Fenrir", "Aoede"] },
                language: { default: "en", examples: ["en", "fr"] },
                google_search: { type: "boolean", default: false, description: "Allow Gemini to use Google Search for up-to-date information.", visible_for_models: ["gemini-3.1-flash-live-preview", "gemini-3.8-live"] },
                api_key: { type: "string" },
            },
        },
    },
};

const initialConfig = {
    is_realtime: true,
    realtime: { provider: "openai_realtime", api_key: "test-key", model: "gpt-realtime-2", voice: "alloy", language: "en" },
};

describe("OpenAI speech model selection", () => {
    it("switches to Live under the same provider and saves its relevant settings", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getAllByRole("option", { name: "OpenAI" })).toHaveLength(1);
        expect(screen.queryByText("backend model")).toBeNull();

        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await waitFor(() => expect(screen.getByDisplayValue("Gleam \u2014 English \u00b7 North American \u00b7 Feminine")).toBeTruthy());
        expect(screen.getByText("language")).toBeTruthy();
        expect(screen.getByText("backend model")).toBeTruthy();
        expect(screen.getByText("reasoning effort")).toBeTruthy();
        expect(screen.getByText("web search")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime", api_key: ["test-key"], model: "gpt-live-1", voice: "gleam", language: "en", backend_model: "gpt-5.6-luna", reasoning_effort: "low", web_search: false,
        });
    });

    it("hides Live backend settings when switching back to Realtime", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByText("backend model");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.queryByText("reasoning effort")).toBeNull();
        expect(screen.queryByText("web search")).toBeNull();
        expect(screen.getByText("language")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe("gpt-realtime-2");
        expect(onSave.mock.calls[0][0].realtime).not.toHaveProperty("backend_model");
    });
});

describe("Realtime/Live mode selector", () => {
    const liveInitialConfig = {
        is_realtime: true,
        realtime: {
            provider: "openai_realtime",
            api_key: "live-key",
            model: "gpt-live-1",
            voice: "marin",
            language: "en",
            backend_model: "gpt-5.6-luna",
            reasoning_effort: "low",
            web_search: false,
        },
    };
    const geminiInitialConfig = {
        is_realtime: true,
        realtime: {
            provider: "google_realtime",
            api_key: "g-key",
            model: "gemini-3.1-flash-live-preview",
            voice: "Puck",
            language: "en",
        },
    };

    const activeModeTab = () => screen.getByRole("tab", { name: "Live" }).getAttribute("data-state") === "active" ? "live"
        : screen.getByRole("tab", { name: "Realtime" }).getAttribute("data-state") === "active" ? "realtime"
        : "unknown";

    it("initializes Live for a saved gpt-live-1 config", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-live-1");
        expect(activeModeTab()).toBe("live");
        expect(screen.getByText("Use a Live speech-to-speech model.")).toBeTruthy();
        // Saved marin is preserved as the current selection even though it is
        // outside the explicit Live voice list.
        expect(screen.getByDisplayValue("marin (current)")).toBeTruthy();
        expect(screen.getByText("backend model")).toBeTruthy();
    });

    it("initializes Realtime for a saved OpenAI realtime config", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(activeModeTab()).toBe("realtime");
        expect(screen.queryByText("backend model")).toBeNull();
    });

    it("initializes Realtime for a saved Gemini config", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={geminiInitialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        expect(activeModeTab()).toBe("realtime");
        expect(screen.queryByText("backend model")).toBeNull();
    });

    it("Live model list contains exactly gpt-live-1", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={vi.fn()} />);
        const modelSelect = await screen.findByDisplayValue("gpt-live-1");
        const options = Array.from(modelSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
        expect(options).toEqual(["gpt-live-1"]);
        expect(options).not.toContain("gpt-realtime-2");
        expect(options).not.toContain("gpt-realtime-2.1");
        expect(options).not.toContain("gpt-realtime-2.1-mini");
    });

    it("Live voice list contains the 12 Live voices", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        const voiceSelect = screen.getByDisplayValue("Gleam \u2014 English \u00b7 North American \u00b7 Feminine");
        const options = Array.from(voiceSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
        for (const voice of ["quartz", "ripple", "vesper", "willow", "stone", "gleam", "meridian", "bossa", "tempo", "beacon", "delta", "cinder"]) {
            expect(options).toContain(voice);
        }
        expect(options).toHaveLength(12);
    });

    it("switches Realtime to Live and back, restoring the previous selection", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        expect(activeModeTab()).toBe("live");
        expect(screen.getByText("backend model")).toBeTruthy();
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(activeModeTab()).toBe("realtime");
        expect(screen.queryByText("backend model")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("openai_realtime");
        expect(saved.model).toBe("gpt-realtime-2");
        expect(saved.voice).toBe("alloy");
        expect(saved.api_key).toEqual(["test-key"]);
        expect(saved).not.toHaveProperty("backend_model");
    });

    it("switches Live provider OpenAI to Gemini", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-live-1");
        const providerSelect = screen.getByDisplayValue("OpenAI");
        fireEvent.change(providerSelect, { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(activeModeTab()).toBe("live");
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.queryByText("reasoning effort")).toBeNull();
        expect(screen.queryByText("web search")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("google_realtime");
        expect(saved.model).toBe("gemini-3.8-live");
        expect(saved.api_key).toEqual(["live-key"]);
        expect(saved).not.toHaveProperty("backend_model");
    });

    it("switches Live provider Gemini to OpenAI", async () => {
        const geminiLiveInitial = {
            is_realtime: true,
            realtime: { provider: "google_realtime", api_key: "g-key", model: "gemini-3.1-flash-live-preview", voice: "Puck", language: "en" },
        };
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={geminiLiveInitial} onSave={onSave} />);
        expect(activeModeTab()).toBe("realtime");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        // Entering Live from a Gemini config stays on Gemini directly (3.8 default).
        await screen.findByDisplayValue("gemini-3.8-live");
        fireEvent.change(screen.getByDisplayValue("Google Gemini"), { target: { value: "openai_realtime" } });
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("openai_realtime");
        expect(saved.model).toBe("gpt-live-1");
        expect(saved.api_key).toEqual(["g-key"]);
        expect(saved.backend_model).toBe("gpt-5.6-luna");
    });

    it("preserves saved Live backend values", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime",
            api_key: ["live-key"],
            model: "gpt-live-1",
            voice: "marin",
            language: "en",
            backend_model: "gpt-5.6-luna",
            reasoning_effort: "low",
            web_search: false,
        });
    });

    it("preserves API keys across mode switches", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.api_key).toEqual(["test-key"]);
    });
});

describe("Live OpenAI voice custom value and docs link", () => {
    const liveInitialConfig = {
        is_realtime: true,
        realtime: {
            provider: "openai_realtime",
            api_key: "live-key",
            model: "gpt-live-1",
            voice: "quartz",
            language: "en",
            backend_model: "gpt-5.6-luna",
            reasoning_effort: "low",
            web_search: false,
        },
    };
    const liveDocsUrl = "https://developers.openai.com/api/docs/guides/live-conversations#voice-options";

    it("shows predefined options and custom value entry", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-live-1");
        expect(document.getElementById("live-voice-custom-input")).toBeTruthy();
        expect(screen.getByRole("link", { name: /View OpenAI Live voice options/ })).toBeTruthy();
    });

    it("enters and saves a custom voice value", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.click(document.getElementById("live-voice-custom-input") as HTMLElement);
        const input = await screen.findByPlaceholderText("Enter voice");
        fireEvent.change(input, { target: { value: "future-voice" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("future-voice");
        expect(onSave.mock.calls[0][0].realtime.model).toBe("gpt-live-1");
    });

    it("preserves a saved custom voice without overwriting", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...liveInitialConfig.realtime, voice: "legacy-voice" } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("legacy-voice (current)");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("legacy-voice");
    });

    it("links to the exact OpenAI voice options URL in a new tab", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={liveInitialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-live-1");
        const link = screen.getByRole("link", { name: /View OpenAI Live voice options/ });
        expect(link.getAttribute("href")).toBe(liveDocsUrl);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
    });

    it("hides the link for Live Gemini", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { provider: "google_realtime", api_key: "g-key", model: "gemini-3.1-flash-live-preview", voice: "Puck", language: "en" } }}
                onSave={vi.fn()}
            />,
        );
        expect(await screen.findByDisplayValue("gemini-3.1-flash-live-preview")).toBeTruthy();
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        // Entering Live from a Gemini config stays on Gemini directly (3.8 default).
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.queryByRole("link", { name: /View OpenAI Live voice options/ })).toBeNull();
    });

    it("hides the link in Realtime mode", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.queryByRole("link", { name: /View OpenAI Live voice options/ })).toBeNull();
    });
});

describe("Live OpenAI default voice", () => {
    const GLEAM_LABEL = "Gleam \u2014 English \u00b7 North American \u00b7 Feminine";
    const CINDER_LABEL = "Cinder \u2014 English \u00b7 Southern U.S. \u00b7 Masculine";
    const DELTA_LABEL = "Delta \u2014 English \u00b7 Southern U.S. \u00b7 Feminine";
    const liveBase = {
        provider: "openai_realtime",
        api_key: "live-key",
        model: "gpt-live-1",
        language: "en",
        backend_model: "gpt-5.6-luna",
        reasoning_effort: "low",
        web_search: false,
    };

    it("defaults a new Live + OpenAI config to gleam", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        expect(screen.getByDisplayValue(GLEAM_LABEL)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("gleam");
    });

    it("preserves an existing saved non-gleam voice", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...liveBase, voice: "cinder" } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gpt-live-1");
        expect(screen.getByDisplayValue(CINDER_LABEL)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("cinder");
    });

    it("preserves an existing custom voice on load and save", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...liveBase, voice: "legacy-voice" } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("legacy-voice (current)");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("legacy-voice");
    });

    it("preserves the selected OpenAI Live voice across mode switches", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        const voiceSelect = screen.getByDisplayValue(GLEAM_LABEL);
        fireEvent.change(voiceSelect, { target: { value: "delta" } });
        await screen.findByDisplayValue(DELTA_LABEL);
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        expect(screen.getByDisplayValue(DELTA_LABEL)).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("delta");
    });
});

describe("Live Gemini Google Search", () => {
    const geminiBase = {
        provider: "google_realtime",
        api_key: "g-key",
        model: "gemini-3.1-flash-live-preview",
        voice: "Puck",
        language: "en",
    };
    const docsUrl = "https://ai.google.dev/gemini-api/docs/live-api/tools";

    const enterLiveGemini = async () => {
        // Saved Gemini configs initialize in Realtime; entering Live stays
        // on the Gemini provider directly.
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
    };

    it("shows the Google Search toggle defaulting to OFF", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(screen.getByText("Google Search")).toBeTruthy();
        expect(screen.getByText("Allow Gemini to use Google Search for up-to-date information.")).toBeTruthy();
        expect(screen.getByText("Off")).toBeTruthy();
        expect(screen.queryByText("On")).toBeNull();
    });

    it("renders an existing google_search=true as ON", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase, google_search: true } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(screen.getByText("On")).toBeTruthy();
    });

    it("toggles and saves the value", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        fireEvent.click(document.getElementById("live-gemini-google-search") as HTMLElement);
        expect(screen.getByText("On")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("google_realtime");
        expect(saved.model).toBe("gemini-3.8-live");
        expect(saved.google_search).toBe(true);
        expect(saved).not.toHaveProperty("backend_model");
        expect(saved).not.toHaveProperty("reasoning_effort");
        expect(saved).not.toHaveProperty("web_search");
    });

    it("preserves the value across Gemini -> OpenAI -> Gemini switches", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        fireEvent.click(document.getElementById("live-gemini-google-search") as HTMLElement);
        expect(screen.getByText("On")).toBeTruthy();
        fireEvent.change(screen.getByDisplayValue("Google Gemini"), { target: { value: "openai_realtime" } });
        await screen.findByDisplayValue("gpt-live-1");
        expect(screen.queryByText("Google Search")).toBeNull();
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.getByText("On")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.google_search).toBe(true);
    });

    it("does not render for Live + OpenAI", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        expect(screen.queryByText("Google Search")).toBeNull();
        expect(screen.queryByRole("link", { name: /View Gemini Live tool documentation/ })).toBeNull();
    });

    it("does not render in the original Realtime tab", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        expect(screen.queryByText("Google Search")).toBeNull();
        expect(screen.queryByRole("link", { name: /View Gemini Live tool documentation/ })).toBeNull();
        // Provider/model/voice options remain the schema-driven originals.
        expect(screen.getByDisplayValue("Puck")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("google_realtime");
        expect(saved.model).toBe("gemini-3.1-flash-live-preview");
        expect(saved).not.toHaveProperty("google_search");
    });

    it("preserves a saved true through a Realtime-tab save", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase, google_search: true } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.google_search).toBe(true);
    });

    it("links to the exact Gemini Live tools documentation", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        const link = screen.getByRole("link", { name: /View Gemini Live tool documentation/ });
        expect(link.getAttribute("href")).toBe(docsUrl);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
    });
});

describe("Live Gemini voices", () => {
    const geminiBase = {
        provider: "google_realtime",
        api_key: "g-key",
        model: "gemini-3.1-flash-live-preview",
        voice: "Puck",
        language: "en",
    };
    const voicesUrl = "https://ai.google.dev/gemini-api/docs/speech-generation#voices";
    const allVoices = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"];

    const enterLiveGemini = async () => {
        // Saved Gemini configs initialize in Realtime; entering Live stays
        // on the Gemini provider directly.
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
    };

    const liveVoiceOptions = () => {
        const voiceSelect = screen.getByDisplayValue("Puck \u2014 Upbeat");
        return Array.from(voiceSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
    };

    it("exposes exactly the 30 documented voices", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(liveVoiceOptions()).toEqual(allVoices);
        for (const voice of ["Zephyr", "Kore", "Callirrhoe", "Achernar", "Zubenelgenubi", "Sulafat"]) {
            expect(liveVoiceOptions()).toContain(voice);
        }
        for (const voice of ["Puck", "Charon", "Kore", "Fenrir", "Aoede"]) {
            expect(liveVoiceOptions()).toContain(voice);
        }
    });

    it("saves a newly added voice as the voice string", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        fireEvent.change(screen.getByDisplayValue("Puck \u2014 Upbeat"), { target: { value: "Sulafat" } });
        await screen.findByDisplayValue("Sulafat \u2014 Warm");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("google_realtime");
        expect(saved.model).toBe("gemini-3.8-live");
        expect(saved.voice).toBe("Sulafat");
    });

    it("keeps the saved voice unchanged when opening the form", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase, voice: "Achernar" } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(screen.getByDisplayValue("Achernar \u2014 Soft")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("Achernar");
    });

    it("supports and preserves a custom voice value", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(document.getElementById("live-gemini-voice-custom-input")).toBeTruthy();
        fireEvent.click(document.getElementById("live-gemini-voice-custom-input") as HTMLElement);
        fireEvent.change(await screen.findByPlaceholderText("Enter voice"), { target: { value: "Future-Voice" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("Future-Voice");
    });

    it("preserves a saved out-of-list voice without overwriting", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase, voice: "Legacy-Voice" } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        expect(screen.getByDisplayValue("Legacy-Voice (current)")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("Legacy-Voice");
    });

    it("links to the exact Gemini voice documentation in a new tab", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        const link = screen.getByRole("link", { name: /View Gemini voice options/ });
        expect(link.getAttribute("href")).toBe(voicesUrl);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
    });

    it("leaves the original Realtime voice list at the original five", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        const voiceSelect = screen.getByDisplayValue("Puck");
        expect(Array.from(voiceSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
            .toEqual(["Puck", "Charon", "Kore", "Fenrir", "Aoede"]);
        expect(screen.queryByRole("link", { name: /View Gemini voice options/ })).toBeNull();
    });

    it("leaves the OpenAI Live voice list unchanged", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        const voiceSelect = screen.getByDisplayValue("Gleam \u2014 English \u00b7 North American \u00b7 Feminine");
        expect(Array.from(voiceSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
            .toEqual(["quartz", "ripple", "vesper", "willow", "stone", "gleam", "meridian", "bossa", "tempo", "beacon", "delta", "cinder"]);
    });

    it("preserves the Gemini voice across Gemini -> OpenAI -> Gemini switches", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        await enterLiveGemini();
        fireEvent.change(screen.getByDisplayValue("Puck \u2014 Upbeat"), { target: { value: "Sulafat" } });
        await screen.findByDisplayValue("Sulafat \u2014 Warm");
        fireEvent.change(screen.getByDisplayValue("Google Gemini"), { target: { value: "openai_realtime" } });
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.getByDisplayValue("Sulafat \u2014 Warm")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("Sulafat");
    });
});

describe("Live Gemini 3.8 models", () => {
    const geminiBase = {
        provider: "google_realtime",
        api_key: "g-key",
        model: "gemini-3.1-flash-live-preview",
        voice: "Puck",
        language: "en",
    };
    const model38 = "gemini-3.8-live";

    const enterLiveGemini = async () => {
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
    };

    const liveModelOptions = (display: string) => {
        const select = screen.getByDisplayValue(display);
        return Array.from(select.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
    };

    it("lists exactly gemini-3.8-live for new Live Gemini", async () => {
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={vi.fn()} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue(model38);
        expect(liveModelOptions(model38)).toEqual([model38]);
    });

    it("defaults new Live Gemini to gemini-3.8-live", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue(model38);
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe(model38);
    });

    it("defaults Live entry from saved 3.1 to 3.8 with no 3.1 option", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await enterLiveGemini();
        // Live Gemini is gemini-3.8-live only: exactly one option, no 3.1,
        // no Extended Thinking.
        expect(liveModelOptions(model38)).toEqual([model38]);
        expect(screen.queryByDisplayValue("gemini-3.1-flash-live-preview")).toBeNull();
        expect(screen.queryByDisplayValue("gemini-3.8-live-extended-thinking")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe(model38);
    });

    it("shows Google Search for Live Gemini", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await enterLiveGemini();
        expect(screen.getByText("Google Search")).toBeTruthy();
        expect(screen.getByText("Allow Gemini to use Google Search for up-to-date information.")).toBeTruthy();
    });

    it("never shows Thinking Level or the extended model", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={vi.fn()}
            />,
        );
        await enterLiveGemini();
        expect(screen.queryByText("thinking level")).toBeNull();
        expect(screen.queryByText("Thinking Level")).toBeNull();
        expect(screen.queryByDisplayValue("gemini-3.8-live-extended-thinking")).toBeNull();
        expect(liveModelOptions(model38)).toEqual([model38]);
    });

    it("saves Live Gemini without thinking_level", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await enterLiveGemini();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe(model38);
        expect(onSave.mock.calls[0][0].realtime).not.toHaveProperty("thinking_level");
    });

    it("keeps voice and search selections on save", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await enterLiveGemini();
        fireEvent.change(screen.getByDisplayValue("Puck \u2014 Upbeat"), { target: { value: "Sulafat" } });
        await screen.findByDisplayValue("Sulafat \u2014 Warm");
        fireEvent.click(document.getElementById("live-gemini-google-search") as HTMLElement);
        expect(screen.getByText("On")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.model).toBe(model38);
        expect(saved.voice).toBe("Sulafat");
        expect(saved.google_search).toBe(true);
        expect(saved).not.toHaveProperty("thinking_level");
    });

    it("original Realtime exposes no 3.8 models and no new fields", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        const modelSelect = screen.getByDisplayValue("gemini-3.1-flash-live-preview");
        expect(Array.from(modelSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
            .toEqual(["gemini-3.1-flash-live-preview"]);
        const voiceSelect = screen.getByDisplayValue("Puck");
        expect(Array.from(voiceSelect.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
            .toEqual(["Puck", "Charon", "Kore", "Fenrir", "Aoede"]);
        expect(screen.queryByText("Google Search")).toBeNull();
        expect(screen.queryByText("thinking level")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved).toEqual({
            provider: "google_realtime",
            api_key: ["g-key"],
            model: "gemini-3.1-flash-live-preview",
            voice: "Puck",
            language: "en",
        });
    });
});

describe("Live Gemini Google Search explicit rendering", () => {
    it("renders without relying on schema metadata", async () => {
        // Simulate an older backend schema without google_search: the Live
        // branch renders the toggle literally, never via generic iteration.
        const realtimeSchemas = { ...(defaults.realtime as Record<string, ProviderSchema>) };
        const geminiSchema = {
            ...realtimeSchemas["google_realtime"],
            properties: { ...realtimeSchemas["google_realtime"].properties },
        };
        delete geminiSchema.properties["google_search"];
        const strippedDefaults = {
            ...defaults,
            realtime: { ...realtimeSchemas, google_realtime: geminiSchema },
        } as ServiceConfigurationDefaults;
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={strippedDefaults}
                initialConfig={{
                    is_realtime: true,
                    realtime: {
                        provider: "google_realtime",
                        api_key: "g-key",
                        model: "gemini-3.1-flash-live-preview",
                        voice: "Puck",
                        language: "en",
                    },
                }}
                onSave={vi.fn()}
            />,
        );
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.getByText("Google Search")).toBeTruthy();
        expect(screen.getByText("Allow Gemini to use Google Search for up-to-date information.")).toBeTruthy();
        expect(screen.getByText("Off")).toBeTruthy();
        expect(document.getElementById("live-gemini-google-search")).toBeTruthy();
        const link = screen.getByRole("link", { name: /View Gemini Live tool documentation/ });
        expect(link.getAttribute("href")).toBe("https://ai.google.dev/gemini-api/docs/live-api/tools");
        expect(link.getAttribute("target")).toBe("_blank");
    });
});

describe("Realtime baseline parity (3beecf23 oracle)", () => {
    // These tests pin the ORIGINAL Dograh Realtime behavior against the
    // pre-Live baseline: schema-driven rendering with no Live awareness.
    // The Realtime branch implements the baseline renderer verbatim; the
    // only intentional additions around it are the mode toggle itself.
    const openaiBase = {
        provider: "openai_realtime",
        api_key: "test-key",
        model: "gpt-realtime-2",
        voice: "alloy",
        language: "en",
    };

    const optionsOf = (display: string) =>
        Array.from(screen.getByDisplayValue(display).querySelectorAll("option")).map(
            (o) => (o as HTMLOptionElement).value,
        );

    it("renders provider options in schema order with schema titles", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(optionsOf("OpenAI")).toEqual(["openai_realtime", "google_realtime"]);
        expect(screen.getByRole("option", { name: "Google Gemini" })).toBeTruthy();
    });

    it("renders model and voice options from schema examples", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(optionsOf("gpt-realtime-2")).toEqual(["gpt-live-1", "gpt-realtime-2.1", "gpt-realtime-2"]);
        expect(optionsOf("Alloy")).toEqual(["alloy", "marin", "cedar"]);
    });

    it("keeps baseline field order", async () => {
        const { container } = render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(Array.from(container.querySelectorAll("label")).map((l) => l.textContent)).toEqual([
            "Provider",
            "model",
            "voice",
            "language",
            "API Key(s)",
        ]);
    });

    it("applies hidden_for_models and model_options voice reset in Realtime", async () => {
        const realtimeSchemas = { ...(defaults.realtime as Record<string, ProviderSchema>) };
        const openai = {
            ...realtimeSchemas["openai_realtime"],
            properties: {
                ...realtimeSchemas["openai_realtime"].properties,
                language: { default: "en", examples: ["en", "fr"], hidden_for_models: ["gpt-live-1"] },
            },
        };
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={{ ...defaults, realtime: { ...realtimeSchemas, openai_realtime: openai } }}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={vi.fn()}
            />,
        );
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await screen.findByDisplayValue("Marin");
        // Baseline visibility rule: language hidden for this model...
        expect(screen.queryByText("language")).toBeNull();
        // ...while Live-only backend fields stay out of Realtime by design
        // (the mandated tab-architecture divergence from the tab-less baseline).
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.queryByText("reasoning effort")).toBeNull();
        expect(screen.queryByText("web search")).toBeNull();
    });

    it("resets voice from model_options on model switch", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={onSave}
            />,
        );
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await screen.findByDisplayValue("Marin");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.model).toBe("gpt-live-1");
        expect(saved.voice).toBe("marin");
        expect(saved).not.toHaveProperty("backend_model");
    });

    it("custom input matches baseline detection and toggle", async () => {
        const realtimeSchemas = { ...(defaults.realtime as Record<string, ProviderSchema>) };
        const openai = {
            ...realtimeSchemas["openai_realtime"],
            properties: {
                ...realtimeSchemas["openai_realtime"].properties,
                voice: { default: "alloy", examples: ["alloy", "marin"], allow_custom_input: true },
            },
        };
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={{ ...defaults, realtime: { ...realtimeSchemas, openai_realtime: openai } }}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase, voice: "my-voice" } }}
                onSave={onSave}
            />,
        );
        // Out-of-list saved value loads as custom text input (baseline detection).
        const customInput = await screen.findByPlaceholderText("Enter voice");
        expect((customInput as HTMLInputElement).value).toBe("my-voice");
        // Unchecking restores the first dropdown option (baseline behavior;
        // the custom-input branch renders raw values, as in the baseline).
        fireEvent.click(document.getElementById("custom-input-realtime_voice") as HTMLElement);
        await screen.findByDisplayValue("alloy");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("alloy");
    });

    it("api key add/remove/save matches baseline", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getByText("API Key(s)")).toBeTruthy();
        expect(screen.getByRole("button", { name: "Add API Key" })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
        const inputs = screen.getAllByPlaceholderText("Enter API key");
        expect(inputs).toHaveLength(2);
        fireEvent.change(inputs[0], { target: { value: "k1" } });
        fireEvent.change(inputs[1], { target: { value: "k2" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.api_key).toEqual(["k1", "k2"]);
    });

    it("provider switching resets to provider defaults", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        expect(screen.getByDisplayValue("Puck")).toBeTruthy();
        // API keys reset (baseline handleProviderChange).
        expect((screen.getByPlaceholderText("Enter API key") as HTMLInputElement).value).toBe("");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "google_realtime",
            model: "gemini-3.1-flash-live-preview",
            voice: "Puck",
            language: "en",
        });
    });

    it("openai realtime save payload shape", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime",
            api_key: ["test-key"],
            model: "gpt-realtime-2",
            voice: "alloy",
            language: "en",
        });
    });

    it("no Live-only controls render in Realtime", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.queryByText("Google Search")).toBeNull();
        expect(screen.queryByText("thinking level")).toBeNull();
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.queryByText("reasoning effort")).toBeNull();
        expect(screen.queryByText("web search")).toBeNull();
        expect(screen.queryByRole("link")).toBeNull();
        expect(screen.queryByText("Enter Custom Value")).toBeNull();
        expect(optionsOf("Alloy")).toEqual(["alloy", "marin", "cedar"]);
    });

    it("Realtime Live Realtime round-trip preserves render and values", async () => {
        const onSave = vi.fn();
        const view = render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        const before = Array.from(view.container.querySelectorAll("label")).map((l) => l.textContent);
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        // Identical render after the round-trip...
        expect(Array.from(view.container.querySelectorAll("label")).map((l) => l.textContent)).toEqual(before);
        // ...and identical save.
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime",
            api_key: ["test-key"],
            model: "gpt-realtime-2",
            voice: "alloy",
            language: "en",
        });
    });
});

describe("Live initial tab resolution", () => {
    const liveTabActive = () =>
        screen.getByRole("tab", { name: "Live" }).getAttribute("data-state") === "active";

    it("initializes Live for a saved gemini-3.8-live config", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{
                    is_realtime: true,
                    realtime: {
                        provider: "google_realtime",
                        api_key: "g-key",
                        model: "gemini-3.8-live",
                        voice: "Puck",
                        language: "en",
                    },
                }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(liveTabActive()).toBe(true);
        expect(screen.getByText("Google Search")).toBeTruthy();
    });

    it("initializes Realtime for a saved gemini-3.1 config", async () => {
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{
                    is_realtime: true,
                    realtime: {
                        provider: "google_realtime",
                        api_key: "g-key",
                        model: "gemini-3.1-flash-live-preview",
                        voice: "Puck",
                        language: "en",
                    },
                }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        expect(liveTabActive()).toBe(false);
        expect(screen.queryByText("Google Search")).toBeNull();
    });

    it("remounting a saved Live Gemini payload stays on Live", async () => {
        const onSave = vi.fn();
        const { unmount } = render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{
                    is_realtime: true,
                    realtime: {
                        provider: "google_realtime",
                        api_key: "g-key",
                        model: "gemini-3.1-flash-live-preview",
                        voice: "Puck",
                        language: "en",
                    },
                }}
                onSave={onSave}
            />,
        );
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const savedRealtime = onSave.mock.calls[0][0].realtime;
        expect(savedRealtime.provider).toBe("google_realtime");
        expect(savedRealtime.model).toBe("gemini-3.8-live");
        unmount();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...savedRealtime } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(liveTabActive()).toBe(true);
    });

    it("remounting a saved Live OpenAI payload stays on Live", async () => {
        const onSave = vi.fn();
        const { unmount } = render(
            <ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const savedRealtime = onSave.mock.calls[0][0].realtime;
        expect(savedRealtime.model).toBe("gpt-live-1");
        unmount();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{ is_realtime: true, realtime: { ...savedRealtime } }}
                onSave={vi.fn()}
            />,
        );
        await screen.findByDisplayValue("gpt-live-1");
        expect(liveTabActive()).toBe(true);
    });

    it("never persists the mode tab value", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={defaults}
                initialConfig={{
                    is_realtime: true,
                    realtime: {
                        provider: "google_realtime",
                        api_key: "g-key",
                        model: "gemini-3.8-live",
                        voice: "Puck",
                        language: "en",
                    },
                }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.8-live");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const payload = JSON.stringify(onSave.mock.calls[0][0]);
        for (const key of ["mode", "architecture", "live_mode", "s2s_mode", "realtimeUiMode"]) {
            expect(payload).not.toContain(`"${key}"`);
        }
    });
});

describe("Live tab-switch state fixes", () => {
    const openaiBase = {
        provider: "openai_realtime",
        api_key: "test-key",
        model: "gpt-realtime-2",
        voice: "alloy",
        language: "en",
    };
    const geminiBase = {
        provider: "google_realtime",
        api_key: "g-key",
        model: "gemini-3.1-flash-live-preview",
        voice: "Puck",
        language: "en",
    };

    it("clicking the active Live tab does not corrupt the Realtime snapshot", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }} onSave={onSave} />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        // Clicking the already-active tab must be a no-op, not a second transition.
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Realtime" }));
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getByDisplayValue("Alloy")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime",
            api_key: ["test-key"],
            model: "gpt-realtime-2",
            voice: "alloy",
            language: "en",
        });
    });

    it("OpenAI custom voice survives Live OpenAI -> Gemini -> OpenAI", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ is_realtime: true, realtime: { ...openaiBase } }} onSave={onSave} />,
        );
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.click(document.getElementById("live-voice-custom-input") as HTMLElement);
        fireEvent.change(await screen.findByPlaceholderText("Enter voice"), { target: { value: "My-Voice" } });
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.8-live");
        fireEvent.change(screen.getByDisplayValue("Google Gemini"), { target: { value: "openai_realtime" } });
        await screen.findByDisplayValue("gpt-live-1");
        const customInput = await screen.findByPlaceholderText("Enter voice");
        expect((customInput as HTMLInputElement).value).toBe("My-Voice");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("My-Voice");
    });

    it("Gemini custom voice survives Live Gemini -> OpenAI -> Gemini", async () => {
        const onSave = vi.fn();
        render(
            <ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }} onSave={onSave} />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
        fireEvent.click(document.getElementById("live-gemini-voice-custom-input") as HTMLElement);
        fireEvent.change(await screen.findByPlaceholderText("Enter voice"), { target: { value: "Gem-Voice" } });
        fireEvent.change(screen.getByDisplayValue("Google Gemini"), { target: { value: "openai_realtime" } });
        await screen.findByDisplayValue("gpt-live-1");
        fireEvent.change(screen.getByDisplayValue("OpenAI"), { target: { value: "google_realtime" } });
        await screen.findByDisplayValue("gemini-3.8-live");
        const customInput = await screen.findByPlaceholderText("Enter voice");
        expect((customInput as HTMLInputElement).value).toBe("Gem-Voice");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0].realtime;
        expect(saved.provider).toBe("google_realtime");
        expect(saved.voice).toBe("Gem-Voice");
    });

    it("saved explicit false is preserved and missing booleans get defaults", async () => {
        const realtimeSchemas = { ...(defaults.realtime as Record<string, ProviderSchema>) };
        const google = {
            ...realtimeSchemas["google_realtime"],
            properties: {
                ...realtimeSchemas["google_realtime"].properties,
                google_search: { type: "boolean", default: true, description: "Search." },
            },
        };
        const localDefaults = { ...defaults, realtime: { ...realtimeSchemas, google_realtime: google } };
        const onSave = vi.fn();
        // Saved explicit false must survive Live entry (not re-defaulted to true).
        const { unmount } = render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={localDefaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase, google_search: false } }}
                onSave={onSave}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.getByText("Off")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.google_search).toBe(false);
        unmount();
        // Genuinely missing value receives the schema default.
        const onSave2 = vi.fn();
        render(
            <ServiceConfigurationForm
                mode="global"
                forceRealtime
                configurationDefaults={localDefaults}
                initialConfig={{ is_realtime: true, realtime: { ...geminiBase } }}
                onSave={onSave2}
            />,
        );
        await screen.findByDisplayValue("gemini-3.1-flash-live-preview");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Live" }));
        await screen.findByDisplayValue("gemini-3.8-live");
        expect(screen.getByText("On")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave2).toHaveBeenCalledOnce());
        expect(onSave2.mock.calls[0][0].realtime.google_search).toBe(true);
    });
});
