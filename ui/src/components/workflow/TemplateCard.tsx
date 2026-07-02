'use client';

import { Copy } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { duplicateWorkflowTemplateApiV1WorkflowTemplatesDuplicatePost } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

interface DuplicateWorkflowTemplateProps {
    id: number;
    title: string;
    description: string;
    serverAccessToken?: string | null;
}

export function DuplicateWorkflowTemplate({ id, title, description, serverAccessToken }: DuplicateWorkflowTemplateProps) {
    const t = useTranslations("workflowList");
    const [isLoading, setIsLoading] = useState(false);
    const router = useRouter();
    const { user, getAccessToken } = useAuth();

    const handleDuplicate = async () => {
        setIsLoading(true);
        try {
            // Use server-provided token if available, otherwise try to get from client auth
            let accessToken = serverAccessToken;

            if (!accessToken) {
                if (!user) {
                    logger.error('User not authenticated and no server token provided');
                    return;
                }
                accessToken = await getAccessToken();
            }

            if (!accessToken) {
                logger.error('No access token available');
                return;
            }

            const response = await duplicateWorkflowTemplateApiV1WorkflowTemplatesDuplicatePost({
                body: {
                    template_id: id,
                    workflow_name: title,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                logger.info('Workflow created successfully from template');
                // Redirect to the new workflow
                router.push(`/workflow/${response.data.id}`);
            }
        } catch (error) {
            logger.error(`Error creating workflow from template: ${error}`);
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="bg-white border rounded-lg overflow-hidden shadow-sm hover:shadow-md transition-shadow p-4">
            <div>
                <h3 className="text-lg font-semibold mb-2">{title}</h3>
                <p className="text-gray-600 mb-4">{description}</p>
                <Button
                    variant="outline"
                    className="w-full"
                    onClick={handleDuplicate}
                    disabled={isLoading}
                >
                    <Copy className="w-4 h-4 mr-2" />
                    {isLoading ? t("creatingButton") : t("duplicateWorkflowTemplate")}
                </Button>
            </div>
        </div>
    );
}
