import { Suspense } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet, listFoldersApiV1FolderGet } from '@/client/sdk.gen';
import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';
import { Card, CardContent } from '@/components/ui/card';
import { CreateWorkflowButton } from "@/components/workflow/CreateWorkflowButton";
import { AgentFolderView } from '@/components/workflow/folders/AgentFolderView';
import { CreateFolderButton } from '@/components/workflow/folders/CreateFolderButton';
import { FolderSection } from '@/components/workflow/folders/FolderSection';
import { UploadWorkflowButton } from '@/components/workflow/UploadWorkflowButton';
import { getServerAccessToken, getServerAuthProvider } from '@/lib/auth/server';
import logger from '@/lib/logger';

import WorkflowLayout from "./WorkflowLayout";

export const dynamic = 'force-dynamic';

// Server component for workflow list
async function WorkflowList() {
    const authProvider = await getServerAuthProvider();
    const accessToken = await getServerAccessToken();

    if (!accessToken) {
        // If no token, user needs to sign in
        const { redirect } = await import('next/navigation');
        if (authProvider === 'stack') {
            redirect('/');
        } else {
            // For OSS mode, this shouldn't happen as token is auto-generated
            return (
                <Card className="rounded-2xl border border-destructive/30 bg-card shadow-[var(--shadow-card)]">
                    <CardContent className="p-8 text-center text-body text-destructive">
                        Authentication required. Please refresh the page.
                    </CardContent>
                </Card>
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
            .filter((w: WorkflowListResponse) => w.status === 'active')
            .sort((a: WorkflowListResponse, b: WorkflowListResponse) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        const archivedWorkflows = allWorkflowData
            .filter((w: WorkflowListResponse) => w.status === 'archived')
            .sort((a: WorkflowListResponse, b: WorkflowListResponse) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        // Fetch folders for grouping active agents. A failure here shouldn't
        // break the page — fall back to an empty list (flat, ungrouped view).
        let folders: FolderResponse[] = [];
        try {
            const foldersResponse = await listFoldersApiV1FolderGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });
            folders = foldersResponse.data ?? [];
        } catch (folderErr) {
            logger.error(`Error fetching folders: ${folderErr}`);
        }

        return (
            <>
                {/* Active Workflows Section */}
                <div className="mb-10">
                    <h2 className="text-h3 mb-4 text-foreground">Active Agents</h2>
                    {activeWorkflows.length > 0 || folders.length > 0 ? (
                        <AgentFolderView workflows={activeWorkflows} folders={folders} />
                    ) : (
                        <Card className="rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
                            <CardContent className="flex flex-col items-center justify-center gap-1 p-12 text-center">
                                <p className="text-body font-medium text-foreground">No active agents yet</p>
                                <p className="text-small text-muted-foreground">
                                    Create your first agent to get started.
                                </p>
                            </CardContent>
                        </Card>
                    )}
                </div>

                {/* Archived Section — collapsible, same design as the folder/Uncategorized sections */}
                {archivedWorkflows.length > 0 && (
                    <div className="mb-10">
                        <FolderSection kind="archived" workflows={archivedWorkflows} />
                    </div>
                )}
            </>
        );
    } catch (err) {
        logger.error(`Error fetching workflows: ${err}`);
        return (
            <Card className="rounded-2xl border border-destructive/30 bg-card shadow-[var(--shadow-card)]">
                <CardContent className="p-8 text-center text-body text-destructive">
                    Failed to load agents. Please try again later.
                </CardContent>
            </Card>
        );
    }
}

async function PageContent() {

    const workflowList = await WorkflowList();

    return (
        <div className="container mx-auto px-4 py-10">
            {/* Your Workflows Section */}
            <div className="mb-6">
                <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
                    <div className="space-y-1">
                        <p className="text-eyebrow text-muted-foreground">Voice Agents</p>
                        <h1 className="text-h1 text-foreground">Your Agents</h1>
                        <p className="text-body text-muted-foreground">
                            Build, organize, and deploy your conversational AI agents.
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <UploadWorkflowButton />
                        <CreateFolderButton />
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
        <div className="container mx-auto animate-pulse px-4 py-10">
            {/* Header Loading */}
            <div className="mb-8 flex items-end justify-between gap-4">
                <div className="space-y-2">
                    <div className="h-3 w-24 rounded bg-muted" />
                    <div className="h-8 w-48 rounded-lg bg-muted" />
                    <div className="h-4 w-72 rounded bg-muted/70" />
                </div>
                <div className="flex gap-2">
                    <div className="h-10 w-28 rounded-md bg-muted" />
                    <div className="h-10 w-28 rounded-md bg-muted" />
                    <div className="h-10 w-32 rounded-md bg-muted" />
                </div>
            </div>

            {/* Active Agents Loading */}
            <div className="mb-10">
                <div className="mb-4 h-5 w-40 rounded bg-muted" />
                <Card className="rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)]">
                    <CardContent className="p-0">
                        <div className="h-96 rounded-2xl bg-muted/60" />
                    </CardContent>
                </Card>
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
