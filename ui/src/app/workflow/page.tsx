import { Suspense } from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { CreateWorkflowButton } from "@/components/workflow/CreateWorkflowButton";
import { CreateFolderButton } from '@/components/workflow/folders/CreateFolderButton';
import { UploadWorkflowButton } from '@/components/workflow/UploadWorkflowButton';

import WorkflowLayout from "./WorkflowLayout";
import WorkflowList from "./WorkflowList";

export const dynamic = 'force-dynamic';

function PageContent() {
    return (
        <div className="container mx-auto px-4 py-8">
            {/* Your Workflows Section */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <h1 className="text-2xl font-bold">Your Agents</h1>
                    <div className="flex gap-2">
                        <UploadWorkflowButton />
                        <CreateFolderButton />
                        <CreateWorkflowButton />
                    </div>
                </div>
                <WorkflowList />
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
                        <Card key={i}>
                            <CardContent className="p-0">
                                <div className="h-40 bg-muted/70" />
                            </CardContent>
                        </Card>
                    ))}
                </div>
            </div>

            {/* Your Workflows Section Loading */}
            <div className="mb-6">
                <div className="flex justify-between items-center mb-6">
                    <div className="h-8 w-48 bg-muted rounded"></div>
                    <div className="h-10 w-32 bg-muted rounded"></div>
                </div>
                <Card>
                    <CardContent className="p-0">
                        <div className="h-96 bg-muted/70" />
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
