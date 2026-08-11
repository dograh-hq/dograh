import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkflowVersionResponse } from "@/client/types.gen";

import { WorkflowVersionDiffDialog } from "./WorkflowVersionDiffDialog";

const makeVersion = (
    id: number,
    versionNumber: number,
    firstPrompt: string,
    secondPrompt: string,
): WorkflowVersionResponse => ({
    id,
    version_number: versionNumber,
    status: "published",
    created_at: "2026-08-10T00:00:00Z",
    published_at: "2026-08-10T00:00:00Z",
    workflow_json: {
        nodes: [{
            id: "node-1",
            data: {
                firstPrompt,
                unchanged: "Keep this line between both changes",
            },
        }],
        edges: [],
    },
    workflow_configurations: {
        secondPrompt,
    },
    template_context_variables: null,
});

const scrollIntoView = vi.fn();

describe("WorkflowVersionDiffDialog change navigation", () => {
    beforeEach(() => {
        scrollIntoView.mockReset();
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
            configurable: true,
            value: scrollIntoView,
        });
    });

    it("moves forward and backward through hunks without changing the compared versions", () => {
        const previousVersion = makeVersion(1, 1, "First old", "Second old");
        const selectedVersion = makeVersion(2, 2, "First new", "Second new");

        render(
            <WorkflowVersionDiffDialog
                open
                onOpenChange={vi.fn()}
                previousVersion={previousVersion}
                selectedVersion={selectedVersion}
            />,
        );

        const previousChangeButton = screen.getByRole("button", {
            name: "Go to previous change",
        });
        const nextChangeButton = screen.getByRole("button", {
            name: "Go to next change",
        });
        expect(screen.getByText("2 changes")).toBeTruthy();
        expect(screen.getByText("Changes v1 → v2")).toBeTruthy();

        fireEvent.click(nextChangeButton);
        expect(screen.getByText("1 / 2")).toBeTruthy();
        expect(scrollIntoView).toHaveBeenCalledTimes(1);

        fireEvent.click(nextChangeButton);
        expect(screen.getByText("2 / 2")).toBeTruthy();
        expect(scrollIntoView).toHaveBeenCalledTimes(2);

        fireEvent.click(previousChangeButton);
        expect(screen.getByText("1 / 2")).toBeTruthy();
        expect(scrollIntoView).toHaveBeenCalledTimes(3);

        fireEvent.click(previousChangeButton);
        expect(screen.getByText("2 / 2")).toBeTruthy();
        expect(scrollIntoView).toHaveBeenCalledTimes(4);
        expect(screen.getByText("Changes v1 → v2")).toBeTruthy();
    });
});
