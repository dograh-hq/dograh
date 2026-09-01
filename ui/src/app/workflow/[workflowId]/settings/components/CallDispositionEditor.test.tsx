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

function Harness({ initialRows = [] }: { initialRows?: CallDispositionRow[] }) {
    const [rows, setRows] = useState(initialRows);
    return <CallDispositionEditor rows={rows} onChange={setRows} />;
}

describe("CallDispositionEditor", () => {
    it("shows the disabled-extraction empty state and adds a complete row", () => {
        render(<Harness />);

        expect(screen.getByText(/No call dispositions configured/i)).toBeTruthy();

        fireEvent.click(screen.getByRole("button", { name: "Add disposition" }));

        const code = screen.getByLabelText("Disposition Code") as HTMLInputElement;
        const description = screen.getByLabelText("Description") as HTMLTextAreaElement;
        expect(code.maxLength).toBe(MAX_CALL_DISPOSITION_CODE_LENGTH);
        expect(description.maxLength).toBe(MAX_CALL_DISPOSITION_DESCRIPTION_LENGTH);
        expect(screen.getByText("Disposition code is required.")).toBeTruthy();
        expect(screen.getByText("Description is required.")).toBeTruthy();
    });

    it("keeps the focused row mounted when an earlier row is removed", () => {
        render(
            <Harness
                initialRows={[
                    { id: "first", code: "qualified", description: "Qualified." },
                    { id: "second", code: "callback", description: "Callback." },
                ]}
            />,
        );

        const secondDescription = screen.getAllByLabelText("Description")[1];
        secondDescription.focus();
        fireEvent.click(screen.getByRole("button", { name: "Remove disposition 1" }));

        expect(screen.getByLabelText("Description")).toBe(secondDescription);
        expect(document.activeElement).toBe(secondDescription);
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
