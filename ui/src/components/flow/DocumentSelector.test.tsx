import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { DocumentResponseSchema } from "@/client/types.gen";

import { DocumentSelector } from "./DocumentSelector";

const doc = (overrides: Partial<DocumentResponseSchema>): DocumentResponseSchema => ({
    id: 1,
    document_uuid: "doc-uuid",
    filename: "faq.md",
    file_size_bytes: 2048,
    file_hash: "hash",
    mime_type: "text/markdown",
    processing_status: "completed",
    total_chunks: 3,
    custom_metadata: {},
    docling_metadata: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    organization_id: 1,
    created_by: 1,
    is_active: true,
    ...overrides,
});

describe("DocumentSelector details links", () => {
    it("links each document to the files page focused on that document", () => {
        render(
            <DocumentSelector
                value={[]}
                onChange={vi.fn()}
                documents={[doc({ document_uuid: "doc-1", filename: "faq.md" })]}
            />,
        );

        const link = screen.getByRole("link", { name: "Open faq.md details" });
        expect(link.getAttribute("href")).toBe("/files?document=doc-1");
        expect(link.getAttribute("target")).toBe("_blank");
    });

    it("does not toggle the document when its details link is clicked", () => {
        const onChange = vi.fn();
        render(
            <DocumentSelector
                value={[]}
                onChange={onChange}
                documents={[doc({ document_uuid: "doc-1", filename: "faq.md" })]}
            />,
        );

        fireEvent.click(screen.getByRole("link", { name: "Open faq.md details" }));

        expect(onChange).not.toHaveBeenCalled();
    });
});
