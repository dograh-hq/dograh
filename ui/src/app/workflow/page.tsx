import { Settings } from 'lucide-react';
import Link from 'next/link';
import { Suspense } from 'react';

import { getWorkflowsApiV1WorkflowFetchGet, getWorkflowTemplatesApiV1WorkflowTemplatesGet } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { CreateWorkflowButton } from "@/components/workflow/CreateWorkflowButton";
import { DuplicateWorkflowTemplate } from "@/components/workflow/TemplateCard";
import { UploadWorkflowButton } from '@/components/workflow/UploadWorkflowButton';
import { WorkflowTable } from "@/components/workflow/WorkflowTable";
import { getServerAccessToken, getServerAuthProvider } from '@/lib/auth/server';
import logger from '@/lib/logger';

import WorkflowLayout from "./WorkflowLayout";

export const dynamic = 'force-dynamic';

// Server component for workflow templates
async function WorkflowTemplatesList() {
    try {
        console.log('[WorkflowTemplatesList] Starting template fetch...');
        logger.info('Attempting to fetch workflow templates...');
        const response = await getWorkflowTemplatesApiV1WorkflowTemplatesGet();
        
        console.log(`[WorkflowTemplatesList] Template response received`);
        console.log(`[WorkflowTemplatesList] Template response status: ${response.response?.status}`);
        // Log request URL if available
        if (response.request?.url) {
            console.log(`[WorkflowTemplatesList] Template Request URL: ${response.request.url}`);
            logger.info(`Template Request URL: ${response.request.url}`);
        }
        logger.info(`Template Response status: ${response.response?.status}`);
        const templates = response.data || [];
        console.log(`[WorkflowTemplatesList] Templates found: ${templates.length}`);
        console.log(`[WorkflowTemplatesList] Template data:`, JSON.stringify(templates, null, 2));
        logger.info(`Found ${templates.length} templates`);

        // Get access token on server side to pass to client component
        const accessToken = await getServerAccessToken();

        return (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {templates.map((template) => (
                    <DuplicateWorkflowTemplate
                        key={template.id}
                        id={template.id}
                        title={template.template_name}
                        description={template.template_description}
                        serverAccessToken={accessToken}
                    />
                ))}
            </div>
        );
    } catch (err) {
        logger.error(`Error fetching workflow templates: ${err}`);
        return (
            <div className="text-red-500">
                Failed to load Workflow Templates. Please Try Again Later.
            </div>
        );
    }
}

// Server component for workflow list
async function WorkflowList() {
    console.log('[WorkflowList] Component started');
    const authProvider = getServerAuthProvider();
    const accessToken = await getServerAccessToken();

    console.log(`[WorkflowList] authProvider: ${authProvider}`);
    console.log(`[WorkflowList] accessToken length: ${accessToken?.length || 0}`);
    console.log(`[WorkflowList] accessToken starts with: ${accessToken?.substring(0, 10)}...`);
    logger.debug(`In WorkflowList, authProvider: ${authProvider}, accessToken: ${accessToken}`);

    if (!accessToken) {
        console.log('[WorkflowList] No access token - redirecting or showing error');
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
        console.log('[WorkflowList] Starting API call...');
        logger.info('Attempting to fetch workflows...');
        
        const requestConfig = {
            headers: {
                'Authorization': `Bearer ${accessToken}`,
            },
            query: {
                status: 'active,archived'
            }
        };
        console.log(`[WorkflowList] Request config:`, JSON.stringify(requestConfig, null, 2));
        
        const response = await getWorkflowsApiV1WorkflowFetchGet(requestConfig);

        console.log(`[WorkflowList] Response received`);
        console.log(`[WorkflowList] Response status: ${response.response?.status}`);
        console.log(`[WorkflowList] Response headers:`, response.response?.headers);
        logger.info(`Workflow Response status: ${response.response?.status}`);
        if (response.request?.url) {
            console.log(`[WorkflowList] Request URL: ${response.request.url}`);
            logger.info(`Workflow Request URL: ${response.request.url}`);
        }

        console.log(`[WorkflowList] Raw response data:`, JSON.stringify(response.data, null, 2));
        const allWorkflowData = response.data ? (Array.isArray(response.data) ? response.data : [response.data]) : [];
        console.log(`[WorkflowList] Processed workflow data length: ${allWorkflowData.length}`);
        logger.info(`Found ${allWorkflowData.length} total workflows`);

        // Separate active and archived workflows
        const activeWorkflows = allWorkflowData
            .filter(w => w.status === 'active')
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

        const archivedWorkflows = allWorkflowData
            .filter(w => w.status === 'archived')
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
            
        console.log(`[WorkflowList] Active workflows: ${activeWorkflows.length}`);
        console.log(`[WorkflowList] Archived workflows: ${archivedWorkflows.length}`);
        console.log(`[WorkflowList] Active workflow IDs:`, activeWorkflows.map(w => w.id));

        return (
            <>
                {/* Active Workflows Section */}
                <div className="mb-8">
                    <h2 className="text-xl font-semibold mb-4">Active Workflows</h2>
                    {activeWorkflows.length > 0 ? (
                        <WorkflowTable workflows={activeWorkflows} showArchived={false} />
                    ) : (
                        <div className="text-gray-500 bg-gray-50 rounded-lg p-8 text-center">
                            No active workflows found. Create your first workflow to get started.
                        </div>
                    )}
                </div>

                {/* Archived Workflows Section */}
                {archivedWorkflows.length > 0 && (
                    <div className="mb-8">
                        <h2 className="text-xl font-semibold mb-4 text-gray-600">Archived Workflows</h2>
                        <WorkflowTable workflows={archivedWorkflows} showArchived={true} />
                    </div>
                )}
            </>
        );
    } catch (err) {
        console.error('[WorkflowList] Error fetching workflows:', err);
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
            {/* Get Started Section */}
            <div className="mb-12">
                <div className="flex justify-between items-center px-4">
                    <h2 className="text-2xl font-bold mb-6">Get Started</h2>
                    <div className="flex gap-2">
                        <Link href="/service-configurations">
                            <Button className="flex items-center gap-2 mb-6">
                                <Settings size={16} />
                                Configure Services
                            </Button>
                        </Link>
                        <Link href="/integrations">
                            <Button className="flex items-center gap-2 mb-6">
                                <Settings size={16} />
                                Integrations
                            </Button>
                        </Link>
                    </div>
                </div>

                <Suspense fallback={
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {Array.from({ length: 3 }, (_, i) => (
                            <div key={i} className="bg-gray-200 rounded-lg h-40"></div>
                        ))}
                    </div>
                }>
                    <WorkflowTemplatesList />
                </Suspense>
            </div>

            {/* Your Workflows Section */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-2xl font-bold">Your Workflows</h1>
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
                <div className="h-8 w-48 bg-gray-200 rounded mb-6"></div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {Array.from({ length: 3 }, (_, i) => (
                        <div key={i} className="bg-gray-200 rounded-lg h-40"></div>
                    ))}
                </div>
            </div>

            {/* Your Workflows Section Loading */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <div className="h-8 w-48 bg-gray-200 rounded"></div>
                    <div className="h-10 w-32 bg-gray-200 rounded"></div>
                </div>
                <div className="bg-gray-200 rounded-lg h-96"></div>
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
