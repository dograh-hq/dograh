import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Edit, Headset, Trash2Icon } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface GlobalNodeEditFormProps {
    nodeData: FlowNodeData;
    prompt: string;
    setPrompt: (value: string) => void;
    name: string;
    setName: (value: string) => void;
}

interface GlobalNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const GlobalNode = memo(({ data, selected, id }: GlobalNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [prompt, setPrompt] = useState(data.prompt);
    const [name, setName] = useState(data.name);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            prompt,
            is_static: false,
            name
        });
        setOpen(false);
        // Save the workflow after updating node data with a small delay to ensure state is updated
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
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
                bgColor="bg-orange-300"
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                <div className="text-sm text-muted-foreground">
                    {data.prompt?.length > 30 ? `${data.prompt.substring(0, 30)}...` : data.prompt}
                </div>
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
            >
                {open && (
                    <GlobalNodeEditForm
                        nodeData={data}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        name={name}
                        setName={setName}
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
    setName
}: GlobalNodeEditFormProps) => {
    return (
        <div className="grid gap-2">
            <Label>Name</Label>
            <Label className="text-xs text-gray-500">
                The name of the global node.
            </Label>
            <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
            />

            <Label>Prompt</Label>
            <Label className="text-xs text-gray-500">
                This is the global prompt. This will be added to the system prompt of all the agents.
            </Label>
            <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="min-h-[100px] max-h-[300px] resize-none"
                style={{
                    overflowY: 'auto'
                }}
            />
        </div>
    );
};

GlobalNode.displayName = "GlobalNode";

