import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Edit, Headset, Trash2Icon } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { MentionTextarea } from "@/components/flow/MentionTextarea";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { CONTEXT_VARIABLES_DOC_URL, NODE_DOCUMENTATION_URLS } from "@/constants/documentation";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface GlobalNodeEditFormProps {
    nodeData: FlowNodeData;
    prompt: string;
    setPrompt: (value: string) => void;
    name: string;
    setName: (value: string) => void;
    recordings: RecordingResponseSchema[];
}

interface GlobalNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const GlobalNode = memo(({ data, selected, id }: GlobalNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow, recordings } = useWorkflow();

    // Form state
    const [prompt, setPrompt] = useState(data.prompt);
    const [name, setName] = useState(data.name);

    // Compute if form has unsaved changes (simplified: only check prompt, name)
    const isDirty = useMemo(() => {
        return (
            prompt !== (data.prompt ?? "") ||
            name !== (data.name ?? "")
        );
    }, [prompt, name, data]);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            prompt,
            is_static: false,
            name
        });
        setOpen(false);
        await saveWorkflow();
    };

    // Reset form state when dialog opens
    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            setPrompt(data.prompt);
            setName(data.name);
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setPrompt(data.prompt);
            setName(data.name);
        }
    }, [data, open]);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || 'Global'}
                icon={<Headset />}
                nodeType="global"
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                <p className="text-sm text-muted-foreground line-clamp-5 leading-relaxed">
                    {data.prompt || 'No prompt configured'}
                </p>
            </NodeContent>

            <NodeToolbar isVisible={selected} position={Position.Right}>
                <div className="flex flex-col gap-1">
                    <Button onClick={() => setOpen(true)} variant="outline" size="icon">
                        <Edit />
                    </Button>
                    <Button onClick={handleDeleteNode} variant="outline" size="icon">
                        <Trash2Icon />
                    </Button>
                </div>
            </NodeToolbar>

            <NodeEditDialog
                open={open}
                onOpenChange={handleOpenChange}
                nodeData={data}
                title="Edit Global Node"
                onSave={handleSave}
                isDirty={isDirty}
                documentationUrl={NODE_DOCUMENTATION_URLS.global}
            >
                {open && (
                    <GlobalNodeEditForm
                        nodeData={data}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        name={name}
                        setName={setName}
                        recordings={recordings ?? []}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const GlobalNodeEditForm = ({
    prompt,
    setPrompt,
    name,
    setName,
    recordings,
}: GlobalNodeEditFormProps) => {
    return (
        <div className="grid gap-2">
            <Label>Name</Label>
            <Label className="text-xs text-muted-foreground">
                The name of the global node.
            </Label>
            <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
            />

            <Label>Prompt</Label>
            <Label className="text-xs text-muted-foreground">
                This is the global prompt. This will be added to the system prompt of all the agents. Supports <a href={CONTEXT_VARIABLES_DOC_URL} target="_blank" rel="noopener noreferrer" className="underline">template variables</a>
            </Label>
            <MentionTextarea
                value={prompt}
                onChange={setPrompt}
                className="min-h-[100px] max-h-[300px] resize-none overflow-y-auto"
                placeholder="Enter a prompt"
                recordings={recordings}
            />
        </div>
    );
};

GlobalNode.displayName = "GlobalNode";

