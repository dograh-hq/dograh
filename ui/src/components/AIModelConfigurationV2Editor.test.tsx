import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AIModelConfigurationV2Editor, type ModelConfigurationDefaultsV2 } from "./AIModelConfigurationV2Editor";

vi.mock("@/components/ServiceConfigurationForm", () => ({ ServiceConfigurationForm: () => null }));
vi.mock("@/components/VoiceSelectorModal", () => ({ VoiceSelectorModal: () => null }));

const defaults: ModelConfigurationDefaultsV2 = {
    dograh: {
        voices: ["test-voice"], speeds: [1], languages: ["en"],
        defaults: { voice: "test-voice", speed: 1, language: "en" },
    },
    byok: {
        pipeline: { llm: {}, tts: {}, stt: {}, embeddings: {}, default_providers: {} },
        realtime: { realtime: {}, llm: {}, embeddings: {}, default_providers: {} },
    },
};

const configuration = { version: 2, mode: "dograh", dograh: { api_key: "test-key" } };

describe("Managed Dograh temperature", () => {
    it.each([0, 0.73, null])("saves and reloads %s", async temperature => {
        const onSave = vi.fn();
        const { rerender } = render(<AIModelConfigurationV2Editor defaults={defaults} configuration={configuration} onSave={onSave} />);
        const input = screen.getByLabelText("LLM Temperature") as HTMLInputElement;
        expect(input.value).toBe("");
        fireEvent.change(input, { target: { value: temperature ?? "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0];
        expect(saved.dograh.temperature).toBe(temperature);
        rerender(<AIModelConfigurationV2Editor defaults={defaults} configuration={saved} onSave={onSave} />);
        expect(input.value).toBe(temperature === null ? "" : String(temperature));
    });

    it("can clear a previously saved value to use the provider default", async () => {
        const onSave = vi.fn();
        render(<AIModelConfigurationV2Editor defaults={defaults} onSave={onSave} configuration={{
            version: 2, mode: "dograh", dograh: { api_key: "test-key", temperature: 0.5 },
        }} />);
        const input = screen.getByLabelText("LLM Temperature") as HTMLInputElement;
        expect(input.value).toBe("0.5");
        fireEvent.change(input, { target: { value: "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].dograh.temperature).toBeNull();
    });

    it("rejects negative temperatures before saving", async () => {
        const onSave = vi.fn();
        render(<AIModelConfigurationV2Editor defaults={defaults} configuration={configuration} onSave={onSave} />);
        fireEvent.change(screen.getByLabelText("LLM Temperature"), { target: { value: "-0.1" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        expect(await screen.findByText(/Temperature must be zero or greater/)).toBeTruthy();
        expect(onSave).not.toHaveBeenCalled();
    });
});
