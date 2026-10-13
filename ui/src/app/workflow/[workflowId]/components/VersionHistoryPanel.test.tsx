import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkflowVersionResponse } from "@/client/types.gen";

import { VersionHistoryPanel } from "./VersionHistoryPanel";

const { updateMetadata } = vi.hoisted(() => ({ updateMetadata: vi.fn() }));
vi.mock("@/client/sdk.gen", () => ({
    updateWorkflowVersionMetadataApiV1WorkflowWorkflowIdVersionsDefinitionIdMetadataPatch: updateMetadata,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

beforeEach(() => vi.clearAllMocks());

const makeVersion = (
    versionNumber: number,
    status: string,
): WorkflowVersionResponse => ({
    id: versionNumber,
    version_number: versionNumber,
    status,
    created_at: "2026-08-10T00:00:00Z",
    published_at: status === "draft" ? null : "2026-08-10T00:00:00Z",
    workflow_json: { nodes: [], edges: [] },
    workflow_configurations: null,
    template_context_variables: null,
});

const renderPanel = ({
    versions,
    hasMore = false,
}: {
    versions: WorkflowVersionResponse[];
    hasMore?: boolean;
}) => {
    const onSelectVersion = vi.fn();
    const onCompareVersion = vi.fn();
    const onVersionUpdated = vi.fn();
    const onClose = vi.fn();

    render(
        <VersionHistoryPanel
            workflowId={42}
            isOpen
            onClose={onClose}
            versions={versions}
            loading={false}
            activeVersionId={versions[0]?.id ?? null}
            onSelectVersion={onSelectVersion}
            onCompareVersion={onCompareVersion}
            comparingVersionId={null}
            hasMore={hasMore}
            loadingMore={false}
            onLoadMore={vi.fn()}
            onVersionUpdated={onVersionUpdated}
        />,
    );

    return { onCompareVersion, onSelectVersion, onVersionUpdated, onClose };
};

describe("VersionHistoryPanel comparisons", () => {
    it("shows the release name and change description alongside its version number", () => {
        renderPanel({ versions: [{
            ...makeVersion(2, "archived"),
            version_name: "Better appointments",
            change_description: "Added confirmation.\nUpdated the greeting.",
        }, makeVersion(1, "published")] });

        expect(screen.getByText("v2")).toBeTruthy();
        expect(screen.getByText("Better appointments")).toBeTruthy();
        expect(screen.getByText(/Added confirmation/).textContent).toBe("Added confirmation.\nUpdated the greeting.");
        expect(screen.getByText("v1")).toBeTruthy();
    });

    it("compares a version without triggering the version-selection action", () => {
        const draft = makeVersion(2, "draft");
        const published = makeVersion(1, "published");
        const { onCompareVersion, onSelectVersion } = renderPanel({
            versions: [draft, published],
        });

        fireEvent.click(screen.getByRole("button", { name: "Compare v2 with v1" }));

        expect(onCompareVersion).toHaveBeenCalledWith(draft);
        expect(onSelectVersion).not.toHaveBeenCalled();
    });

    it("does not offer a comparison for v1 when no older page exists", () => {
        renderPanel({ versions: [makeVersion(1, "published")] });

        expect(screen.queryByRole("button", { name: /Compare v1/ })).toBeNull();
    });

    it("offers comparison on a pagination boundary when older versions exist", () => {
        renderPanel({
            versions: [makeVersion(10, "archived")],
            hasMore: true,
        });

        expect(screen.getByRole("button", {
            name: "Compare v10 with its previous version",
        })).toBeTruthy();
    });
});

describe("VersionHistoryPanel metadata editing", () => {
    it.each(["published", "archived"])("edits %s version details without selecting the version", async (status) => {
        const { onSelectVersion, onVersionUpdated } = renderPanel({ versions: [{
            ...makeVersion(2, status), version_name: "Original name", change_description: "Original notes",
        }] });
        const updated = { id: 2, version_name: "Corrected name", change_description: "Corrected notes" };
        updateMetadata.mockResolvedValueOnce({ data: updated });
        fireEvent.click(screen.getByRole("button", { name: "Edit details for v2" }));
        expect((screen.getByLabelText("Version name (optional)") as HTMLInputElement).value).toBe("Original name");
        expect((screen.getByLabelText("Change description (optional)") as HTMLTextAreaElement).value).toBe("Original notes");
        fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "  Corrected name  " } });
        fireEvent.change(screen.getByLabelText("Change description (optional)"), { target: { value: "  Corrected notes  " } });
        fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
        await waitFor(() => expect(onVersionUpdated).toHaveBeenCalledWith(updated));
        expect(updateMetadata).toHaveBeenCalledWith({
            path: { workflow_id: 42, definition_id: 2 },
            body: { version_name: "Corrected name", change_description: "Corrected notes" },
        });
        expect(onSelectVersion).not.toHaveBeenCalled();
        expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("clears optional fields and prevents duplicate saves or closing during a save", async () => {
        let resolve!: (value: unknown) => void;
        updateMetadata.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
        const { onClose, onVersionUpdated } = renderPanel({ versions: [{
            ...makeVersion(1, "published"), version_name: "Original", change_description: "Notes",
        }] });
        fireEvent.click(screen.getByRole("button", { name: "Edit details for v1" }));
        fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "" } });
        fireEvent.change(screen.getByLabelText("Change description (optional)"), { target: { value: "   " } });
        const form = screen.getByRole("button", { name: "Save changes" }).closest("form")!;
        fireEvent.submit(form);
        fireEvent.submit(form);
        fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
        expect(onClose).not.toHaveBeenCalled();
        expect(screen.getByRole("dialog")).toBeTruthy();
        expect(updateMetadata).toHaveBeenCalledOnce();
        expect(updateMetadata).toHaveBeenCalledWith({
            path: { workflow_id: 42, definition_id: 1 },
            body: { version_name: null, change_description: null },
        });
        resolve({ data: { id: 1, version_name: null, change_description: null } });
        await waitFor(() => expect(onVersionUpdated).toHaveBeenCalledOnce());
    });

    it("keeps edits after validation errors and allows retry", async () => {
        updateMetadata.mockResolvedValueOnce({ error: { detail: [{ msg: "Version name is too long" }] } });
        const { onVersionUpdated } = renderPanel({ versions: [makeVersion(1, "published")] });
        fireEvent.click(screen.getByRole("button", { name: "Edit details for v1" }));
        fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "New name" } });
        fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
        expect((await screen.findByRole("alert")).textContent).toBe("Version name is too long");
        expect((screen.getByLabelText("Version name (optional)") as HTMLInputElement).value).toBe("New name");
        expect(onVersionUpdated).not.toHaveBeenCalled();
        updateMetadata.mockResolvedValueOnce({ data: { id: 1, version_name: "New name", change_description: null } });
        fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
        await waitFor(() => expect(onVersionUpdated).toHaveBeenCalledOnce());
    });

    it("dismisses the editor with Escape without closing history or saving edits", async () => {
        const { onClose } = renderPanel({ versions: [{ ...makeVersion(1, "published"), version_name: "Original" }] });
        const trigger = screen.getByRole("button", { name: "Edit details for v1" });
        fireEvent.click(trigger);
        fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "Unsaved" } });
        fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
        await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
        expect(onClose).not.toHaveBeenCalled();
        expect(updateMetadata).not.toHaveBeenCalled();
        await waitFor(() => expect(document.activeElement).toBe(trigger));
        fireEvent.click(trigger);
        expect((screen.getByLabelText("Version name (optional)") as HTMLInputElement).value).toBe("Original");
    });

    it("does not offer release-note edits for drafts", () => {
        renderPanel({ versions: [makeVersion(2, "draft"), makeVersion(1, "published")] });
        expect(screen.queryByRole("button", { name: "Edit details for v2" })).toBeNull();
        expect(screen.getByRole("button", { name: "Edit details for v1" })).toBeTruthy();
    });
});
