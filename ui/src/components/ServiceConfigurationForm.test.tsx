import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

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
