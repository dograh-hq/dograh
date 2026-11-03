import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AnswerSupervisorSettings } from "@/types/workflow-configurations";

import { AnswerSupervisorFields } from "./AnswerSupervisorFields";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: null, loading: false }) }));

function Harness({ initial = {} }: { initial?: AnswerSupervisorSettings }) {
    const [value, setValue] = useState(initial);
    return <>
        <AnswerSupervisorFields value={value} onChange={setValue} />
        <output data-testid="saved">{JSON.stringify(value)}</output>
    </>;
}

describe("AnswerSupervisorFields", () => {
    it("defaults to disconnecting and keeps screening settings available", () => {
        render(<Harness />);
        expect(screen.getByRole("radio", { name: "Disconnect the call" }).getAttribute("aria-checked")).toBe("true");
        expect(screen.queryByLabelText("Voicemail message")).toBeNull();
        expect(screen.getByLabelText("Screening message")).toBeTruthy();
        expect((screen.getByLabelText("Screening wait (seconds)") as HTMLInputElement).value).toBe("30");
        expect(screen.queryByLabelText("Answer handling")).toBeNull();
        expect(screen.queryByText(/Stage [123]|Existing detection|System Prompt/i)).toBeNull();
    });

    it("saves voicemail and screening text independently", () => {
        render(<Harness />);
        fireEvent.click(screen.getByRole("radio", { name: "Leave a message" }));
        fireEvent.change(screen.getByLabelText("Voicemail message"), { target: { value: "Please call back." } });
        fireEvent.change(screen.getByLabelText("Screening message"), { target: { value: "Alex calling about your appointment." } });
        const config = JSON.parse(screen.getByTestId("saved").textContent!);
        expect(config.voicemail_action).toBe("leave_message");
        expect(config.voicemail_message.text).toBe("Please call back.");
        expect(config.screening_message.text).toBe("Alex calling about your appointment.");
    });

    it.each([
        ["Voicemail message", "voicemail_message"],
        ["Screening message", "screening_message"],
    ] as const)("preserves %s text when toggling formats without selecting a recording", (label, key) => {
        render(<Harness initial={{ voicemail_action: "leave_message" }} />);
        fireEvent.change(screen.getByLabelText(label), { target: { value: "Alex calling about your appointment." } });
        const field = screen.getByRole("group", { name: label });
        const format = within(field).getByLabelText("Message format");

        fireEvent.change(format, { target: { value: "audio" } });
        expect(JSON.parse(screen.getByTestId("saved").textContent!)[key]).toEqual({ text: "Alex calling about your appointment." });

        fireEvent.change(format, { target: { value: "text" } });
        expect((screen.getByLabelText(label) as HTMLTextAreaElement).value).toBe("Alex calling about your appointment.");
        expect(JSON.parse(screen.getByTestId("saved").textContent!)[key]).toEqual({ text: "Alex calling about your appointment." });
    });

    it("clears an old recording when switching voicemail to text", () => {
        render(<Harness initial={{ voicemail_action: "leave_message", voicemail_message: { recording_pk: 9 }, screening_message: { text: "Alex calling." } }} />);
        const field = screen.getByRole("group", { name: "Voicemail message" });
        fireEvent.change(within(field).getByLabelText("Message format"), { target: { value: "text" } });
        fireEvent.change(screen.getByLabelText("Voicemail message"), { target: { value: "Call back." } });
        const config = JSON.parse(screen.getByTestId("saved").textContent!);
        expect(config.voicemail_message).toEqual({ text: "Call back." });
        expect(config.screening_message).toEqual({ text: "Alex calling." });
    });

    it("preserves a message when switching between disconnect and leave a message", () => {
        render(<Harness initial={{ voicemail_action: "leave_message", voicemail_message: { text: "Call back." } }} />);
        fireEvent.click(screen.getByRole("radio", { name: "Disconnect the call" }));
        expect(screen.queryByLabelText("Voicemail message")).toBeNull();
        expect(JSON.parse(screen.getByTestId("saved").textContent!)).toMatchObject({
            voicemail_action: "hangup", voicemail_message: { text: "Call back." },
        });
        fireEvent.click(screen.getByRole("radio", { name: "Leave a message" }));
        expect((screen.getByLabelText("Voicemail message") as HTMLTextAreaElement).value).toBe("Call back.");
    });

    it("uses the start node Delayed Start control instead of a second listening setting", () => {
        render(<Harness />);
        expect(screen.queryByLabelText("Listening window (seconds)")).toBeNull();
        expect(screen.getByText(/Start node.*Delayed Start/)).toBeTruthy();
    });

    it("saves screening wait separately from the ordinary listening window", () => {
        render(<Harness initial={{ listening_window_ms: 1200 }} />);
        fireEvent.change(screen.getByLabelText("Screening wait (seconds)"), { target: { value: "45" } });
        const config = JSON.parse(screen.getByTestId("saved").textContent!);
        expect(config.screening_wait_ms).toBe(45000);
        expect(config.listening_window_ms).toBe(1200);
    });
});
