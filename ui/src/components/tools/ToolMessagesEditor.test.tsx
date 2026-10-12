import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { ToolMessages } from "@/client/types.gen";

import { loadToolMessages, orderedToolMessages, validateToolMessages } from "./toolMessages";
import { ToolMessagesEditor } from "./ToolMessagesEditor";

function Harness({ initial = {} }: { initial?: ToolMessages }) {
    const [value, onChange] = useState(initial);
    return <>
        <ToolMessagesEditor value={value} onChange={onChange} recordings={[]} timeoutMs={5000} />
        <output data-testid="value">{JSON.stringify(value)}</output>
    </>;
}

const saved = () => JSON.parse(screen.getByTestId("value").textContent || "{}");

describe("tool messages", () => {
    it("loads a legacy announcement and respects explicit removal", () => {
        const definition = { type: "http_api", config: { customMessage: "One moment." } };
        expect(loadToolMessages(definition).start?.variants).toEqual([{ type: "text", text: "One moment." }]);
        expect(loadToolMessages({ ...definition, messages: {} })).toEqual({});
        expect(loadToolMessages({ type: "http_api", config: { customMessageType: "audio", customMessageRecordingId: "42" } }).start?.variants)
            .toEqual([{ type: "audio", recording_id: 42 }]);
    });

    it("adds, edits, and removes start variants without persisted IDs", () => {
        render(<Harness />);
        fireEvent.click(screen.getByRole("button", { name: "Add start variant" }));
        fireEvent.change(screen.getByLabelText("start variant 1"), { target: { value: "Checking." } });
        fireEvent.click(screen.getByRole("button", { name: "Add start variant" }));
        fireEvent.change(screen.getByLabelText("start variant 2"), { target: { value: "One moment." } });
        expect(saved().start.variants).toEqual([{ type: "text", text: "Checking." }, { type: "text", text: "One moment." }]);
        fireEvent.click(screen.getByRole("button", { name: "Remove start variant 1" }));
        expect(saved().start.variants).toEqual([{ type: "text", text: "One moment." }]);
        fireEvent.click(screen.getByRole("button", { name: "Remove start variant 1" }));
        expect(saved().start).toBeNull();
        expect(screen.queryByRole("checkbox")).toBeNull();
    });

    it("keeps text and audio controls independent across variants", () => {
        render(<Harness initial={{ start: { variants: [{ type: "text", text: "First." }, { type: "text", text: "Second." }] } }} />);
        const audioRadios = screen.getAllByRole("radio", { name: "Audio" });
        expect(audioRadios[0].id).not.toBe(audioRadios[1].id);
        fireEvent.click(screen.getAllByText("Audio", { selector: "label" })[1]);
        expect(saved().start.variants).toEqual([{ type: "text", text: "First." }, { type: "audio", recording_id: 0 }]);
        expect(screen.getByRole("alert").textContent).toContain("Select a recording");
    });

    it("configures multiple delays, validates duplicate timings, and warns about timeout", () => {
        render(<Harness />);
        fireEvent.click(screen.getByRole("button", { name: "Add delayed update" }));
        fireEvent.change(screen.getByLabelText("delayed 1 variant 1"), { target: { value: "Still checking." } });
        fireEvent.click(screen.getByRole("button", { name: "Add delayed update" }));
        fireEvent.change(screen.getByLabelText("delayed 2 variant 1"), { target: { value: "Thanks for waiting." } });
        expect(screen.getByText(/at or beyond the tool timeout/)).toBeTruthy();
        fireEvent.change(screen.getByLabelText("Delay 2 in milliseconds"), { target: { value: "3000" } });
        expect(screen.getByRole("alert").textContent).toContain("different timing");
        fireEvent.change(screen.getByLabelText("Delay 2 in milliseconds"), { target: { value: "1000" } });
        fireEvent.blur(screen.getByLabelText("Delay 2 in milliseconds"));
        // Rows stay put while editing; the saved definition is sorted by time.
        expect(saved().delayed.map((m: { after_ms: number }) => m.after_ms)).toEqual([3000, 1000]);
        expect(orderedToolMessages(saved()).delayed?.map(m => m.after_ms)).toEqual([1000, 3000]);
        fireEvent.click(screen.getByRole("button", { name: "Remove delayed update 1" }));
        expect(saved().delayed).toHaveLength(1);
        expect(saved().delayed[0].variants[0].text).toBe("Thanks for waiting.");
    });

    it("rejects blank variants and invalid timings before saving", () => {
        expect(validateToolMessages({})).toBeNull();
        expect(validateToolMessages({ start: { variants: [{ type: "text", text: " " }] } })).toContain("characters");
        expect(validateToolMessages({ delayed: [{ after_ms: -1, variants: [{ type: "text", text: "Wait." }] }] })).toContain("whole number");
        expect(validateToolMessages({ delayed: [{ after_ms: 3.5, variants: [{ type: "text", text: "Wait." }] }] })).toContain("whole number");
    });
});
