"use client";

import { Brain } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

import { useWorkflowModelOverride } from "./useWorkflowModelOverride";
import { WorkflowModelPicker } from "./WorkflowModelPicker";

/** The settings-page card: the full editor on the workflow's override. */
export function WorkflowModelConfiguration({ workflowConfigurations, workflowName, onSave }: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const model = useWorkflowModelOverride({ workflowName, workflowConfigurations, onSave });
    // Edits the autosave cannot finish on its own would be lost on leaving.
    useUnsavedChanges("models", model.dirty);
    return <Card id="models">
        <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Brain className="h-4 w-4" />Model Configuration</CardTitle>
            <CardDescription>Follow a preset configuration or build custom settings for this agent. Changes save to the draft and apply to test calls at once; publish to apply them to live calls.</CardDescription>
        </CardHeader>
        <CardContent>
            <WorkflowModelPicker model={model} density="full" />
        </CardContent>
    </Card>;
}
