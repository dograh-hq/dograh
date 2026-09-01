import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
    CallDispositionEditor,
    type CallDispositionRow,
    MAX_CALL_DISPOSITION_CODE_LENGTH,
    MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH,
    MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH,
    normalizeCallDispositions,
    validateCallDispositionRows,
} from "./CallDispositionEditor";

const DEFAULT_DISPOSITIONS = [
    { code: "qualified", description: "The call achieved its goal." },
    { code: "not_interested", description: "The person declined the offer." },
];

function Harness({
    initialRows = [],
    defaultDispositions = DEFAULT_DISPOSITIONS,
}: {
    initialRows?: CallDispositionRow[];
    defaultDispositions?: typeof DEFAULT_DISPOSITIONS;
}) {
    const [rows, setRows] = useState(initialRows);
    return (
        <CallDispositionEditor
            rows={rows}
            onChange={setRows}
            defaultDispositions={defaultDispositions}
        />
    );
}

describe("CallDispositionEditor", () => {
    it("starts disabled, then seeds the configuration dialog with backend defaults", () => {
        render(<Harness />);

        const toggle = screen.getByRole("switch", {
            name: "Extract call disposition at the end of the call",
        });
        expect(toggle.getAttribute("aria-checked")).toBe("false");

        fireEvent.click(toggle);

        expect(screen.getByRole("dialog", { name: "Call disposition extraction" })).toBeTruthy();
        expect(
            screen.getAllByLabelText("Disposition Code").map((input) => (
                (input as HTMLInputElement).value
            )),
        ).toEqual(["qualified", "not_interested"]);

        fireEvent.click(screen.getByRole("button", { name: "Enable extraction" }));

        expect(toggle.getAttribute("aria-checked")).toBe("true");
        expect(screen.getByText(/2 outcomes configured/i)).toBeTruthy();
    });

    it("allows custom code and description pairs when no suggestions are available", () => {
        render(<Harness defaultDispositions={[]} />);

        fireEvent.click(screen.getByRole("switch", {
            name: "Extract call disposition at the end of the call",
        }));

        expect(screen.getByText("Add at least one disposition")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Add custom disposition" }));

        const code = screen.getByLabelText("Disposition Code") as HTMLInputElement;
        const description = screen.getByLabelText("Description") as HTMLTextAreaElement;
        expect(code.maxLength).toBe(MAX_CALL_DISPOSITION_CODE_LENGTH);
        expect(description.maxLength).toBe(MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH);
        expect(screen.getByText("Disposition code is required.")).toBeTruthy();
        expect(screen.getByText("Description is required.")).toBeTruthy();
    });

    it("keeps the focused dialog row mounted when an earlier row is removed", () => {
        render(
            <Harness
                initialRows={[
                    { id: "first", code: "qualified", description: "Qualified." },
                    { id: "second", code: "callback", description: "Callback." },
                ]}
            />,
        );

        fireEvent.click(screen.getByRole("button", { name: "Configure options" }));
        const secondDescription = screen.getAllByLabelText("Description")[1];
        secondDescription.focus();
        fireEvent.click(screen.getByRole("button", { name: "Remove disposition 1" }));

        expect(screen.getByLabelText("Description")).toBe(secondDescription);
        expect(document.activeElement).toBe(secondDescription);
    });

    it("remembers configured options when extraction is toggled off and back on", () => {
        render(
            <Harness
                initialRows={[
                    { id: "custom", code: "call_rescheduled", description: "A new time was booked." },
                ]}
            />,
        );

        const toggle = screen.getByRole("switch", {
            name: "Extract call disposition at the end of the call",
        });
        fireEvent.click(toggle);
        expect(toggle.getAttribute("aria-checked")).toBe("false");

        fireEvent.click(toggle);
        expect((screen.getByLabelText("Disposition Code") as HTMLInputElement).value)
            .toBe("call_rescheduled");
    });

    it("reports duplicate codes case-insensitively", () => {
        const validation = validateCallDispositionRows([
            { id: "first", code: "qualified", description: "Qualified." },
            { id: "second", code: "QUALIFIED", description: "Also qualified." },
        ]);

        expect(validation.isValid).toBe(false);
        expect(validation.rowErrors.first.code).toMatch(/unique/i);
        expect(validation.rowErrors.second.code).toMatch(/unique/i);
    });

    it("rejects unsafe codes and descriptions over the shared prompt budget", () => {
        const validation = validateCallDispositionRows([
            {
                id: "unsafe",
                code: "not interested",
                description: "x".repeat(MAX_CALL_DISPOSITION_DESCRIPTIONS_TOTAL_LENGTH),
            },
            {
                id: "overflow",
                code: "callback_requested",
                description: "x",
            },
        ]);

        expect(validation.isValid).toBe(false);
        expect(validation.rowErrors.unsafe.code).toMatch(/only letters/i);
        expect(validation.rowErrors.unsafe.description).toMatch(/at most/i);
        expect(validation.totalError).toMatch(/total at most/i);
    });

    it("normalizes only persisted fields", () => {
        expect(normalizeCallDispositions([
            {
                id: "ui-only-id",
                code: "  callback_requested  ",
                description: "  The customer asks to be called later.  ",
            },
        ])).toEqual([
            {
                code: "callback_requested",
                description: "The customer asks to be called later.",
            },
        ]);
    });
});
