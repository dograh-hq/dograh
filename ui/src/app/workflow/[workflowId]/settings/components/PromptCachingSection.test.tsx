import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { PromptCachingSection } from "./PromptCachingSection";

const mocks = vi.hoisted(() => ({
    auth: { loading: false, user: { id: "user" } as { id: string } | null },
    preview: vi.fn(), replay: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/context/UnsavedChangesContext", () => ({ useUnsavedChanges: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    previewWorkflowPromptsApiV1WorkflowWorkflowIdPromptPreviewPost: mocks.preview,
    replayWorkflowPromptsApiV1WorkflowWorkflowIdPromptReplayPost: mocks.replay,
}));

const preview = { nodes: [], call_context: null, explicit_cache_available: true, provider: "google_vertex", model: "gemini-3-flash-preview", notices: [], minimum_tokens: 4096, default_note: "Read call variables", italian_note: "Leggi le variabili" };
const variables = { name: "Ada" };
async function settlePreview() { await act(async () => { await vi.advanceTimersByTimeAsync(450); }); }

beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.auth = { loading: false, user: { id: "user" } };
    mocks.preview.mockResolvedValue({ data: preview });
    mocks.replay.mockResolvedValue({ data: { turns: [], valid_turns: 0, failed_turns: 0, action_agreement: null, inline_noise_agreement: null, call_context_noise_agreement: null, semantic_agreement: {}, sample_metrics: {}, notices: [] } });
});
afterEach(() => { vi.useRealTimers(); });

describe("prompt caching settings", () => {
    it("waits for auth and keeps optimization off by default", async () => {
        mocks.auth = { loading: true, user: null };
        const props = { workflowId: 12, workflowConfigurations: resolveWorkflowConfigurations(), templateContextVariables: variables, onSave: vi.fn() };
        const view = render(<PromptCachingSection {...props} />);
        await settlePreview();
        expect(mocks.preview).not.toHaveBeenCalled();
        mocks.auth = { loading: false, user: { id: "user" } };
        view.rerender(<PromptCachingSection {...props} />);
        await settlePreview();
        expect(mocks.preview).toHaveBeenCalledOnce();
        expect(screen.getByRole("switch", { name: "Optimize prompt caching" }).getAttribute("aria-checked")).toBe("false");
        expect(screen.queryByRole("switch", { name: "Gemini explicit context caching" })).toBeNull();
    });

    it.each(["google", "google_vertex", "openai"])("saves one opt-in for %s without a separate provider switch", async provider => {
        mocks.preview.mockResolvedValue({ data: { ...preview, provider, explicit_cache_available: provider !== "openai" } });
        const save = vi.fn().mockResolvedValue(undefined);
        render(<PromptCachingSection workflowId={12} workflowConfigurations={resolveWorkflowConfigurations({ prompt_caching: { explicit: false } })} templateContextVariables={variables} onSave={save} />);
        await settlePreview();
        fireEvent.click(screen.getByRole("switch", { name: "Optimize prompt caching" }));
        await settlePreview();
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(save.mock.calls[0][0]).toMatchObject({ prompt_variables: { mode: "call_context" } });
        expect(save.mock.calls[0][0]).not.toHaveProperty("prompt_caching");
        expect(screen.queryByRole("switch", { name: "Gemini explicit context caching" })).toBeNull();
    });

    it("turns optimization off using the same switch", async () => {
        const save = vi.fn().mockResolvedValue(undefined);
        render(<PromptCachingSection workflowId={12} workflowConfigurations={resolveWorkflowConfigurations({ prompt_variables: { mode: "call_context" }, prompt_caching: { explicit: true } })} templateContextVariables={variables} onSave={save} />);
        await settlePreview();
        fireEvent.click(screen.getByRole("switch", { name: "Optimize prompt caching" }));
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save Prompt Settings" })); });
        expect(save.mock.calls[0][0]).toMatchObject({ prompt_variables: { mode: "inline" } });
        expect(save.mock.calls[0][0]).not.toHaveProperty("prompt_caching");
    });

    it("compares automatic optimization without an explicit-cache option", async () => {
        render(<PromptCachingSection workflowId={12} workflowConfigurations={resolveWorkflowConfigurations()} templateContextVariables={variables} onSave={vi.fn()} />);
        await settlePreview();
        await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Compare on recent calls" })); });
        expect(mocks.replay.mock.calls[0][0].body).toEqual({ prompt_variables: { mode: "call_context", include_weekday: true, call_context_note: null }, limit: 3, max_turns: 10 });
    });

    it("shows validation errors without treating them as successful previews", async () => {
        mocks.preview.mockResolvedValue({ error: { detail: [{ msg: "Invalid sample run", loc: ["body", "sample_run_id"], type: "value_error" }] } });
        render(<PromptCachingSection workflowId={12} workflowConfigurations={resolveWorkflowConfigurations()} templateContextVariables={variables} onSave={vi.fn()} />);
        await settlePreview();
        expect(screen.getByRole("alert").textContent).toContain("Invalid sample run");
    });
});
