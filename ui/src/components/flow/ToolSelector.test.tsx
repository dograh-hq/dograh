import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ToolResponse } from "@/client/types.gen";

import { ToolSelector } from "./ToolSelector";

vi.mock("./mcpRefresh", () => ({ refreshMcpTools: vi.fn() }));

const tool = (overrides: Partial<ToolResponse>): ToolResponse => ({
    id: 1,
    tool_uuid: "tool-uuid",
    name: "Tool",
    description: null,
    category: "http_api",
    icon: null,
    icon_color: null,
    status: "active",
    definition: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
});

const httpTool = tool({ tool_uuid: "http-1", name: "Lookup order" });
const mcpTool = tool({
    id: 2,
    tool_uuid: "mcp-1",
    name: "CRM server",
    category: "mcp",
    definition: { config: { discovered_tools: [{ name: "find_contact", description: null }] } },
});

describe("ToolSelector details links", () => {
    it("links each HTTP tool to its details page in a new tab", () => {
        render(<ToolSelector value={[]} onChange={vi.fn()} tools={[httpTool]} />);

        const link = screen.getByRole("link", { name: "Open Lookup order details" });
        expect(link.getAttribute("href")).toBe("/tools/http-1");
        expect(link.getAttribute("target")).toBe("_blank");
    });

    it("does not toggle the tool when its details link is clicked", () => {
        const onChange = vi.fn();
        render(<ToolSelector value={[]} onChange={onChange} tools={[httpTool]} />);

        fireEvent.click(screen.getByRole("link", { name: "Open Lookup order details" }));

        expect(onChange).not.toHaveBeenCalled();
        expect(screen.getByRole("checkbox").getAttribute("aria-checked")).toBe("false");
    });

    it("links each MCP server to its details page", () => {
        render(<ToolSelector value={[]} onChange={vi.fn()} tools={[mcpTool]} />);

        fireEvent.mouseDown(screen.getByRole("tab", { name: "MCP (1)" }));

        expect(
            screen.getByRole("link", { name: "Open CRM server details" }).getAttribute("href"),
        ).toBe("/tools/mcp-1");
    });
});
