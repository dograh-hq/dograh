import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowVersionResponse } from "@/client/types.gen";

import { VersionHistoryPanel } from "./VersionHistoryPanel";

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

    render(
        <VersionHistoryPanel
            isOpen
            onClose={vi.fn()}
            versions={versions}
            loading={false}
            activeVersionId={versions[0]?.id ?? null}
            onSelectVersion={onSelectVersion}
            onCompareVersion={onCompareVersion}
            comparingVersionId={null}
            hasMore={hasMore}
            loadingMore={false}
            onLoadMore={vi.fn()}
        />,
    );

    return { onCompareVersion, onSelectVersion };
};

describe("VersionHistoryPanel comparisons", () => {
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
