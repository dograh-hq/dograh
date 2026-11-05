"use client";

import { ArrowLeft } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from "@/client/sdk.gen";
import type { WorkflowResponse } from "@/client/types.gen";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { PageShell } from "@/components/layout/PageShell";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { UnsavedChangesProvider, useUnsavedChangesContext } from "@/context/UnsavedChangesContext";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";
import { type WorkflowConfigurations } from "@/types/workflow-configurations";

import { useWorkflowState } from "../hooks/useWorkflowState";
import { WorkflowSettingsSections } from "./WorkflowSettingsSections";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function WorkflowSettingsPage() {
    const params = useParams();
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const [workflow, setWorkflow] = useState<WorkflowResponse | undefined>(undefined);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        const fetchWorkflow = async () => {
            if (!user) return;
            try {
                const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: { workflow_id: Number(params.workflowId) },
                });
                setWorkflow(response.data);
            } catch (err) {
                setError("Failed to fetch workflow");
                logger.error(`Error fetching workflow settings: ${err}`);
            } finally {
                setLoading(false);
            }
        };
        if (user) fetchWorkflow();
    }, [params.workflowId, user]);

    if (loading || authLoading) return <SpinLoader />;

    if (error || !workflow) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="text-lg text-destructive">{error || "Workflow not found"}</div>
            </div>
        );
    }

    if (!user) return null;

    return <WorkflowSettingsContent workflow={workflow} user={user} />;
}

// ---------------------------------------------------------------------------
// Content — only mounts once the workflow API response is available, so
// useWorkflowState always initialises with real data.
// ---------------------------------------------------------------------------

function WorkflowSettingsContent({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    return (
        <UnsavedChangesProvider>
            <WorkflowSettingsInner workflow={workflow} user={user} />
        </UnsavedChangesProvider>
    );
}

function WorkflowSettingsInner({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    const router = useRouter();
    const { confirmNavigate } = useUnsavedChangesContext();
    const workflowId = workflow.id;

    const initialFlow = useMemo(
        () => ({
            nodes: workflow.workflow_definition.nodes as FlowNode[],
            edges: workflow.workflow_definition.edges as FlowEdge[],
            viewport: { x: 0, y: 0, zoom: 0 },
        }),
        [workflow],
    );

    const initialTemplateContextVariables = useMemo(
        () => (workflow.template_context_variables as Record<string, string>) || {},
        [workflow],
    );

    const initialWorkflowConfigurations = useMemo(
        () => (
            workflow.workflow_configurations
                ? (workflow.workflow_configurations as WorkflowConfigurations)
                : undefined
        ),
        [workflow],
    );

    const state = useWorkflowState({
        initialWorkflowName: workflow.name,
        workflowId,
        initialFlow,
        initialTemplateContextVariables,
        initialWorkflowConfigurations,
        user,
    });
    const workflowName = state.workflowName || workflow.name;

    return (
        <>
            {/* Sticky header */}
            <header className="sticky top-0 z-10 flex items-center gap-3 border-b bg-background/95 px-6 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/60">
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => confirmNavigate(() => router.push(`/workflow/${workflowId}`))}
                >
                    <ArrowLeft className="h-4 w-4" />
                </Button>
                <div>
                    <p className="text-xs text-muted-foreground">Workflow Settings</p>
                    <h1 className="text-sm font-semibold">{workflowName}</h1>
                </div>
            </header>

            <PageShell className="max-w-5xl px-6">
                <WorkflowSettingsSections
                    workflowId={workflowId}
                    workflowName={workflowName}
                    workflowUuid={workflow.workflow_uuid}
                    workflowConfigurations={state.workflowConfigurations}
                    defaultCallDispositions={state.defaultCallDispositions}
                    defaultAnswerClassifierPrompt={state.defaultAnswerClassifierPrompt}
                    textChatInactivityTimeoutConstraints={state.textChatInactivityTimeoutConstraints}
                    widgetTextDefaults={state.widgetTextDefaults}
                    templateContextVariables={state.templateContextVariables}
                    dictionary={state.dictionary}
                    saveWorkflowConfigurations={state.saveWorkflowConfigurations}
                    saveTemplateContextVariables={state.saveTemplateContextVariables}
                    saveDictionary={state.saveDictionary}
                />
            </PageShell>
        </>
    );
}
