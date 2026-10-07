import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StreamTextChatMessageResponse, WorkflowRunTextSessionResponse } from "@/client/types.gen";

import type { TextChatSession, TextChatTurn } from "./types";
import { useTextChatSession } from "./useTextChatSession";

const mocks = vi.hoisted(() => ({
    create: vi.fn(), message: vi.fn(), get: vi.fn(), recover: vi.fn(), rewind: vi.fn(), end: vi.fn(), error: vi.fn(),
}));
vi.mock("@/client/sdk.gen", () => ({
    streamTextChatSession: mocks.create,
    streamTextChatMessage: mocks.message,
    getTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdGet: mocks.get,
    recoverTextChatSession: mocks.recover,
    rewindTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdRewindPost: mocks.rewind,
    endTextChatSessionApiV1WorkflowWorkflowIdTextChatSessionsRunIdEndPost: mocks.end,
}));
vi.mock("sonner", () => ({ toast: { error: mocks.error, success: vi.fn() } }));

const now = "2026-01-01T00:00:00Z";
const speech = (text: string) => ({ type: "bot_speech", created_at: now, payload: { text } });
const turn = (id: string, text: string | null): TextChatTurn => ({
    id, status: "completed", created_at: now, message_events_version: 1,
    user_message: text ? { text, created_at: now } : null,
    assistant_message: null, events: [], usage: {},
});
const greeting = { ...turn("greeting", null), events: [speech("Welcome.")] };
const initial: TextChatSession = {
    workflow_run_id: 42, workflow_id: 3, name: "Test", mode: "textchat", state: "running",
    revision: 2, is_completed: false, created_at: now,
    session_data: {
        version: 1, status: "idle", cursor_turn_id: null, turns: [greeting],
        discarded_future: [], simulator: { enabled: false, config: {} },
    },
    checkpoint: { version: 1, anchor_turn_id: null, current_node_id: "start", messages: [], gathered_context: {}, tool_state: {} },
};
const pending: TextChatSession = {
    ...initial, revision: 3,
    session_data: { ...initial.session_data, status: "pending_assistant_turn", turns: [greeting, { ...turn("turn-1", "Check"), status: "pending" }] },
};
const completed: TextChatSession = {
    ...initial, revision: 4,
    session_data: { ...initial.session_data, turns: [greeting, {
        ...turn("turn-1", "Check"),
        assistant_message: { text: "Please wait.\n\nYour result.", created_at: now },
        events: [speech("Please wait."), speech("Your result.")],
    }] },
};

function apiSession(value: TextChatSession): WorkflowRunTextSessionResponse {
    return { ...value, session_data: { ...value.session_data }, checkpoint: { ...value.checkpoint } };
}

async function* completeSession(): AsyncGenerator<StreamTextChatMessageResponse> {
    yield { type: "complete", session: apiSession(initial) };
}
function deferred() {
    let resolve!: () => void;
    const promise = new Promise<void>(done => { resolve = done; });
    return { promise, resolve };
}
async function start() {
    const hook = renderHook(() => useTextChatSession({ workflowId: 3, ready: true, disabled: false }));
    act(() => hook.result.current.startSession());
    await waitFor(() => expect(hook.result.current.session?.revision).toBe(2));
    await waitFor(() => expect(hook.result.current.creatingSession).toBe(false));
    return hook;
}
function messageTexts(items: ReturnType<typeof useTextChatSession>["conversationItems"]) {
    return items.filter(item => item.kind === "message").map(item => item.text);
}

beforeEach(() => {
    vi.resetAllMocks();
    mocks.create.mockImplementation(async () => ({ stream: completeSession() }));
});
afterEach(() => vi.unstubAllGlobals());

describe("streaming Test Chat", () => {
    it("recovers by the client request ID when even the response headers are lost", async () => {
        mocks.create.mockRejectedValue(new Error("Connection lost"));
        mocks.recover.mockResolvedValueOnce({ data: null }).mockResolvedValue({ data: initial });
        const { result } = renderHook(() => useTextChatSession({ workflowId: 3, ready: true, disabled: false }));
        act(() => result.current.startSession());
        await waitFor(() => expect(mocks.recover).toHaveBeenCalledTimes(1));
        expect(result.current.creatingSession).toBe(true);
        expect(result.current.inputDisabled).toBe(true);
        await waitFor(() => expect(result.current.session?.revision).toBe(2), { timeout: 2500 });
        expect(mocks.create).toHaveBeenCalledTimes(1);
        expect(mocks.recover.mock.calls[0][0].path).toEqual({
            workflow_id: 3, request_id: mocks.create.mock.calls[0][0].body.request_id,
        });
    });

    it("allows a fresh start after creation was explicitly rejected", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 402 })));
        mocks.create.mockImplementation(async (options) => {
            await options.fetch("https://api.test/session");
            throw new Error("SSE failed: 402");
        });
        const { result } = renderHook(() => useTextChatSession({ workflowId: 3, ready: true, disabled: false }));
        act(() => result.current.startSession());
        await waitFor(() => expect(result.current.started).toBe(false));
        expect(mocks.recover).not.toHaveBeenCalled();
        expect(mocks.create).toHaveBeenCalledTimes(1);
    });

    it("recovers initialization by the response run ID before receiving any SSE event", async () => {
        vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, {
            headers: { "X-Workflow-Run-Id": "42" },
        })));
        mocks.create.mockImplementation(async (options) => {
            await options.fetch("https://api.test/session");
            return { stream: (async function* () {
                throw new Error("Disconnected before the first event");
            })() };
        });
        mocks.get.mockRejectedValueOnce(new Error("Offline"))
            .mockResolvedValue({ data: initial });
        const { result } = renderHook(() => useTextChatSession({ workflowId: 3, ready: true, disabled: false }));
        act(() => result.current.startSession());
        await waitFor(() => expect(result.current.session?.revision).toBe(2));
        expect(result.current.started).toBe(true);
        expect(result.current.creatingSession).toBe(false);
        expect(mocks.create).toHaveBeenCalledTimes(1);
        expect(mocks.get).toHaveBeenCalledTimes(2);
        expect(mocks.get.mock.calls[0][0].path).toEqual({ workflow_id: 3, run_id: 42 });
    });

    it("shows the announcement while the tool is pending and reconciles without duplicates", async () => {
        const tool = deferred();
        async function* messages(): AsyncGenerator<StreamTextChatMessageResponse> {
            yield { type: "session", session: apiSession(pending) };
            yield { type: "turn_event", turn_id: "turn-1", event: speech("Please wait.") };
            yield { type: "turn_event", turn_id: "turn-1", event: {
                type: "tool_call_started", created_at: now, payload: { function_name: "lookup", tool_call_id: "tool-1" },
            } };
            await tool.promise;
            yield { type: "complete", session: apiSession(completed) };
        }
        mocks.message.mockImplementation(async () => ({ stream: messages() }));
        const { result } = await start();
        act(() => result.current.setDraft("Check"));
        act(() => { void result.current.submitComposer(); });
        await waitFor(() => expect(messageTexts(result.current.conversationItems)).toContain("Please wait."));
        expect(result.current.sendingMessage).toBe(true);
        expect(result.current.inputDisabled).toBe(true);
        expect(result.current.conversationItems.find(item => item.kind === "tool-call")).toMatchObject({ status: "running" });
        expect(messageTexts(result.current.conversationItems)).not.toContain("Your result.");
        expect(mocks.message.mock.calls[0][0].sseMaxRetryAttempts).toBe(1);
        await act(async () => { await result.current.submitComposer(); });
        expect(mocks.message).toHaveBeenCalledTimes(1);
        act(() => tool.resolve());
        await waitFor(() => expect(result.current.sendingMessage).toBe(false));
        expect(messageTexts(result.current.conversationItems)).toEqual(["Welcome.", "Check", "Please wait.", "Your result."]);
    });

    it("recovers a disconnected turn using GET without replaying its POST", async () => {
        async function* messages(): AsyncGenerator<StreamTextChatMessageResponse> {
            yield { type: "session", session: apiSession(pending) };
            yield { type: "turn_event", turn_id: "turn-1", event: speech("Please wait.") };
            throw new Error("Disconnected");
        }
        mocks.message.mockImplementation(async () => ({ stream: messages() }));
        mocks.get.mockResolvedValueOnce({ data: pending }).mockResolvedValue({ data: completed });
        const { result } = await start();
        act(() => result.current.setDraft("Check"));
        act(() => { void result.current.submitComposer(); });
        await waitFor(() => expect(result.current.session?.revision).toBe(4));
        expect(mocks.message).toHaveBeenCalledTimes(1);
        expect(mocks.get).toHaveBeenCalledTimes(2);
        expect(messageTexts(result.current.conversationItems)).toEqual(["Welcome.", "Check", "Please wait.", "Your result."]);
    });

    it("uses the revision returned by rewind when streaming an edited turn", async () => {
        mocks.create.mockImplementation(async () => ({ stream: (async function* () {
            yield { type: "complete", session: apiSession(completed) };
        })() }));
        mocks.rewind.mockResolvedValue({ data: { ...initial, revision: 10 } });
        const edited = {
            ...completed, revision: 12,
            session_data: { ...completed.session_data, turns: [greeting, {
                ...turn("edited-turn", "Edited check"), events: [speech("Updated result.")],
            }] },
        };
        mocks.message.mockImplementation(async () => ({ stream: (async function* () {
            yield { type: "session", session: apiSession({
                ...edited, revision: 11,
                session_data: { ...edited.session_data, status: "pending_assistant_turn", turns: [greeting, {
                    ...turn("edited-turn", "Edited check"), status: "pending",
                }] },
            }) };
            yield { type: "complete", session: apiSession(edited) };
        })() }));
        const { result } = renderHook(() => useTextChatSession({ workflowId: 3, ready: true, disabled: false }));
        act(() => result.current.startSession());
        await waitFor(() => expect(result.current.creatingSession).toBe(false));
        await waitFor(() => expect(result.current.session?.revision).toBe(4));
        act(() => result.current.startEditingTurn(completed.session_data.turns[1]));
        act(() => result.current.setDraft("Edited check"));
        await act(async () => { await result.current.submitComposer(); });
        expect(mocks.message.mock.calls[0][0].body).toEqual({ text: "Edited check", expected_revision: 10 });
        expect(result.current.editingTurnId).toBeNull();
        expect(result.current.session?.revision).toBe(12);
        expect(result.current.turns).toEqual(edited.session_data.turns);
        expect(messageTexts(result.current.conversationItems)).toEqual(["Welcome.", "Edited check", "Updated result."]);
        expect(result.current.draft).toBe("");
        expect(result.current.sendingMessage).toBe(false);
    });

    it("waits for authentication readiness before opening a stream", async () => {
        const { result, rerender } = renderHook(({ ready }) => useTextChatSession({ workflowId: 3, ready, disabled: false }), {
            initialProps: { ready: false },
        });
        act(() => result.current.startSession());
        expect(mocks.create).not.toHaveBeenCalled();
        rerender({ ready: true });
        await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
    });
});
