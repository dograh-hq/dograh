'use client';

import { Bot, ChevronDown, LayoutTemplate, PlusIcon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { createWorkflowApiV1WorkflowCreateDefinitionPost } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
                prompt: "# Goal\nYou are a helpful agent who is handing a conversation over voice with a human. This is a voice conversation, so transcripts can be error prone.\n\n## Rules\n- Language: UK English but does not have to be correct english\n- Keep responses short and 2-3 sentences max\n- If you have to repeat something that you said in your previous two turns, then rephrase a bit while keeping the same meaning. Never repeat the exact same words as in your previous 2 responses.\n\n## Speech Handling\n- There could be multiple transcription errors. \n- Accept variations: yes/yeah/yep/aye, no/nah/nope\n- If user says \"sorry?\" or \"pardon me\" or \"can you repeat\"  or \"what?\", they might not have heard you- so just repeat what you just said.\n\n### Flow\nStart by saying \"Hi\". Be polite and courteous. ",
                name: "start call",
                allow_interrupt: false,
                invalid: false,
                validationMessage: null,
                is_static: false,
                add_global_prompt: false,
                wait_for_user_response: false,
                detect_voicemail: true,
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

export function CreateWorkflowButton() {
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [isCreating, setIsCreating] = useState(false);

    const handleAgentBuilder = () => {
        router.push('/workflow/create');
    };

    const handleBlankCanvas = async () => {
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

    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button disabled={isCreating}>
                    <PlusIcon className="w-4 h-4" />
                    {isCreating ? 'Creating...' : 'Create Agent'}
                    <ChevronDown className="w-4 h-4" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleAgentBuilder} className="cursor-pointer">
                    <Bot className="w-4 h-4 mr-2" />
                    <div>
                        <div className="font-medium">Use Agent Builder</div>
                        <div className="text-xs text-muted-foreground">AI generates a workflow from your description</div>
                    </div>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleBlankCanvas} disabled={isCreating} className="cursor-pointer">
                    <LayoutTemplate className="w-4 h-4 mr-2" />
                    <div>
                        <div className="font-medium">Blank Canvas</div>
                        <div className="text-xs text-muted-foreground">Start from scratch with an empty workflow</div>
                    </div>
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
