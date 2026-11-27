import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { createWorkflowApiV1WorkflowCreateDefinitionPost } from '@/client/sdk.gen';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from '@/lib/utils';

const BLANK_WORKFLOW_DEFINITION = {
    nodes: [
        {
            id: "1",
            type: "startCall",
            position: { x: 175, y: 60 },
            data: {
                greeting: "Hey There! How can I help you today?",
                prompt: "# Goal\nYou are a helpful agent who is handing a conversation over voice with a human. This is a voice conversation, so transcripts can be error prone.\n\n## Rules\n- Language: UK English but does not have to be correct english\n- Keep responses short and 2-3 sentences max\n- If you have to repeat something that you said in your previous two turns, then rephrase a bit while keeping the same meaning. Never repeat the exact same words as in your previous 2 responses.\n\n## Speech Handling\n- There could be multiple transcription errors. \n- Accept variations: yes/yeah/yep/aye, no/nah/nope\n- If user says \"sorry?\" or \"pardon me\" or \"can you repeat\"  or \"what?\", they might not have heard you- so just repeat what you just said.\n\n### Flow\nStart by saying \"Hi\". Be polite and courteous. ",
                name: "start call",
                allow_interrupt: true,
                invalid: false,
                validationMessage: null,
                add_global_prompt: false,
                delayed_start: false,
                is_start: true,
                selected_through_edge: false,
                hovered_through_edge: false,
                extraction_enabled: false,
                selected: false,
                dragging: false,
            },
        },
    ],
    edges: [],
    viewport: { x: 808, y: 269, zoom: 0.75 },
};

/** Creates an agent from a one-node starter canvas and opens it in the editor. */
export function useCreateBlankWorkflow() {
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [isCreating, setIsCreating] = useState(false);

    const createBlankWorkflow = async () => {
        if (isCreating || !user) return;
        setIsCreating(true);

        try {
            const accessToken = await getAccessToken();
            const name = `Workflow-${getRandomId()}`;
            const response = await createWorkflowApiV1WorkflowCreateDefinitionPost({
                body: {
                    name,
                    workflow_definition: BLANK_WORKFLOW_DEFINITION as unknown as { [key: string]: unknown },
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data?.id) {
                router.push(`/workflow/${response.data.id}`);
            }
        } catch (err) {
            logger.error(`Error creating blank workflow: ${err}`);
            toast.error('Failed to create workflow');
        } finally {
            setIsCreating(false);
        }
    };

    return { createBlankWorkflow, isCreating };
}
