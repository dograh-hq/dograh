import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowSavedProviders } from "./WorkflowSavedProviders";

const list = vi.fn();

vi.mock("@/client/sdk.gen", () => ({
    listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet: (...args: unknown[]) => list(...args),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "u" }, loading: false }) }));
vi.mock("@/components/ui/select", () => ({
    Select: ({ value, onValueChange, children }: { value: string; onValueChange: (value: string) => void; children: ReactNode }) => (
        <select value={value} onChange={event => onValueChange(event.target.value)}>{children}</select>
    ),
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => <option value={value}>{children}</option>,
}));

const profiles = [
    { name: "openai-cheap", service: "llm", config: { provider: "openai", model: "gpt-4.1-nano" } },
    { name: "openai-prod", service: "llm", config: { provider: "openai", model: "gpt-4.1" } },
    { name: "eleven-eu", service: "tts", config: { provider: "elevenlabs", voice: "Rachel" } },
];

// The mocked Select drops the trigger that carries the label's id, so the
// dropdowns are addressed by their position: LLM, Voice, Transcriber, Speech to Speech.
const SERVICE_ORDER = ["LLM", "Voice", "Transcriber", "Speech to Speech"];
async function dropdown(label: string): Promise<HTMLSelectElement> {
    const selects = await screen.findAllByRole("combobox");
    return selects[SERVICE_ORDER.indexOf(label)] as HTMLSelectElement;
}

describe("WorkflowSavedProviders", () => {
    beforeEach(() => {
        list.mockReset().mockResolvedValue({ data: { profiles } });
    });

    it("offers only the profiles of each service, plus Use default", async () => {
        render(<WorkflowSavedProviders selection={undefined} onSave={vi.fn()} />);
        const llm = await dropdown("LLM");
        const voice = await dropdown("Voice");

        expect([...llm.options].map(option => option.text)).toEqual(["Use default", "openai-cheap", "openai-prod"]);
        expect([...voice.options].map(option => option.text)).toEqual(["Use default", "eleven-eu"]);
        expect(screen.getByRole("button", { name: "Save Saved Providers" })).toHaveProperty("disabled", true);
    });

    it("saves the chosen profiles by name", async () => {
        const onSave = vi.fn().mockResolvedValue(undefined);
        render(<WorkflowSavedProviders selection={undefined} onSave={onSave} />);

        fireEvent.change(await dropdown("LLM"), { target: { value: "openai-cheap" } });
        fireEvent.change((await dropdown("Voice")), { target: { value: "eleven-eu" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Saved Providers" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({
            llm: { profile: "openai-cheap" },
            tts: { profile: "eleven-eu" },
        });
    });

    it("clears a service when set back to Use default", async () => {
        const onSave = vi.fn().mockResolvedValue(undefined);
        render(<WorkflowSavedProviders selection={{ llm: { profile: "openai-cheap" } }} onSave={onSave} />);

        const llm = await dropdown("LLM");
        expect(llm.value).toBe("openai-cheap");
        fireEvent.change(llm, { target: { value: "__default__" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Saved Providers" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledWith({}));
    });

    it("keeps field tweaks for an unchanged profile", async () => {
        const onSave = vi.fn().mockResolvedValue(undefined);
        render(
            <WorkflowSavedProviders
                selection={{ llm: { profile: "openai-cheap", model: "gpt-4.1" } }}
                onSave={onSave}
            />,
        );

        fireEvent.change(await dropdown("Voice"), { target: { value: "eleven-eu" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Saved Providers" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].llm).toEqual({ profile: "openai-cheap", model: "gpt-4.1" });
    });

    it("warns when the selected profile was deleted", async () => {
        render(<WorkflowSavedProviders selection={{ llm: { profile: "gone" } }} onSave={vi.fn()} />);
        expect(await screen.findByText(/no longer exists/)).toBeTruthy();
        expect(screen.getByText("gone (deleted)")).toBeTruthy();
    });

    it("explains when there are no saved providers", async () => {
        list.mockResolvedValue({ data: { profiles: [] } });
        render(<WorkflowSavedProviders selection={undefined} onSave={vi.fn()} />);
        expect(await screen.findByText(/No saved providers yet/)).toBeTruthy();
    });

    it("shows a save error inline", async () => {
        const onSave = vi.fn().mockRejectedValue(new Error("No saved llm profile named 'x'"));
        render(<WorkflowSavedProviders selection={undefined} onSave={onSave} />);

        fireEvent.change(await dropdown("LLM"), { target: { value: "openai-cheap" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Saved Providers" }));

        expect(await screen.findByText("No saved llm profile named 'x'")).toBeTruthy();
    });
});
