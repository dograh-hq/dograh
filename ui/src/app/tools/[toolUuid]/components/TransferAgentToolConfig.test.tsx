import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TransferAgentToolConfig, type TransferGreetingMode } from "./TransferAgentToolConfig";

const noop = vi.fn();

const AGENTS = [
    { id: 42, name: "Billing" },
    { id: 43, name: "Support" },
];

function Harness({
    initialWorkflowId = "",
    initialGreetingMode = "start_greeting",
}: {
    initialWorkflowId?: string;
    initialGreetingMode?: TransferGreetingMode;
}) {
    const [workflowId, setWorkflowId] = useState(initialWorkflowId);
    const [message, setMessage] = useState("Connecting you now.");
    const [greetingMode, setGreetingMode] = useState<TransferGreetingMode>(initialGreetingMode);
    const [introduction, setIntroduction] = useState("");
    return (
        <TransferAgentToolConfig
            name="Transfer to Billing"
            onNameChange={noop}
            description="Use for invoice questions"
            onDescriptionChange={noop}
            workflowId={workflowId}
            onWorkflowIdChange={setWorkflowId}
            workflows={AGENTS}
            message={message}
            onMessageChange={setMessage}
            greetingMode={greetingMode}
            onGreetingModeChange={setGreetingMode}
            introduction={introduction}
            onIntroductionChange={setIntroduction}
        />
    );
}

describe("TransferAgentToolConfig", () => {
    it("offers every agent in the organization as the destination", () => {
        render(<Harness />);
        fireEvent.click(screen.getByLabelText("Transfer to agent"));
        for (const agent of AGENTS) {
            expect(screen.queryAllByText(agent.name).length).toBeGreaterThan(0);
        }
    });

    it("shows the agent already configured", () => {
        render(<Harness initialWorkflowId="43" />);
        expect(screen.getByLabelText("Transfer to agent").textContent).toContain(
            "Support",
        );
    });

    it("edits the handover message", () => {
        render(<Harness />);
        const input = screen.getByLabelText("Handover message") as HTMLInputElement;
        expect(input.value).toBe("Connecting you now.");

        fireEvent.change(input, { target: { value: "One moment." } });
        expect(
            (screen.getByLabelText("Handover message") as HTMLInputElement).value,
        ).toBe("One moment.");
    });

    it("plays the destination's greeting by default and asks for no introduction", () => {
        render(<Harness />);
        expect(
            screen.getByLabelText("When the destination agent picks up").textContent,
        ).toContain("Play its greeting, then wait");
        expect(screen.queryByLabelText("Introduction")).toBeNull();
    });

    it("shows the greeting mode already configured", () => {
        render(<Harness initialGreetingMode="introduce_and_continue" />);
        expect(
            screen.getByLabelText("When the destination agent picks up").textContent,
        ).toContain("Introduce itself, then continue");
        expect(screen.getByLabelText("Introduction")).toBeDefined();
    });

    it("asks for the introduction only when the destination introduces itself", () => {
        render(<Harness />);
        const modeSelect = () =>
            screen.getByLabelText("When the destination agent picks up");

        fireEvent.keyDown(modeSelect(), { key: "ArrowDown" });
        expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
            "Play its greeting, then wait",
            "Continue the conversation",
            "Introduce itself, then continue",
        ]);
        fireEvent.click(screen.getByRole("option", { name: "Introduce itself, then continue" }));
        expect(modeSelect().textContent).toContain("Introduce itself, then continue");

        const field = screen.getByLabelText("Introduction") as HTMLTextAreaElement;
        fireEvent.change(field, { target: { value: "Billing here, carrying on." } });
        expect(
            (screen.getByLabelText("Introduction") as HTMLTextAreaElement).value,
        ).toBe("Billing here, carrying on.");

        fireEvent.keyDown(modeSelect(), { key: "ArrowDown" });
        fireEvent.click(screen.getByRole("option", { name: "Continue the conversation" }));
        expect(modeSelect().textContent).toContain("Continue the conversation");
        expect(screen.queryByLabelText("Introduction")).toBeNull();
    });
});
