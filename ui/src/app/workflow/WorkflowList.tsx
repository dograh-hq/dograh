"use client";

import { useCallback, useEffect, useState } from "react";

import { getWorkflowsApiV1WorkflowFetchGet, listFoldersApiV1FolderGet } from "@/client/sdk.gen";
import type { FolderResponse, WorkflowListResponse } from "@/client/types.gen";
import { Card, CardContent } from "@/components/ui/card";
import { AgentFolderView } from "@/components/workflow/folders/AgentFolderView";
import { FolderSection } from "@/components/workflow/folders/FolderSection";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import logger from "@/lib/logger";

function WorkflowListLoading() {
    return (
        <Card>
            <CardContent className="p-0">
                <div className="h-96 animate-pulse bg-muted/70" />
            </CardContent>
        </Card>
    );
}

export default function WorkflowList() {
    const { isAuthenticated, loading: authLoading, redirectToLogin } = useAuth();
    const [activeWorkflows, setActiveWorkflows] = useState<WorkflowListResponse[]>([]);
    const [archivedWorkflows, setArchivedWorkflows] = useState<WorkflowListResponse[]>([]);
    const [folders, setFolders] = useState<FolderResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadWorkflows = useCallback(async () => {
        if (!isAuthenticated) return;

        setLoading(true);
        setError(null);
        try {
            // Do not pass an Authorization header here. OrgConfigProvider has
            // installed the shared SDK interceptor, which obtains the current
            // token for each request and is also used by the other app pages.
            const response = await getWorkflowsApiV1WorkflowFetchGet({
                query: { status: "active,archived" },
            });

            if (response.response?.status === 401) {
                redirectToLogin();
                return;
            }
            if (response.error || !response.data) {
                throw new Error(detailFromError(response.error, "Failed to load agents"));
            }

            const allWorkflows = Array.isArray(response.data) ? response.data : [response.data];
            setActiveWorkflows(
                allWorkflows
                    .filter((workflow) => workflow.status === "active")
                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
            );
            setArchivedWorkflows(
                allWorkflows
                    .filter((workflow) => workflow.status === "archived")
                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
            );

            const foldersResponse = await listFoldersApiV1FolderGet();
            if (foldersResponse.response?.status === 401) {
                redirectToLogin();
                return;
            }
            setFolders(foldersResponse.data ?? []);
        } catch (loadError) {
            logger.error(`Error fetching workflows: ${loadError}`);
            setError(loadError instanceof Error ? loadError.message : "Failed to load Workflows. Please Try Again Later.");
        } finally {
            setLoading(false);
        }
    }, [isAuthenticated, redirectToLogin]);

    useEffect(() => {
        if (authLoading) return;
        if (!isAuthenticated) {
            redirectToLogin();
            return;
        }
        void loadWorkflows();
    }, [authLoading, isAuthenticated, loadWorkflows, redirectToLogin]);

    if (authLoading || loading) return <WorkflowListLoading />;
    if (error) return <div className="text-red-500">{error}</div>;

    return (
        <>
            <div className="mb-8">
                <h2 className="mb-4 text-xl font-semibold">Active Agents</h2>
                {activeWorkflows.length > 0 || folders.length > 0 ? (
                    <AgentFolderView workflows={activeWorkflows} folders={folders} />
                ) : (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            No active workflows found. Create your first workflow to get started.
                        </CardContent>
                    </Card>
                )}
            </div>

            {archivedWorkflows.length > 0 && (
                <div className="mb-8">
                    <FolderSection kind="archived" workflows={archivedWorkflows} />
                </div>
            )}
        </>
    );
}
