import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { PromptCachingSection } from "./PromptCachingSection";

const mocks = vi.hoisted(() => ({
    auth: { loading: false, user: { id: "user" } as { id: string } | null },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/UnsavedChangesContext", () => ({ useUnsavedChanges: vi.fn() }));

beforeEach(() => {
    vi.clearAllMocks();
    mocks.auth = { loading: false, user: { id: "user" } };
});

describe("prompt caching settings", () => {
    it("offers only an off-by-default switch and save button", () => {
        render(<PromptCachingSection workflowConfigurations={resolveWorkflowConfigurations()} onSave={vi.fn()} />);
        const toggle = screen.getByRole("switch", { name: "Optimize prompt caching" });
        expect(toggle.getAttribute("aria-checked")).toBe("false");
        expect((screen.getByRole("button", { name: "Save Prompt Settings" }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(toggle);
        expect(screen.getAllByRole("switch")).toHaveLength(1);
        expect(screen.getAllByRole("button")).toHaveLength(1);
        expect(screen.queryByRole("textbox")).toBeNull();
        expect(screen.queryByRole("spinbutton")).toBeNull();
        expect(screen.queryByText(/Preview uses the saved draft/)).toBeNull();
        expect(screen.queryByRole("button", { name: "Compare on recent calls" })).toBeNull();
    });

    it("saves the opt-in while preserving existing prompt configuration", async () => {
        const save = vi.fn().mockResolvedValue(undefined);
        render(<PromptCachingSection workflowConfigurations={resolveWorkflowConfigurations({
            prompt_variables: { mode: "inline", include_weekday: false, call_context_note: "Existing custom instructions" },
            prompt_caching: { explicit: false },
        })} onSave={save} />);
        fireEvent.click(screen.getByRole("switch", { name: "Optimize prompt caching" }));
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(save.mock.calls[0][0]).toMatchObject({ prompt_variables: { mode: "call_context", include_weekday: false, call_context_note: "Existing custom instructions" } });
        expect(save.mock.calls[0][0]).not.toHaveProperty("prompt_caching");
    });

    it("turns optimization off using the same switch", async () => {
        const save = vi.fn().mockResolvedValue(undefined);
        render(<PromptCachingSection workflowConfigurations={resolveWorkflowConfigurations({ prompt_variables: { mode: "call_context" } })} onSave={save} />);
        fireEvent.click(screen.getByRole("switch", { name: "Optimize prompt caching" }));
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(save.mock.calls[0][0]).toMatchObject({ prompt_variables: { mode: "inline" } });
    });

    it("requires auth before changing settings", () => {
        mocks.auth = { loading: true, user: null };
        render(<PromptCachingSection workflowConfigurations={resolveWorkflowConfigurations()} onSave={vi.fn()} />);
        expect((screen.getByRole("switch", { name: "Optimize prompt caching" }) as HTMLButtonElement).disabled).toBe(true);
        expect((screen.getByRole("button", { name: "Save Prompt Settings" }) as HTMLButtonElement).disabled).toBe(true);
    });

    it("shows a save error and clears it on a successful retry", async () => {
        const save = vi.fn().mockRejectedValueOnce(new Error("Network failed")).mockResolvedValue(undefined);
        render(<PromptCachingSection workflowConfigurations={resolveWorkflowConfigurations()} onSave={save} />);
        fireEvent.click(screen.getByRole("switch", { name: "Optimize prompt caching" }));
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(screen.getByRole("alert").textContent).toContain("Could not save prompt settings");
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(screen.queryByRole("alert")).toBeNull();
        expect(save).toHaveBeenCalledTimes(2);
    });
});
