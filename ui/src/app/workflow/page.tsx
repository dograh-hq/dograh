import { Suspense } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet } from '@/client/sdk.gen';
import { CreateWorkflowButton } from "@/components/workflow/CreateWorkflowButton";
import { UploadWorkflowButton } from '@/components/workflow/UploadWorkflowButton';
import { WorkflowTable } from "@/components/workflow/WorkflowTable";
import { getServerAccessToken, getServerAuthProvider } from '@/lib/auth/server';
import logger from '@/lib/logger';

import WorkflowLayout from "./WorkflowLayout";

export const dynamic = 'force-dynamic';

// Server component for workflow list
async function WorkflowList() {
    const authProvider = getServerAuthProvider();
    const accessToken = await getServerAccessToken();

    logger.debug(`In WorkflowList, authProvider: ${authProvider}, accessToken: ${accessToken}`);

    if (!accessToken) {
        // If no token, user needs to sign in
        const { redirect } = await import('next/navigation');
        if (authProvider === 'stack') {
            redirect('/');
        } else {
            // For OSS mode, this shouldn't happen as token is auto-generated
            return (
                <div className="text-red-500">
                    Authentication required. Please refresh the page.
                </div>
            );
        }
    }

    try {
        // Fetch both active and archived workflows in a single request
        const response = await getWorkflowsApiV1WorkflowFetchGet({
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
            query: {
                status: 'active,archived'
            }
        });

        const allWorkflowData = response.data ? (Array.isArray(response.data) ? response.data : [response.data]) : [];

        // Separate active and archived workflows
        const activeWorkflows = allWorkflowData
            .filter(w => w.status === 'active')
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        const archivedWorkflows = allWorkflowData
            .filter(w => w.status === 'archived')
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        return (
            <>
                {/* Active Workflows Section */}
                <div className="mb-8">
                    <h2 className="text-xl font-semibold mb-4">Active Agents</h2>
                    {activeWorkflows.length > 0 ? (
                        <WorkflowTable workflows={activeWorkflows} showArchived={false} />
                    ) : (
                        <div className="text-muted-foreground bg-muted rounded-lg p-8 text-center">
                            No active workflows found. Create your first workflow to get started.
                        </div>
                    )}
                </div>

                {/* Archived Workflows Section */}
                {archivedWorkflows.length > 0 && (
                    <div className="mb-8">
                        <h2 className="text-xl font-semibold mb-4 text-muted-foreground">Archived Workflows</h2>
                        <WorkflowTable workflows={archivedWorkflows} showArchived={true} />
                    </div>
                )}
            </>
        );
    } catch (err) {
        logger.error(`Error fetching workflows: ${err}`);
        return (
            <div className="text-red-500">
                Failed to load Workflows. Please Try Again Later.
            </div>
        );
    }
}

async function PageContent() {

    const workflowList = await WorkflowList();

    return (
        <div className="container mx-auto px-4 py-8">
            {/* Your Workflows Section */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-2xl font-bold">Your Agents</h1>
                    <div className="flex gap-2">
                        <UploadWorkflowButton />
                        <CreateWorkflowButton />
                    </div>
                </div>
                {workflowList}
            </div>
        </div>
    );
}

function WorkflowsLoading() {
    return (
        <div className="container mx-auto px-4 py-8">
            {/* Get Started Section Loading */}
            <div className="mb-12">
                <div className="h-8 w-48 bg-muted rounded mb-6"></div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {Array.from({ length: 3 }, (_, i) => (
                        <div key={i} className="bg-muted rounded-lg h-40"></div>
                    ))}
                </div>
            </div>

            {/* Your Workflows Section Loading */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <div className="h-8 w-48 bg-muted rounded"></div>
                    <div className="h-10 w-32 bg-muted rounded"></div>
                </div>
                <div className="bg-muted rounded-lg h-96"></div>
            </div>
        </div>
    );
}

export default function WorkflowPage() {
    return (
        <WorkflowLayout showFeaturesNav={true}>
            <Suspense fallback={<WorkflowsLoading />}>
                <PageContent />
            </Suspense>
        </WorkflowLayout>

    );
}
