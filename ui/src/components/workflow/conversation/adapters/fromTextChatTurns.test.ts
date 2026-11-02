import { describe, expect, it } from "vitest";

import { conversationItemsFromTextChatTurns } from "./fromTextChatTurns";

const speech = (text: string) => ({ type: "bot_speech", payload: { text } });

describe("text chat message events", () => {
    it("preserves repeated announcement words inside an earlier reply", () => {
        const preceding = "I'll look that up. Please wait. I will transfer you next.";
        const items = conversationItemsFromTextChatTurns([{
            id: "turn-1", message_events_version: 1,
            events: [
                speech(preceding),
                { type: "tool_call_started", payload: { function_name: "go_to_agent", tool_call_id: "transition-1" } },
                { type: "node_transition", payload: { node_name: "Agent" } },
                speech("Please wait."), speech("Here are your details."),
            ],
            assistant_message: { text: `${preceding}\n\nPlease wait.\n\nHere are your details.` },
        }]);
        expect(items.map(item => item.kind === "message" ? item.text : item.kind)).toEqual([
            preceding, "tool-call", "node-transition", "Please wait.", "Here are your details.",
        ]);
    });

    it("keeps legacy combined messages intact", () => {
        const items = conversationItemsFromTextChatTurns([{
            id: "legacy", events: [speech("Please wait.")],
            assistant_message: { text: "Please wait.\n\nThe answer." },
        }]);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ kind: "message", text: "Please wait.\n\nThe answer." });
    });

    it("preserves delivered speech when a turn fails", () => {
        const items = conversationItemsFromTextChatTurns([{
            id: "failed", status: "failed", message_events_version: 1,
            events: [speech("Please wait."), { type: "execution_error", payload: { message: "Tool failed" } }],
        }]);
        expect(items[0]).toMatchObject({ text: "Please wait." });
        expect(items[1]).toMatchObject({ kind: "notice", text: "Tool failed" });
    });
});
