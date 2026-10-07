import { describe, expect, it } from "vitest";

import type { RealtimeFeedbackEvent, TtfbKind } from "../types";
import { conversationItemsFromRealtimeFeedbackEvents, isLlmTtfb } from "./fromRealtimeFeedback";

const timestamp = "2026-09-23T10:00:00.000Z";

function ttfbEvent(seconds: number, kind?: TtfbKind): RealtimeFeedbackEvent {
    return {
        type: "rtf-ttfb-metric",
        payload: { ttfb_seconds: seconds, ...(kind ? { kind } : {}) },
        timestamp,
        turn: 1,
    };
}

describe("reasoning delay", () => {
    it("uses the LLM TTFB in stored runs, not the STT or TTS one around it", () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([
            ttfbEvent(0.3, "stt"),
            ttfbEvent(0.8, "llm"),
            ttfbEvent(0.2, "tts"),
            { type: "rtf-bot-text", payload: { text: "Hello" }, timestamp, turn: 1 },
        ]);

        expect(items[0]).toMatchObject({ role: "assistant", reasoningDurationMs: 800 });
    });

    it("reads TTFB without a kind as the LLM's", () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([
            ttfbEvent(0.8),
            { type: "rtf-bot-text", payload: { text: "Hello" }, timestamp, turn: 1 },
        ]);

        expect(items[0]).toMatchObject({ reasoningDurationMs: 800 });
    });

    // The live hook drops non-LLM TTFB with this before it reaches state.
    it("counts only LLM TTFB, and untagged TTFB as LLM", () => {
        expect(isLlmTtfb("llm")).toBe(true);
        expect(isLlmTtfb(undefined)).toBe(true);
        expect(isLlmTtfb("stt")).toBe(false);
        expect(isLlmTtfb("tts")).toBe(false);
    });
});

describe("tools finishing after hangup", () => {
    const start: RealtimeFeedbackEvent = {
        type: "rtf-function-call-start",
        payload: { function_name: "long_wait_http_tool", tool_call_id: "call-2796", arguments: {} },
        timestamp,
        turn: 3,
    };
    const timeout = {
        function_name: "long_wait_http_tool",
        tool_call_id: "call-2796",
        status: "timeout",
        result: { status: "error", error: "Tool execution timed out" },
    };

    it("uses the saved timeout when the call log only has a start event", () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([start], [timeout]);
        expect(items).toEqual([expect.objectContaining({
            toolCallId: "call-2796", status: "timeout", arguments: {}, result: timeout.result,
        })]);
    });

    it("keeps one card when both a result event and a saved outcome exist", () => {
        const end: RealtimeFeedbackEvent = {
            type: "rtf-function-call-end", payload: { ...start.payload, result: "old result" }, timestamp, turn: 3,
        };
        const items = conversationItemsFromRealtimeFeedbackEvents([start, end], [timeout]);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ status: "timeout", result: timeout.result });
    });

    it("shows a saved outcome even if the pipeline never emitted a start event", () => {
        expect(conversationItemsFromRealtimeFeedbackEvents([], [timeout])).toEqual([
            expect.objectContaining({ functionName: "long_wait_http_tool", status: "timeout" }),
        ]);
    });

    it("does not duplicate a result event that has no matching start event", () => {
        const end: RealtimeFeedbackEvent = {
            type: "rtf-function-call-end", payload: { ...start.payload, result: "old result" }, timestamp, turn: 3,
        };
        const items = conversationItemsFromRealtimeFeedbackEvents([end], [timeout]);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ status: "timeout", result: timeout.result });
    });

    it("preserves the event result when a saved outcome omits it", () => {
        const result = { booking_id: "booking-1" };
        const end: RealtimeFeedbackEvent = {
            type: "rtf-function-call-end", payload: { ...start.payload, result }, timestamp, turn: 3,
        };
        const items = conversationItemsFromRealtimeFeedbackEvents([start, end], [{
            function_name: timeout.function_name, tool_call_id: timeout.tool_call_id, status: "completed",
        }]);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ status: "completed", result });
    });

    it("keeps an explicit null result from the saved outcome", () => {
        const end: RealtimeFeedbackEvent = {
            type: "rtf-function-call-end", payload: { ...start.payload, result: "old result" }, timestamp, turn: 3,
        };
        const items = conversationItemsFromRealtimeFeedbackEvents([end], [{ ...timeout, result: null }]);
        expect(items[0]).toMatchObject({ status: "timeout", result: null });
    });

    it.each(["rtf-function-call-start", "rtf-function-call-end"] as const)(
        "restores a missing name from the saved outcome for %s", (type) => {
            const event: RealtimeFeedbackEvent = {
                ...start, type, payload: { tool_call_id: timeout.tool_call_id },
            };
            const items = conversationItemsFromRealtimeFeedbackEvents([event], [timeout]);
            expect(items[0]).toMatchObject({ functionName: "long_wait_http_tool", status: "timeout" });
        }
    );

    it("matches invocation IDs instead of tool names and preserves other running calls", () => {
        const other = { ...start, payload: { ...start.payload, tool_call_id: "another-call" } };
        const items = conversationItemsFromRealtimeFeedbackEvents([start, other], [timeout]);
        expect(items.map((item) => item.kind === "tool-call" && item.status)).toEqual(["timeout", "running"]);
    });

    it.each(["failed", "cancelled", "completed"])("shows a saved %s outcome", (status) => {
        const result = { saved: true };
        const items = conversationItemsFromRealtimeFeedbackEvents([start], [{ ...timeout, status, result }]);
        expect(items[0]).toMatchObject({ status, result });
    });

    it("shows an HTTP error as failed even when handler execution completed", () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([start], [{ ...timeout, status: "completed" }]);
        expect(items[0]).toMatchObject({ status: "failed" });
    });

    it.each([null, {}, [null, {}, { ...timeout, status: "unknown" }]])("ignores invalid saved records: %j", (records) => {
        expect(conversationItemsFromRealtimeFeedbackEvents([start], records)[0]).toMatchObject({ status: "running" });
    });
});
