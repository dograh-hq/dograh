'use client';

import { Bot, ChevronDown, LayoutTemplate, PlusIcon } from 'lucide-react';
import { useRouter } from 'next/navigation';

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { useCreateBlankWorkflow } from './useCreateBlankWorkflow';

export function CreateWorkflowButton() {
    const router = useRouter();
    const { createBlankWorkflow, isCreating, ready } = useCreateBlankWorkflow();

    const handleAgentBuilder = () => {
        router.push('/workflow/create');
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
                <DropdownMenuItem onClick={() => void createBlankWorkflow()} disabled={isCreating || !ready} className="cursor-pointer">
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
