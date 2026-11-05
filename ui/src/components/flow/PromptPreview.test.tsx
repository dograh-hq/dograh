import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parsePromptToPreviewBlocks, PromptPreview } from "./PromptPreview";

describe("parsePromptToPreviewBlocks", () => {
    it("returns empty array for empty input", () => {
        expect(parsePromptToPreviewBlocks("")).toEqual([]);
    });

    it("parses markdown and conditional blocks correctly", () => {
        const prompt = [
            "# Main Action",
            "Ask user:",
            "1. Amount",
            "2. Income",
            "{% if loan_amount > 500 %}",
            "Ask for IT returns",
            "{% else %}",
            "Standard KYC",
            "{% endif %}",
            "- Thank you",
        ].join("\n");

        const blocks = parsePromptToPreviewBlocks(prompt);
        expect(blocks).toHaveLength(3);
        expect(blocks[0].type).toBe("markdown");
        expect(blocks[1].type).toBe("conditional");

        const cond = blocks[1];
        if (cond.type === "conditional") {
            expect(cond.condition).toBe("loan_amount > 500");
            expect(cond.ifContent).toBe("Ask for IT returns");
            expect(cond.elseContent).toBe("Standard KYC");
        }
    });

    it("parses multi-branch if/elif/else blocks correctly", () => {
        const prompt = [
            "# Main Action",
            "{% if man == 1 %}",
            "yes",
            "{% elif men == 2 %}",
            "no",
            "{% else %}",
            "nana",
            "{% endif %}",
        ].join("\n");

        const blocks = parsePromptToPreviewBlocks(prompt);
        expect(blocks).toHaveLength(2);
        const cond = blocks[1];
        expect(cond.type).toBe("conditional");
        if (cond.type === "conditional") {
            expect(cond.branches).toHaveLength(3);
            expect(cond.branches[0]).toEqual({
                kind: "if",
                condition: "man == 1",
                content: "yes",
            });
            expect(cond.branches[1]).toEqual({
                kind: "elif",
                condition: "men == 2",
                content: "no",
            });
            expect(cond.branches[2]).toEqual({
                kind: "else",
                condition: undefined,
                content: "nana",
            });
        }
    });

    it("parses set variable and custom tag blocks correctly", () => {
        const prompt = [
            '{% set interest_rate = "8.5%" %}',
            "{% do audit_log() %}",
            "Rate is {{ interest_rate }}.",
        ].join("\n");

        const blocks = parsePromptToPreviewBlocks(prompt);
        expect(blocks).toHaveLength(3);
        expect(blocks[0]).toEqual({
            type: "set",
            varName: "interest_rate",
            expression: '"8.5%"',
        });
        expect(blocks[1]).toEqual({
            type: "custom_tag",
            tagContent: "do audit_log()",
        });
        expect(blocks[2].type).toBe("markdown");
    });

    it("parses for loop blocks correctly", () => {
        const prompt = [
            "Your items:",
            "{% for item in order.items %}",
            "- {{ item.name }}: {{ item.price }}",
            "{% endfor %}",
            "End of list.",
        ].join("\n");

        const blocks = parsePromptToPreviewBlocks(prompt);
        expect(blocks).toHaveLength(3);
        expect(blocks[0].type).toBe("markdown");
        expect(blocks[1].type).toBe("loop");
        if (blocks[1].type === "loop") {
            expect(blocks[1].item).toBe("item");
            expect(blocks[1].items).toBe("order.items");
            expect(blocks[1].content).toBe("- {{ item.name }}: {{ item.price }}");
        }
        expect(blocks[2].type).toBe("markdown");
    });

    it("parses for-else loop blocks correctly", () => {
        const prompt = [
            "{% for item in items %}",
            "Item: {{ item }}",
            "{% else %}",
            "No items.",
            "{% endfor %}",
        ].join("\n");

        const blocks = parsePromptToPreviewBlocks(prompt);
        expect(blocks).toHaveLength(1);
        expect(blocks[0].type).toBe("loop");
        if (blocks[0].type === "loop") {
            expect(blocks[0].content).toBe("Item: {{ item }}");
            expect(blocks[0].elseContent).toBe("No items.");
        }
    });
});

describe("PromptPreview Component", () => {
    it("renders empty state placeholder", () => {
        render(<PromptPreview value="" />);
        expect(screen.getByText("No prompt content to preview.")).toBeDefined();
    });

    it("renders set variable cards and formatted custom variables", () => {
        const prompt = [
            '{% set max_loan = "$50,000" %}',
            "Hello {{ customer.name }}, your max loan is {{ max_loan }}.",
        ].join("\n");

        render(<PromptPreview value={prompt} />);
        expect(screen.getByText("SET:")).toBeDefined();
        expect(screen.getAllByText("max_loan")).toHaveLength(2);
        expect(screen.getByText('"$50,000"')).toBeDefined();
        expect(screen.getByText("customer.name")).toBeDefined();
    });

    it("renders multi-branch if/elif/else cards with individual branch headers", () => {
        const prompt = [
            "{% if man == 1 %}",
            "yes",
            "{% elif men == 2 %}",
            "no",
            "{% else %}",
            "nana",
            "{% endif %}",
        ].join("\n");

        render(<PromptPreview value={prompt} />);
        expect(screen.getByText("IF: man == 1")).toBeDefined();
        expect(screen.getByText("yes")).toBeDefined();
        expect(screen.getByText("ELIF: men == 2")).toBeDefined();
        expect(screen.getByText("no")).toBeDefined();
        expect(screen.getByText("ELSE (Fallback)")).toBeDefined();
        expect(screen.getByText("nana")).toBeDefined();
    });

    it("renders formatted headings, lists, and conditional logic cards", () => {
        const prompt = [
            "# MAIN ACTION POINT",
            "1. Loan amount",
            "- Bullet item",
            "{% if is_vip %}",
            "Priority VIP customer",
            "{% else %}",
            "Standard customer",
            "{% endif %}",
        ].join("\n");

        render(<PromptPreview value={prompt} />);
        expect(screen.getByText("MAIN ACTION POINT")).toBeDefined();
        expect(screen.getByText("Loan amount")).toBeDefined();
        expect(screen.getByText("Bullet item")).toBeDefined();
        expect(screen.getByText("IF: is_vip")).toBeDefined();
        expect(screen.getByText("Priority VIP customer")).toBeDefined();
        expect(screen.getByText("ELSE (Fallback)")).toBeDefined();
        expect(screen.getByText("Standard customer")).toBeDefined();
    });

    it("renders for loop cards with iteration header", () => {
        const prompt = [
            "{% for product in cart %}",
            "Buy {{ product.name }}",
            "{% endfor %}",
        ].join("\n");

        render(<PromptPreview value={prompt} />);
        expect(screen.getByText("FOR: product IN cart")).toBeDefined();
        expect(screen.getByText("Buy")).toBeDefined();
        expect(screen.getByText("product.name")).toBeDefined();
    });

    it("renders for loop else empty fallback card", () => {
        const prompt = [
            "{% for item in items %}",
            "Item {{ item }}",
            "{% else %}",
            "No items in list.",
            "{% endfor %}",
        ].join("\n");

        render(<PromptPreview value={prompt} />);
        expect(screen.getByText("FOR: item IN items")).toBeDefined();
        expect(screen.getByText("ELSE (If empty)")).toBeDefined();
        expect(screen.getByText("No items in list.")).toBeDefined();
    });
});
