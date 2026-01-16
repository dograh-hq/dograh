"use client";

import { Badge } from "@/components/ui/badge";
import { listDocumentsApiV1KnowledgeBaseDocumentsGet } from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";
import { useCallback, useEffect, useState } from "react";

interface DocumentBadgesProps {
    documentUuids: string[];
}

export const DocumentBadges = ({ documentUuids }: DocumentBadgesProps) => {
    const { getAccessToken } = useAuth();
    const [documentNames, setDocumentNames] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);

    const fetchDocuments = useCallback(async () => {
        if (documentUuids.length === 0) return;

        setLoading(true);
        try {
            const accessToken = await getAccessToken();
            const response = await listDocumentsApiV1KnowledgeBaseDocumentsGet({
                headers: { Authorization: `Bearer ${accessToken}` },
                query: {
                    limit: 100,
                },
            });

            if (response.data) {
                const nameMap: Record<string, string> = {};
                response.data.documents
                    .filter((doc) => documentUuids.includes(doc.document_uuid))
                    .forEach((doc) => {
                        nameMap[doc.document_uuid] = doc.filename;
                    });
                setDocumentNames(nameMap);
            }
        } catch (error) {
            console.error("Failed to fetch documents:", error);
        } finally {
            setLoading(false);
        }
    }, [documentUuids, getAccessToken]);

    useEffect(() => {
        fetchDocuments();
    }, [fetchDocuments]);

    if (documentUuids.length === 0) {
        return <></>;
    }

    if (loading) {
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
