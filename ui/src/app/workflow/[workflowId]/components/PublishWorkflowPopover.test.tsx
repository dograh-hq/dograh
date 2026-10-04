import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PublishWorkflowPopover } from "./PublishWorkflowPopover";

const { publish, success } = vi.hoisted(() => ({ publish: vi.fn(), success: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({ publishWorkflowApiV1WorkflowWorkflowIdPublishPost: publish }));
vi.mock("sonner", () => ({ toast: { success } }));

function renderPopover(canPublish = true) {
    const onPublished = vi.fn();
    const view = render(<PublishWorkflowPopover workflowId={42} canPublish={canPublish} onPublished={onPublished} />);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    return { onPublished, ...view };
}

function enterNotes() {
    fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "  Better appointments  " } });
    fireEvent.change(screen.getByLabelText("Change description (optional)"), { target: { value: "  Added appointment confirmation.  " } });
}

describe("PublishWorkflowPopover", () => {
    beforeEach(() => vi.clearAllMocks());

    it("publishes with trimmed notes and closes the popover", async () => {
        publish.mockResolvedValue({ data: { id: 7 } });
        const { onPublished } = renderPopover();
        const submit = screen.getByRole("button", { name: "Publish version" });
        expect((submit as HTMLButtonElement).disabled).toBe(false);
        expect(publish).not.toHaveBeenCalled();
        enterNotes();
        fireEvent.click(submit);
        await waitFor(() => expect(onPublished).toHaveBeenCalledOnce());
        expect(publish).toHaveBeenCalledWith({
            path: { workflow_id: 42 },
            body: { version_name: "Better appointments", change_description: "Added appointment confirmation." },
        });
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(success).toHaveBeenCalledOnce();
    });

    it.each([
        { name: "", description: "", expected: { version_name: null, change_description: null } },
        { name: "   ", description: "   ", expected: { version_name: null, change_description: null } },
        { name: "  New greeting  ", description: "", expected: { version_name: "New greeting", change_description: null } },
        { name: "", description: "  Updated the greeting.  ", expected: { version_name: null, change_description: "Updated the greeting." } },
    ])("publishes with optional fields: %j", async ({ name, description, expected }) => {
        publish.mockResolvedValue({ data: { id: 7 } });
        const { onPublished } = renderPopover();
        const nameInput = screen.getByLabelText("Version name (optional)") as HTMLInputElement;
        const descriptionInput = screen.getByLabelText("Change description (optional)") as HTMLTextAreaElement;
        expect(nameInput.required).toBe(false);
        expect(descriptionInput.required).toBe(false);
        fireEvent.change(nameInput, { target: { value: name } });
        fireEvent.change(descriptionInput, { target: { value: description } });
        fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
        await waitFor(() => expect(onPublished).toHaveBeenCalledOnce());
        expect(publish).toHaveBeenCalledWith({
            path: { workflow_id: 42 },
            body: expected,
        });
    });

    it("keeps the popover and notes after API validation fails, allowing retry", async () => {
        publish.mockResolvedValueOnce({ error: { detail: [{ msg: "A node is missing its prompt" }] } });
        const { onPublished } = renderPopover();
        enterNotes();
        fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
        expect((await screen.findByRole("alert")).textContent).toBe("A node is missing its prompt");
        expect((screen.getByLabelText("Version name (optional)") as HTMLInputElement).value).toBe("  Better appointments  ");
        expect(onPublished).not.toHaveBeenCalled();
        expect(screen.getByRole("dialog", { name: "Publish version" })).not.toBeNull();
        expect(success).not.toHaveBeenCalled();
        publish.mockResolvedValueOnce({ data: { id: 7 } });
        fireEvent.click(screen.getByRole("button", { name: "Publish version" }));
        await waitFor(() => expect(onPublished).toHaveBeenCalledOnce());
    });

    it("prevents repeated submission and closing while publish is in progress", async () => {
        let resolve!: (value: unknown) => void;
        publish.mockReturnValue(new Promise((done) => { resolve = done; }));
        const { onPublished } = renderPopover();
        enterNotes();
        const form = screen.getByRole("button", { name: "Publish version" }).closest("form")!;
        fireEvent.submit(form);
        fireEvent.submit(form);
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
        expect(screen.getByRole("dialog", { name: "Publish version" })).not.toBeNull();
        expect(publish).toHaveBeenCalledOnce();
        resolve({ data: { id: 7 } });
        await waitFor(() => expect(onPublished).toHaveBeenCalledOnce());
    });

    it("does not publish when the draft is no longer ready", () => {
        const { rerender, onPublished } = renderPopover();
        enterNotes();
        rerender(<PublishWorkflowPopover workflowId={42} canPublish={false} onPublished={onPublished} />);
        const submit = screen.getByRole("button", { name: "Publish version" });
        expect((submit as HTMLButtonElement).disabled).toBe(true);
        fireEvent.submit(submit.closest("form")!);
        expect(publish).not.toHaveBeenCalled();
    });

    it("disables the trigger when the draft cannot be published", () => {
        renderPopover(false);
        expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(publish).not.toHaveBeenCalled();
    });

    it("dismisses with Escape and returns focus to Publish", async () => {
        renderPopover();
        expect(document.activeElement).toBe(screen.getByLabelText("Version name (optional)"));
        fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Publish" })));
        expect(publish).not.toHaveBeenCalled();
    });
});
