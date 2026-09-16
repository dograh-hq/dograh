import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TransferAgentToolConfig } from "./TransferAgentToolConfig";

const noop = vi.fn();

const AGENTS = [
    { id: 42, name: "Billing" },
    { id: 43, name: "Support" },
];

function Harness({ initialWorkflowId = "" }: { initialWorkflowId?: string }) {
    const [workflowId, setWorkflowId] = useState(initialWorkflowId);
    const [message, setMessage] = useState("Connecting you now.");
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
});
