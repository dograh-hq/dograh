import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { NodeSpec } from "@/client/types.gen";

import { NodeEditForm } from "./NodeEditForm";

const spec: NodeSpec = {
    name: "startCall",
    display_name: "Start Call",
    description: "Opening settings",
    category: "call_node",
    icon: "Play",
    properties: [
        { name: "greeting", display_name: "Greeting", description: "Opening text", type: "string" },
        {
            name: "allow_greeting_interrupt",
            display_name: "Allow Greeting Interruption",
            description: "Allow caller interruption",
            type: "boolean",
            default: true,
        },
        {
            name: "greeting_interrupt_min_words",
            display_name: "Minimum Words to Interrupt",
            description: "Word threshold",
            type: "number",
            default: 2,
            min_value: 1,
            display_options: { show: { allow_greeting_interrupt: [true] } },
        },
        { name: "prompt", display_name: "Prompt", description: "Agent instructions", type: "string" },
    ],
};

function Form() {
    const [values, setValues] = useState<Record<string, unknown>>({
        allow_greeting_interrupt: true,
        greeting_interrupt_min_words: 2,
    });
    return (
        <NodeEditForm
            spec={spec}
            values={values}
            onChange={setValues}
            context={{ tools: [], documents: [], recordings: [] }}
        />
    );
}

describe("greeting interruption controls", () => {
    it("shows an enabled toggle and a two-word threshold between greeting and prompt", () => {
        const { container } = render(<Form />);
        expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
        expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe("2");
        const labels = [...container.querySelectorAll("label")].map((label) => label.textContent);
        expect(labels).toEqual(expect.arrayContaining([
            "Greeting",
            "Allow Greeting Interruption",
            "Minimum Words to Interrupt",
            "Prompt",
        ]));
        expect(labels.indexOf("Greeting")).toBeLessThan(labels.indexOf("Allow Greeting Interruption"));
        expect(labels.indexOf("Allow Greeting Interruption")).toBeLessThan(labels.indexOf("Minimum Words to Interrupt"));
        expect(labels.indexOf("Minimum Words to Interrupt")).toBeLessThan(labels.indexOf("Prompt"));
    });

    it("hides the threshold without losing its value when interruption is disabled", () => {
        render(<Form />);
        fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "4" } });
        fireEvent.click(screen.getByRole("switch"));
        expect(screen.queryByRole("spinbutton")).toBeNull();
        fireEvent.click(screen.getByRole("switch"));
        expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe("4");
    });
});
