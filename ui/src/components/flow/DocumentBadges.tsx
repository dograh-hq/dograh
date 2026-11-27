"use client";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { Badge } from "@/components/ui/badge";

interface DocumentBadgesProps {
    documentUuids: string[];
}

export const DocumentBadges = ({ documentUuids }: DocumentBadgesProps) => {
    const { documents } = useWorkflow();
    // A cached or paginated catalog is not evidence that a document was deleted.
    const documentNames = new Map(documents?.map(doc => [doc.document_uuid, doc.filename]));

    if (documentUuids.length === 0) {
        return <></>;
    }

    // Show loading while data hasn't loaded yet
    if (documents === undefined) {
        return <Badge variant="outline">Loading...</Badge>;
    }

    return (
        <>
            {documentUuids.map((uuid) => (
                <Badge key={uuid} variant="outline" title={uuid}>
                    {documentNames.get(uuid) || 'Document unavailable'}
                </Badge>
            ))}
        </>
    );
};
