import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FlowNode } from "@/components/flow/types";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { describeVariables, EditorSetup, type EditorView, isEditorView, referencedVariables } from "./EditorSetup";

const model = { summary: "model-a / Alice", dirty: false, pendingSave: false };
const sections = { dirty: false };
vi.mock("@/components/model-connections/useWorkflowModelOverride", () => ({ useWorkflowModelOverride: () => model }));
vi.mock("@/components/model-connections/WorkflowModelPicker", () => ({ WorkflowModelPicker: () => <div>Model picker</div> }));
vi.mock("../settings/WorkflowSettingsSections", async () => {
    const { useUnsavedChanges } = await import("@/context/UnsavedChangesContext");
    return {
        TemplateVariablesSection: () => <div>Variables form</div>,
        WorkflowSettingsSections: ({ hide }: { hide: string[] }) => {
            useUnsavedChanges("general", sections.dirty);
            return <div>Sections without {hide.join(", ")}</div>;
        },
    };
});

const nodes = [{ id: "start", type: "start", position: { x: 0, y: 0 }, data: { prompt: "Greet {{ customer_name }} about {{order_id}}" } }] as FlowNode[];

function renderSetup(view: EditorView, overrides: Partial<Parameters<typeof EditorSetup>[0]> = {}) {
    const onViewChange = vi.fn();
    const onSavingChange = vi.fn();
    render(<UnsavedChangesProvider><EditorSetup
        view={view} onViewChange={onViewChange} onSavingChange={onSavingChange} workflowId={12} workflowName="Agent" workflowUuid="agent-uuid" nodes={nodes}
        workflowConfigurations={resolveWorkflowConfigurations(null)} templateContextVariables={{ customer_name: "Ada" }} dictionary=""
        defaultCallDispositions={[]} defaultAnswerClassifierPrompt="" textChatInactivityTimeoutConstraints={null} widgetTextDefaults={null}
        saveWorkflowConfigurations={vi.fn(async () => undefined)} saveTemplateContextVariables={vi.fn(async () => undefined)} saveDictionary={vi.fn(async () => undefined)}
        {...overrides}
    /></UnsavedChangesProvider>);
    return { onViewChange, onSavingChange };
}

const tab = (name: RegExp) => within(screen.getByRole("toolbar", { name: "Editor" })).getByRole("tab", { name });

beforeEach(() => {
    model.dirty = false;
    model.pendingSave = false;
    sections.dirty = false;
});

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
        // Only the agent's own variables count, not what every object inherits.
        expect(describeVariables({}, [{ ...nodes[0], data: { prompt: "{{toString}}" } }] as FlowNode[])).toEqual({ text: "0 set, 1 missing", missing: ["toString"] });
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

    it("waits for the configurations before offering the views that edit them", () => {
        renderSetup("variables", { workflowConfigurations: null });
        expect(tab(/^Model/).hasAttribute("disabled")).toBe(true);
        expect(tab(/^Variables/).hasAttribute("disabled")).toBe(true);
        expect(tab(/^Settings/).hasAttribute("disabled")).toBe(true);
        expect(screen.queryByRole("region")).toBeNull();
    });

    it("asks before leaving a view with unsaved settings", async () => {
        sections.dirty = true;
        const { onViewChange } = renderSetup("settings");
        fireEvent.mouseDown(tab(/^Canvas/));
        const dialog = await screen.findByRole("alertdialog");
        expect(onViewChange).not.toHaveBeenCalled();
        fireEvent.click(within(dialog).getByRole("button", { name: "Discard changes" }));
        expect(onViewChange).toHaveBeenCalledWith("canvas");
    });

    it("tells the editor while a model edit is still saving", () => {
        model.pendingSave = true;
        const { onSavingChange } = renderSetup("model");
        expect(onSavingChange).toHaveBeenLastCalledWith(true);
    });

    it("switches views freely while only the model editor has unsaved edits, since it stays mounted", () => {
        model.dirty = true;
        const { onViewChange } = renderSetup("model");
        fireEvent.mouseDown(tab(/^Settings/));
        expect(onViewChange).toHaveBeenCalledWith("settings");
        expect(screen.queryByRole("alertdialog")).toBeNull();
    });
});
