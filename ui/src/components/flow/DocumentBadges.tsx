"use client";

import { useCallback, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { DocumentResponseSchema } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";

interface DocumentBadgesProps {
    documentUuids: string[];
    onStaleUuidsDetected?: (staleUuids: string[]) => void;
}

export const DocumentBadges = ({ documentUuids, onStaleUuidsDetected }: DocumentBadgesProps) => {
    const { documents } = useWorkflow();
    const [documentNames, setDocumentNames] = useState<Record<string, string>>({});

    const processDocuments = useCallback((docs: DocumentResponseSchema[]) => {
        const nameMap: Record<string, string> = {};
        const validUuids = new Set<string>();

        docs
            .filter((doc) => documentUuids.includes(doc.document_uuid))
            .forEach((doc) => {
                nameMap[doc.document_uuid] = doc.filename;
                validUuids.add(doc.document_uuid);
            });
        setDocumentNames(nameMap);

        // Detect stale UUIDs - this only runs when we have loaded data (not undefined)
        if (onStaleUuidsDetected) {
            const staleUuids = documentUuids.filter(uuid => !validUuids.has(uuid));
            if (staleUuids.length > 0) {
                onStaleUuidsDetected(staleUuids);
            }
        }
    }, [documentUuids, onStaleUuidsDetected]);

    useEffect(() => {
        if (documentUuids.length > 0 && documents !== undefined) {
            processDocuments(documents);
        } else if (documentUuids.length === 0) {
            setDocumentNames({});
        }
    }, [documentUuids, documents, processDocuments]);

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
                <Badge key={uuid} variant="outline">
                    {documentNames[uuid] || uuid}
                </Badge>
            ))}
        </>
    );
};
