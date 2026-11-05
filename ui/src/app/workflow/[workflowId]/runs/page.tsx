"use client";

import { useParams, useSearchParams } from "next/navigation";

import { WorkflowExecutions } from "../components/WorkflowExecutions";

export default function WorkflowRunsPage() {
    const { workflowId } = useParams();
    const searchParams = useSearchParams();

    return (
        <WorkflowExecutions
            workflowId={Number(workflowId)}
            searchParams={searchParams}
        />
    );
}
