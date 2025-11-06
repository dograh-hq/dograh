'use client';

import { useParams, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import RenderWorkflow from '@/app/workflow/[workflowId]/RenderWorkflow';
import { getWorkflowApiV1WorkflowFetchWorkflowIdGet } from '@/client/sdk.gen';
import type { WorkflowResponse } from '@/client/types.gen';
import { FlowEdge, FlowNode } from '@/components/flow/types';
import SpinLoader from '@/components/SpinLoader';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { DEFAULT_WORKFLOW_CONFIGURATIONS,WorkflowConfigurations } from '@/types/workflow-configurations';

import WorkflowLayout from '../WorkflowLayout';
import { WorkflowExecutions } from './components/WorkflowExecutions';
import { WorkflowTabs } from './components/WorkflowTabs';

export default function WorkflowDetailPage() {
    const params = useParams();
    const searchParams = useSearchParams();
    const [workflow, setWorkflow] = useState<WorkflowResponse | undefined>(undefined);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const { user, getAccessToken, redirectToLogin, loading: authLoading } = useAuth();

    // Get current tab from URL, default to 'editor'
    const currentTab = (searchParams.get('tab') as 'editor' | 'executions') || 'editor';

    // Redirect if not authenticated
    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        const fetchWorkflow = async () => {
            if (!user) return;
            try {
                const accessToken = await getAccessToken();
                const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: {
                        workflow_id: Number(params.workflowId)
                    },
                    headers: {
                        'Authorization': `Bearer ${accessToken}`,
                    },
                });
                const workflow = response.data;
                setWorkflow(workflow);
            } catch (err) {
                setError('Failed to fetch workflow');
                logger.error(`Error fetching workflow: ${err}`);
            } finally {
                setLoading(false);
            }
        };

        if (user) {
            fetchWorkflow();
        }
    }, [params.workflowId, user, getAccessToken]);

    const stickyTabs = workflow ? <WorkflowTabs workflowId={workflow.id} currentTab={currentTab} /> : null;

    // Memoize user and getAccessToken to prevent unnecessary re-renders
    const stableUser = useMemo(() => user, [user?.id]);
    const stableGetAccessToken = useMemo(() => getAccessToken, [getAccessToken]);

    if (loading) {
        return (
            <WorkflowLayout stickyTabs={stickyTabs}>
                <SpinLoader />
            </WorkflowLayout>
        );
    }
    else if (error || !workflow) {
        return (
            <WorkflowLayout showFeaturesNav={false} stickyTabs={stickyTabs}>
                <div className="flex items-center justify-center min-h-screen">
                    <div className="text-lg text-red-500">{error || 'Workflow not found'}</div>
                </div>
            </WorkflowLayout>
        );
    }
    else {
        // Render both views but hide the inactive one using absolute positioning
        // This preserves state when switching tabs
        return (
            <>
                {/* Editor view */}
                <div className={currentTab === 'editor' ? 'block' : 'hidden'} aria-hidden={currentTab !== 'editor'}>
                    {stableUser && (
                        <RenderWorkflow
                            initialWorkflowName={workflow.name}
                            workflowId={workflow.id}
                            initialFlow={{
                                nodes: workflow.workflow_definition.nodes as FlowNode[],
                                edges: workflow.workflow_definition.edges as FlowEdge[],
                                viewport: { x: 0, y: 0, zoom: 0 }
                            }}
                            initialTemplateContextVariables={workflow.template_context_variables as Record<string, string> || {}}
                            initialWorkflowConfigurations={(workflow.workflow_configurations as WorkflowConfigurations) || DEFAULT_WORKFLOW_CONFIGURATIONS}
                            user={stableUser}
                            getAccessToken={stableGetAccessToken}
                        />
                    )}
                </div>

                {/* Executions view */}
                <div className={currentTab === 'executions' ? 'block' : 'hidden'} aria-hidden={currentTab !== 'executions'}>
                    <WorkflowLayout stickyTabs={stickyTabs} showFeaturesNav={false}>
                        <WorkflowExecutions workflowId={workflow.id} searchParams={searchParams} />
                    </WorkflowLayout>
                </div>
            </>
        );
    }
}
