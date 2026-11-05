"use client";

import { Braces, Brain, MessageSquare, Settings, Workflow } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { FlowNode } from "@/components/flow/types";
import { useWorkflowModelOverride } from "@/components/model-connections/useWorkflowModelOverride";
import { WorkflowModelPicker } from "@/components/model-connections/WorkflowModelPicker";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { WorkflowBuilder } from "@/components/workflow-builder/WorkflowBuilder";
import { useUnsavedChanges, useUnsavedChangesContext } from "@/context/UnsavedChangesContext";
import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { TemplateVariablesSection, WorkflowSettingsSections, type WorkflowSettingsState } from "../settings/WorkflowSettingsSections";

export const EDITOR_VIEWS = ["builder", "canvas", "model", "variables", "settings"] as const;
export type EditorView = (typeof EDITOR_VIEWS)[number];

export function isEditorView(value: string | null | undefined): value is EditorView {
    return (EDITOR_VIEWS as readonly string[]).includes(value ?? "");
}

// Keywords and built-in identifiers that should not be treated as user template variables
const JINJA_KEYWORDS = new Set([
    "and", "or", "not", "in", "is", "defined", "undefined",
    "none", "null", "true", "false", "loop", "int", "float",
    "str", "string", "lower", "upper", "default", "fallback",
    "initial_context", "gathered_context", "call",
]);

// `{{name}}` references in prompts and other node text.
const VARIABLE_PATTERN = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s*\||\s*\}\}|\s*\.)/g;
const IDENT_PATTERN = /\b([A-Za-z_][A-Za-z0-9_]*)\b/g;

function isCandidateVariable(name: string, localVars: Set<string>): boolean {
    return (
        !name.startsWith("current_time") &&
        !name.startsWith("current_weekday") &&
        !JINJA_KEYWORDS.has(name) &&
        !localVars.has(name)
    );
}

function extractTemplateStrings(node: FlowNode): string[] {
    const list: string[] = [];
    const data = node.data;
    if (!data || typeof data !== "object") return list;

    for (const key of ["prompt", "greeting", "extraction_prompt"]) {
        const val = (data as Record<string, unknown>)[key];
        if (typeof val === "string" && (val.includes("{{") || val.includes("{%"))) {
            list.push(val);
        }
    }
    const evs = (data as Record<string, unknown>).extraction_variables;
    if (Array.isArray(evs)) {
        for (const ev of evs) {
            if (ev && typeof ev === "object" && typeof (ev as Record<string, unknown>).prompt === "string") {
                const prompt = (ev as Record<string, unknown>).prompt as string;
                if (prompt.includes("{{") || prompt.includes("{%")) {
                    list.push(prompt);
                }
            }
        }
    }
    return list;
}

/** Template variable names referenced anywhere in the node definitions (both {{ var }} and Jinja blocks). */
export function referencedVariables(nodes: FlowNode[]): string[] {
    const names = new Set<string>();

    for (const node of nodes) {
        const templates = extractTemplateStrings(node);

        for (const text of templates) {
            const localVars = new Set<string>();

            // 1. Register loop iteration variables first: {% for (k, v) in items %}
            for (const match of text.matchAll(/\{%-?\s*for\s+([^%]+?)\s+in\s+([^%]+?)-?%\}/g)) {
                const rawTargets = match[1].replace(/[()]/g, "");
                for (const t of rawTargets.split(",")) {
                    const cleaned = t.trim();
                    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(cleaned)) {
                        localVars.add(cleaned);
                    }
                }
                const iterExpr = match[2]
                    .replace(/"(?:[^"\\]|\\.)*"/g, "")
                    .replace(/'(?:[^'\\]|\\.)*'/g, "")
                    .replace(/\.\s*[A-Za-z_][A-Za-z0-9_]*/g, "")
                    .replace(/\|\s*[A-Za-z_][A-Za-z0-9_]*/g, "");
                for (const idMatch of iterExpr.matchAll(IDENT_PATTERN)) {
                    if (isCandidateVariable(idMatch[1], localVars)) names.add(idMatch[1]);
                }
            }

            // 2. Locally defined variables in {% set var = expr %}
            for (const match of text.matchAll(/\{%-?\s*set\s+([^%=]+?)(?:\s*=\s*([\s\S]+?))?-?%\}/g)) {
                const varTarget = match[1].trim().split(".")[0];
                const expr = match[2];
                if (expr) {
                    const strippedExpr = expr
                        .replace(/"(?:[^"\\]|\\.)*"/g, "")
                        .replace(/'(?:[^'\\]|\\.)*'/g, "")
                        .replace(/\.\s*[A-Za-z_][A-Za-z0-9_]*/g, "")
                        .replace(/\|\s*[A-Za-z_][A-Za-z0-9_]*/g, "");
                    for (const idMatch of strippedExpr.matchAll(IDENT_PATTERN)) {
                        if (isCandidateVariable(idMatch[1], localVars)) names.add(idMatch[1]);
                    }
                }
                if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(varTarget)) {
                    localVars.add(varTarget);
                }
            }

            // 3. {{ var }} references
            for (const match of text.matchAll(VARIABLE_PATTERN)) {
                if (isCandidateVariable(match[1], localVars)) names.add(match[1]);
            }

            // 4. Jinja condition references {% if expr %} and {% elif expr %}
            for (const match of text.matchAll(/\{%-?\s*(?:if|elif)\s+([\s\S]+?)-?%\}/g)) {
                const strippedCond = match[1]
                    .replace(/"(?:[^"\\]|\\.)*"/g, "")
                    .replace(/'(?:[^'\\]|\\.)*'/g, "")
                    .replace(/\.\s*[A-Za-z_][A-Za-z0-9_]*/g, "")
                    .replace(/\|\s*[A-Za-z_][A-Za-z0-9_]*/g, "");
                for (const idMatch of strippedCond.matchAll(IDENT_PATTERN)) {
                    if (isCandidateVariable(idMatch[1], localVars)) names.add(idMatch[1]);
                }
            }
        }
    }
    return [...names].sort();
}

export function describeVariables(variables: Record<string, string>, nodes: FlowNode[]): { text: string; missing: string[] } {
    // Values written through the API may not be strings.
    const hasValue = (value: unknown) => String(value ?? "").trim() !== "";
    const set = Object.values(variables).filter(hasValue).length;
    const missing = referencedVariables(nodes).filter(name => !Object.hasOwn(variables, name) || !hasValue(variables[name]));
    if (set === 0 && missing.length === 0) return { text: "No variables", missing };
    return { text: missing.length ? `${set} set, ${missing.length} missing` : `${set} set`, missing };
}

const TAB = "h-7 gap-1.5 px-2.5 text-sm";
const MODEL_SECTION = "models";

/**
 * The editor's view switcher and the views it switches between.
 *
 * The bar floats at the top of the editor body. "Canvas" shows the flow; the
 * other views cover it with the agent's model configuration, its
 * template variables, or the rest of its settings. The tester rail is
 * outside this area, so a voice can be changed and heard without leaving
 * the Model view. The active view is part of the URL, so a refresh keeps it.
 *
 * Must render inside an UnsavedChangesProvider: the settings sections
 * register their unsaved edits there, so leaving a view with a half-edited
 * form asks first, and so does leaving the editor altogether.
 */
type EditorSetupProps = WorkflowSettingsState & {
    view: EditorView;
    onViewChange: (view: EditorView) => void;
    /** A model edit is waiting to save or saving; the editor holds publishing and test calls until it lands. */
    onSavingChange?: (saving: boolean) => void;
    workflowId: number;
    workflowName: string;
    workflowUuid?: string | null;
    nodes: FlowNode[];
    builderDisabled?: boolean;
    builderBusy?: boolean;
    onBuilderSaved?: () => Promise<void>;
    onBuilderBusyChange?: (busy: boolean) => void;
};

export function EditorSetup({ view, onViewChange, onSavingChange, workflowId, workflowName, workflowUuid, nodes, builderDisabled, builderBusy, onBuilderSaved, onBuilderBusyChange, ...state }: EditorSetupProps) {
    // Keep the conversation and any in-flight turn mounted when changing tabs.
    const [builderOpened, setBuilderOpened] = useState(view === "builder");
    useEffect(() => { if (view === "builder") setBuilderOpened(true); }, [view]);
    const { confirmNavigate, dirtySections } = useUnsavedChangesContext();
    const { workflowConfigurations, templateContextVariables, saveWorkflowConfigurations, saveTemplateContextVariables } = state;
    // The configurations and variables arrive together; until then the
    // views that edit them would start from empty forms.
    const ready = workflowConfigurations != null;
    // Hooks cannot be conditional; until the real configurations arrive the
    // views that need them are disabled, so this placeholder is never saved.
    const placeholder = useMemo(() => resolveWorkflowConfigurations(null), []);
    const model = useWorkflowModelOverride({ workflowName, workflowConfigurations: workflowConfigurations ?? placeholder, onSave: saveWorkflowConfigurations });
    // Model edits the autosave cannot finish on its own would be lost on leaving the editor.
    useUnsavedChanges(MODEL_SECTION, model.dirty);
    const { pendingSave } = model;
    useEffect(() => {
        onSavingChange?.(pendingSave);
        return () => onSavingChange?.(false);
    }, [pendingSave, onSavingChange]);
    const variables = useMemo(() => describeVariables(templateContextVariables, nodes), [templateContextVariables, nodes]);

    const switchView = (next: EditorView) => {
        if (builderBusy && next !== "builder") return;
        // The model editor stays mounted across views, so only the other
        // sections' edits are at stake when switching.
        const othersDirty = [...dirtySections].some(id => id !== MODEL_SECTION);
        if (othersDirty) confirmNavigate(() => onViewChange(next));
        else onViewChange(next);
    };

    return <>
        <div className="pointer-events-none absolute inset-x-0 top-4 z-30 flex justify-center">
            <div role="toolbar" aria-label="Editor" className="nodrag nopan pointer-events-auto rounded-xl border bg-card p-1 shadow-md">
                <Tabs value={view} onValueChange={next => switchView(next as EditorView)}>
                    <TabsList className="h-8" aria-label="Editor view">
                        <TabsTrigger value="builder" className={TAB}><MessageSquare className="h-4 w-4" aria-hidden />Builder</TabsTrigger>
                        <TabsTrigger value="canvas" className={TAB} disabled={builderBusy}><Workflow className="h-4 w-4" aria-hidden />Canvas</TabsTrigger>
                        <TabsTrigger value="model" className={TAB} disabled={!ready || builderBusy} title={ready ? model.summary : "Loading…"}>
                            <Brain className="h-4 w-4" aria-hidden />Model
                        </TabsTrigger>
                        <TabsTrigger value="variables" className={TAB} disabled={!ready || builderBusy} title={ready ? variables.text : "Loading…"}>
                            <Braces className="h-4 w-4" aria-hidden />Variables
                        </TabsTrigger>
                        <TabsTrigger value="settings" className={TAB} disabled={!ready || builderBusy}><Settings className="h-4 w-4" aria-hidden />Settings</TabsTrigger>
                    </TabsList>
                </Tabs>
            </div>
        </div>

        {builderOpened && <section aria-label="Agent builder" hidden={view !== "builder"} className="absolute inset-0 z-20 overflow-y-auto bg-background">
            <div className="mx-auto w-full max-w-[1500px] px-6 pb-10 pt-20">
                <WorkflowBuilder workflowId={workflowId} disabled={builderDisabled} onSaved={onBuilderSaved} onBusyChange={onBuilderBusyChange} />
            </div>
        </section>}

        {view === "model" && ready && <section aria-label="Model configuration" className="absolute inset-0 z-20 overflow-y-auto bg-background">
            <div className="mx-auto w-full max-w-3xl px-6 pb-16 pt-20">
                <h2 className="text-lg font-semibold">Model configuration</h2>
                <p className="mb-6 text-sm text-muted-foreground">
                    Choose from a preset <Link href="/model-configurations" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">model configuration</Link> or create a custom one from scratch. Changes save to the draft and apply to test calls at once; publish to apply them to live calls.
                </p>
                <WorkflowModelPicker model={model} density="compact" />
            </div>
        </section>}

        {view === "variables" && ready && <section aria-label="Template variables" className="absolute inset-0 z-20 overflow-y-auto bg-background">
            <div className="mx-auto w-full max-w-3xl px-6 pb-16 pt-20">
                {variables.missing.length > 0 && <p className="mb-4 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300" role="status">
                    Prompts reference {variables.missing.map(name => `{{${name}}}`).join(", ")} but no value is set. Set them here to use during test calls.
                </p>}
                <TemplateVariablesSection templateContextVariables={templateContextVariables} onSave={saveTemplateContextVariables} />
            </div>
        </section>}

        {view === "settings" && ready && <section aria-label="Settings" className="absolute inset-0 z-20 overflow-y-auto bg-background">
            <div className="mx-auto w-full max-w-5xl px-6 pb-16 pt-20">
                <WorkflowSettingsSections workflowId={workflowId} workflowName={workflowName} workflowUuid={workflowUuid} hide={["models", "variables"]} {...state} />
            </div>
        </section>}
    </>;
}
