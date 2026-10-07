import { describe, expect, it } from "vitest";

import { conversationItemsFromTextChatTurns } from "./fromTextChatTurns";

describe("conversationItemsFromTextChatTurns", () => {
    it("renders transition speech as separate assistant message before tool calls", () => {
        const turns = [
            {
                id: "turn-1",
                created_at: "2026-01-01T00:00:00.000Z",
                user_message: {
                    text: "Check my order",
                    created_at: "2026-01-01T00:00:00.000Z",
                },
                events: [
                    {
                        type: "node_transition",
                        created_at: "2026-01-01T00:00:01.000Z",
                        payload: {
                            node_id: "retrieval_node",
                            node_name: "Retrieval",
                            previous_node_id: "welcome_node",
                            previous_node_name: "Welcome",
                        },
                    },
                    {
                        type: "bot_speech",
                        created_at: "2026-01-01T00:00:01.500Z",
                        payload: {
                            text: "Please give me a moment to review your attachments.",
                        },
                    },
                    {
                        type: "tool_call_started",
                        created_at: "2026-01-01T00:00:02.000Z",
                        payload: {
                            function_name: "lookup_attachments",
                            tool_call_id: "call-1",
                            arguments: { order_id: 123 },
                        },
                    },
                    {
                        type: "tool_call_result",
                        created_at: "2026-01-01T00:00:05.000Z",
                        payload: {
                            function_name: "lookup_attachments",
                            tool_call_id: "call-1",
                            result: { status: "found" },
                        },
                    },
                ],
                assistant_message: {
                    text: "Please give me a moment to review your attachments.\n\nI have reviewed your files and found order 123.",
                    created_at: "2026-01-01T00:00:06.000Z",
                },
            },
        ];

        const items = conversationItemsFromTextChatTurns(turns);

        expect(items.map((item) => ({ kind: item.kind, role: "role" in item ? item.role : undefined, text: "text" in item ? item.text : undefined }))).toEqual([
            {
                kind: "message",
                role: "user",
                text: "Check my order",
            },
            {
                kind: "node-transition",
                role: undefined,
                text: undefined,
            },
            {
                kind: "message",
                role: "assistant",
                text: "Please give me a moment to review your attachments.",
            },
            {
                kind: "tool-call",
                role: undefined,
                text: undefined,
            },
            {
                kind: "message",
                role: "assistant",
                text: "I have reviewed your files and found order 123.",
            },
        ]);
    });

    it("renders ordinary assistant message when no bot_speech event exists", () => {
        const turns = [
            {
                id: "turn-1",
                created_at: "2026-01-01T00:00:00.000Z",
                user_message: {
                    text: "Hello",
                    created_at: "2026-01-01T00:00:00.000Z",
                },
                events: [],
                assistant_message: {
                    text: "Hello, how can I help you?",
                    created_at: "2026-01-01T00:00:01.000Z",
                },
            },
        ];

        const items = conversationItemsFromTextChatTurns(turns);

        expect(items).toHaveLength(2);
        expect(items[0]).toMatchObject({
            kind: "message",
            role: "user",
            text: "Hello",
        });
        expect(items[1]).toMatchObject({
            kind: "message",
            role: "assistant",
            text: "Hello, how can I help you?",
        });
    });

    it("strips transition speech even when preceding assistant text exists", () => {
        const turns = [
            {
                id: "turn-1",
                created_at: "2026-01-01T00:00:00.000Z",
                user_message: {
                    text: "Check my order",
                    created_at: "2026-01-01T00:00:00.000Z",
                },
                events: [
                    {
                        type: "bot_speech",
                        created_at: "2026-01-01T00:00:01.500Z",
                        payload: {
                            text: "Please give me a moment to review your attachments.",
                        },
                    },
                ],
                assistant_message: {
                    text: "Sure thing! Please give me a moment to review your attachments. Here are your details.",
                    created_at: "2026-01-01T00:00:06.000Z",
                },
            },
        ];

        const items = conversationItemsFromTextChatTurns(turns);

        expect(items.map((item) => ({ kind: item.kind, role: "role" in item ? item.role : undefined, text: "text" in item ? item.text : undefined }))).toEqual([
            {
                kind: "message",
                role: "user",
                text: "Check my order",
            },
            {
                kind: "message",
                role: "assistant",
                text: "Please give me a moment to review your attachments.",
            },
            {
                kind: "message",
                role: "assistant",
                text: "Sure thing! Here are your details.",
            },
        ]);
    });
});
