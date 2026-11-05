"use client";

import { PageShell } from '@/components/layout/PageShell';
import { WorkflowBuilder } from '@/components/workflow-builder/WorkflowBuilder';

export default function CreateWorkflowPage() {
    return <PageShell className="flex min-h-[calc(100vh-80px)] max-w-[1500px] flex-col py-6 md:px-8">
        <WorkflowBuilder />
    </PageShell>;
}
