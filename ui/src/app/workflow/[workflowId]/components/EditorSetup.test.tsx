import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FlowNode } from "@/components/flow/types";
import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { describeVariables, EditorSetup, type EditorView, isEditorView, referencedVariables } from "./EditorSetup";

const model = { summary: "model-a / Alice" };
vi.mock("@/components/model-connections/useWorkflowModelOverride", () => ({ useWorkflowModelOverride: () => model }));
vi.mock("@/components/model-connections/WorkflowModelPicker", () => ({ WorkflowModelPicker: () => <div>Model picker</div> }));
vi.mock("../settings/WorkflowSettingsSections", () => ({
    TemplateVariablesSection: () => <div>Variables form</div>,
    WorkflowSettingsSections: ({ hide }: { hide: string[] }) => <div>Sections without {hide.join(", ")}</div>,
}));

const nodes = [{ id: "start", type: "start", position: { x: 0, y: 0 }, data: { prompt: "Greet {{ customer_name }} about {{order_id}}" } }] as FlowNode[];

function renderSetup(view: EditorView, overrides: Partial<Parameters<typeof EditorSetup>[0]> = {}) {
    const onViewChange = vi.fn();
    render(<EditorSetup
        view={view} onViewChange={onViewChange} workflowId={12} workflowName="Agent" workflowUuid="agent-uuid" nodes={nodes}
        workflowConfigurations={resolveWorkflowConfigurations(null)} templateContextVariables={{ customer_name: "Ada" }} dictionary=""
        defaultCallDispositions={[]} defaultAnswerClassifierPrompt="" textChatInactivityTimeoutConstraints={null} widgetTextDefaults={null}
        saveWorkflowConfigurations={vi.fn(async () => undefined)} saveTemplateContextVariables={vi.fn(async () => undefined)} saveDictionary={vi.fn(async () => undefined)}
        {...overrides}
    />);
    return { onViewChange };
}

const tab = (name: RegExp) => within(screen.getByRole("toolbar", { name: "Editor" })).getByRole("tab", { name });

describe("editor setup", () => {
    it("recognises the views a URL may name", () => {
        expect(isEditorView("model")).toBe(true);
        expect(isEditorView("recordings")).toBe(false);
        expect(isEditorView(null)).toBe(false);
    });

    it("finds the variables prompts reference and which have no value", () => {
        expect(referencedVariables(nodes)).toEqual(["customer_name", "order_id"]);
        expect(describeVariables({ customer_name: "Ada", order_id: " " }, nodes)).toEqual({ text: "1 set, 1 missing", missing: ["order_id"] });
        expect(describeVariables({}, [])).toEqual({ text: "No variables", missing: [] });
    });

    it("offers the four views and switches on click", () => {
        const { onViewChange } = renderSetup("canvas");
        expect(within(screen.getByRole("toolbar", { name: "Editor" })).getAllByRole("tab").map(item => item.textContent)).toEqual(["Canvas", "Model", "Variables", "Settings"]);
        expect(tab(/^Canvas/).getAttribute("aria-selected")).toBe("true");
        expect(screen.queryByRole("region")).toBeNull();
        fireEvent.mouseDown(tab(/^Model/));
        expect(onViewChange).toHaveBeenCalledWith("model");
    });

    it("shows the model configuration in place of the canvas", () => {
        renderSetup("model");
        const region = screen.getByRole("region", { name: "Model configuration" });
        expect(region.textContent).toContain("Model picker");
        expect(within(region).getByRole("link", { name: "model configuration" }).getAttribute("href")).toBe("/model-configurations");
    });

    it("flags referenced variables that have no value", () => {
        renderSetup("variables");
        const region = screen.getByRole("region", { name: "Template variables" });
        expect(within(region).getByRole("status").textContent).toBe("Prompts reference {{order_id}} but no value is set. Set them here to use during test calls.");
        expect(region.textContent).toContain("Variables form");
    });

    it("shows the remaining settings without the sections that have views of their own", () => {
        renderSetup("settings");
        expect(screen.getByRole("region", { name: "Settings" }).textContent).toBe("Sections without models, variables");
    });

    it("waits for the configurations before offering the model and settings views", () => {
        renderSetup("canvas", { workflowConfigurations: null });
        expect(tab(/^Model/).hasAttribute("disabled")).toBe(true);
        expect(tab(/^Settings/).hasAttribute("disabled")).toBe(true);
        expect(tab(/^Variables/).hasAttribute("disabled")).toBe(false);
    });
});
