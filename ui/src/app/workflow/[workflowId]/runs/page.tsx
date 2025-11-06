"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

export default function WorkflowRunsPage() {
    const { workflowId } = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();

    // Redirect to main workflow page with executions tab
    useEffect(() => {
        const params = new URLSearchParams(searchParams.toString());
        params.set('tab', 'executions');
        router.replace(`/workflow/${workflowId}?${params.toString()}`);
    }, [workflowId, router, searchParams]);

    return null;
}
