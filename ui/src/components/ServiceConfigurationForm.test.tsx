import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type ServiceConfigurationDefaults, ServiceConfigurationForm } from "./ServiceConfigurationForm";

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
vi.mock("@/components/ui/tabs", () => ({
    Tabs: ({ children }: { children: ReactNode }) => <>{children}</>,
    TabsList: () => null,
    TabsTrigger: () => null,
    TabsContent: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
        observe() {}
        unobserve() {}
        disconnect() {}
    });
});

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
                language: { default: "en", examples: ["en", "fr"], hidden_for_models: ["gpt-live-1"] },
                backend_model: { default: "gpt-5.4-mini", examples: ["gpt-5.4-mini"], visible_for_models: ["gpt-live-1"] },
                api_key: { type: "string" },
            },
        },
    },
};

const temperatureDefaults: ServiceConfigurationDefaults = {
    llm: {
        openrouter: {
            properties: {
                provider: { default: "openrouter" },
                model: {
                    default: "openai/gpt-4.1",
                    examples: ["openai/gpt-4.1", "anthropic/claude-sonnet-4", "openai/gpt-5-mini"],
                    allow_custom_input: true,
                },
                api_key: { type: "string" },
                temperature: {
                    default: 0.1,
                    anyOf: [{ type: "number", minimum: 0, maximum: 2 }, { type: "null" }],
                    model_constraints: [
                        { pattern: "^openai/gpt-5", supported: false },
                        { pattern: "claude-", maximum: 1 },
                    ],
                },
            },
        },
    },
    tts: {}, stt: {}, embeddings: {}, default_providers: { llm: "openrouter" },
};

describe("LLM temperature", () => {
    it.each([0, 0.73, null])("saves %s without replacing zero or a cleared value", async temperature => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={temperatureDefaults} onSave={onSave} />);
        const input = await screen.findByPlaceholderText("Enter temperature");
        fireEvent.change(input, { target: { value: temperature ?? "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm.temperature).toBe(temperature);
    });

    it.each([null, ""])("normalizes untouched unset temperature (%s) on save", async temperature => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={temperatureDefaults} initialConfig={{ llm: { provider: "openrouter", model: "openai/gpt-4.1", temperature } }} onSave={onSave} />);
        const input = await screen.findByPlaceholderText("Enter temperature") as HTMLInputElement;
        expect(input.value).toBe("");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm.temperature).toBeNull();
    });

    it("updates the allowed range when the model changes", async () => {
        render(<ServiceConfigurationForm mode="global" configurationDefaults={temperatureDefaults} onSave={vi.fn()} />);
        const input = await screen.findByPlaceholderText("Enter temperature") as HTMLInputElement;
        expect(input.max).toBe("2");
        fireEvent.change(input, { target: { value: "1.5" } });
        fireEvent.change(screen.getByDisplayValue("openai/gpt-4.1"), { target: { value: "anthropic/claude-sonnet-4" } });
        expect(input.max).toBe("1");
        expect(input.checkValidity()).toBe(false);
        fireEvent.change(screen.getByDisplayValue("anthropic/claude-sonnet-4"), { target: { value: "openai/gpt-4.1" } });
        expect(input.max).toBe("2");
        expect(input.checkValidity()).toBe(true);
    });

    it("does not save hidden temperature for an unsupported model", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={temperatureDefaults} onSave={onSave} />);
        await screen.findByPlaceholderText("Enter temperature");
        fireEvent.change(screen.getByDisplayValue("openai/gpt-4.1"), { target: { value: "openai/gpt-5-mini" } });
        expect(screen.queryByPlaceholderText("Enter temperature")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm).not.toHaveProperty("temperature");
    });

    it("saves temperature in workflow overrides", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="override" configurationDefaults={temperatureDefaults} currentOverrides={{ llm: { provider: "openrouter", model: "openai/gpt-4.1", temperature: 0.5 } }} onSave={onSave} />);
        const input = await screen.findByPlaceholderText("Enter temperature");
        fireEvent.change(input, { target: { value: "0" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].model_overrides.llm.temperature).toBe(0);
    });
});

describe("Custom OpenAI endpoint temperature", () => {
    const endpointDefaults: ServiceConfigurationDefaults = {
        ...temperatureDefaults,
        default_providers: { llm: "openai" },
        llm: {
            openai: { properties: {
                provider: { default: "openai" },
                model: { type: "string", default: "llama3" },
                base_url: { type: "string", default: "https://api.openai.com/v1" },
                temperature: {
                    default: null,
                    anyOf: [{ type: "number", minimum: 0 }, { type: "null" }],
                    custom_endpoint: {
                        field: "base_url", default_hostname: "api.openai.com", maximum: 2,
                        description: "Limits depend on your server and model.",
                    },
                },
            } },
        },
    };

    it("updates the maximum with the endpoint and saves values above 2 for custom servers", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={endpointDefaults} onSave={onSave} />);
        const input = await screen.findByPlaceholderText("Enter temperature") as HTMLInputElement;
        const endpoint = screen.getByPlaceholderText("Enter base_url");
        expect(input.max).toBe("2");
        fireEvent.change(input, { target: { value: "3" } });
        expect(input.checkValidity()).toBe(false);
        fireEvent.change(endpoint, { target: { value: "http://localhost:11434/v1" } });
        expect(input.max).toBe("");
        expect(input.checkValidity()).toBe(true);
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm.temperature).toBe(3);
        fireEvent.change(endpoint, { target: { value: "https://API.OPENAI.COM/v1/" } });
        expect(input.max).toBe("2");
        expect(input.checkValidity()).toBe(false);
    });

    it("saves an untouched nullable schema default as null", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={endpointDefaults} onSave={onSave} />);
        await screen.findByPlaceholderText("Enter temperature");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm.temperature).toBeNull();
    });
});

const initialConfig = {
    is_realtime: true,
    realtime: { provider: "openai_realtime", api_key: "test-key", model: "gpt-realtime-2", voice: "alloy", language: "en" },
};

describe("OpenAI speech model selection", () => {
    it("switches to Live under the same provider and saves its relevant settings", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getAllByRole("option", { name: "OpenAI" })).toHaveLength(1);
        expect(screen.queryByText("backend model")).toBeNull();

        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await waitFor(() => expect(screen.getByDisplayValue("Marin")).toBeTruthy());
        expect(screen.queryByText("language")).toBeNull();
        expect(screen.getByText("backend model")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime", api_key: ["test-key"], model: "gpt-live-1", voice: "marin", backend_model: "gpt-5.4-mini",
        });
    });

    it("hides Live backend settings when switching back to Realtime", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await screen.findByText("backend model");
        fireEvent.change(modelSelect, { target: { value: "gpt-realtime-2.1" } });
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.getByText("language")).toBeTruthy();
        expect(screen.getByDisplayValue("Marin")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe("gpt-realtime-2.1");
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("marin");
        expect(onSave.mock.calls[0][0].realtime).not.toHaveProperty("backend_model");
    });
});

const listDefaults: ServiceConfigurationDefaults = {
    llm: {
        openrouter: {
            title: "Open Router",
            properties: {
                provider: { default: "openrouter" },
                model: { default: "openai/gpt-4.1" },
                provider_order: { type: "array", items: { type: "string" } },
                api_key: { type: "string" },
            },
        },
    },
    tts: {},
    stt: {
        deepgram: {
            title: "Deepgram",
            properties: {
                provider: { default: "deepgram" },
                model: { default: "nova-3-general", examples: ["nova-3-general", "flux-general-multi"] },
                language: { default: "multi", examples: ["multi", "en", "hi"] },
                language_hints: {
                    type: "array",
                    items: { type: "string" },
                    examples: ["en", "hi", "es"],
                    visible_for_models: ["flux-general-multi"],
                },
                api_key: { type: "string" },
            },
        },
    },
    embeddings: {},
    default_providers: { llm: "openrouter", stt: "deepgram" },
};

const listInitialConfig = {
    llm: { provider: "openrouter", api_key: "llm-key", model: "openai/gpt-4.1", provider_order: ["provider-x"] },
    stt: { provider: "deepgram", api_key: "stt-key", model: "flux-general-multi", language: "multi", language_hints: ["hi"] },
};

describe("List fields", () => {
    it("saves a multi-select in option order", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={listDefaults} initialConfig={listInitialConfig} onSave={onSave} />);
        const hindi = await screen.findByRole("checkbox", { name: "Hindi" });
        await waitFor(() => expect(hindi.getAttribute("aria-checked")).toBe("true"));

        fireEvent.click(screen.getByRole("checkbox", { name: "English" }));
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].stt.language_hints).toEqual(["en", "hi"]);
    });

    it("drops a multi-select hidden for the chosen model", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={listDefaults} initialConfig={listInitialConfig} onSave={onSave} />);
        const modelSelect = await screen.findByDisplayValue("flux-general-multi");

        fireEvent.change(modelSelect, { target: { value: "nova-3-general" } });
        expect(screen.queryByRole("checkbox", { name: "Hindi" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].stt).not.toHaveProperty("language_hints");
    });

    it("saves free-form entries in the order added, skipping blanks", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" configurationDefaults={listDefaults} initialConfig={listInitialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("provider-x");

        fireEvent.click(screen.getByRole("button", { name: "Add" }));
        fireEvent.click(screen.getByRole("button", { name: "Add" }));
        const inputs = screen.getAllByPlaceholderText("Enter provider order");
        expect(inputs).toHaveLength(3);
        fireEvent.change(inputs[1], { target: { value: "  provider-y " } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm.provider_order).toEqual(["provider-x", "provider-y"]);
    });
});
