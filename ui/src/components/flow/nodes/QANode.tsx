import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Circle, ClipboardCheck, Edit, Trash2Icon } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface QANodeProps extends NodeProps {
    data: FlowNodeData;
}

export const QANode = memo(({ data, selected, id }: QANodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [name, setName] = useState(data.name || "QA Analysis");
    const [qaEnabled, setQaEnabled] = useState(data.qa_enabled ?? true);
    const [qaModel, setQaModel] = useState(data.qa_model || "default");
    const [qaSystemPrompt, setQaSystemPrompt] = useState(data.qa_system_prompt || "");

    const isDirty = useMemo(() => {
        return (
            name !== (data.name || "QA Analysis") ||
            qaEnabled !== (data.qa_enabled ?? true) ||
            qaModel !== (data.qa_model || "default") ||
            qaSystemPrompt !== (data.qa_system_prompt || "")
        );
    }, [name, qaEnabled, qaModel, qaSystemPrompt, data]);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            name,
            qa_enabled: qaEnabled,
            qa_model: qaModel,
            qa_system_prompt: qaSystemPrompt,
        });
        setOpen(false);
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            setName(data.name || "QA Analysis");
            setQaEnabled(data.qa_enabled ?? true);
            setQaModel(data.qa_model || "default");
            setQaSystemPrompt(data.qa_system_prompt || "");
        }
        setOpen(newOpen);
    };

    useEffect(() => {
        if (open) {
            setName(data.name || "QA Analysis");
            setQaEnabled(data.qa_enabled ?? true);
            setQaModel(data.qa_model || "default");
            setQaSystemPrompt(data.qa_system_prompt || "");
        }
    }, [data, open]);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || "QA Analysis"}
                icon={<ClipboardCheck />}
                nodeType="qa"
                onDoubleClick={() => handleOpenChange(true)}
                nodeId={id}
            >
                <div className="space-y-2">
                    <div className="flex items-center gap-2">
                        <span className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded">
                            {data.qa_model || "default"}
                        </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <Circle
                            className={`h-2 w-2 ${data.qa_enabled !== false ? "fill-green-500 text-green-500" : "fill-gray-400 text-gray-400"}`}
                        />
                        <span className="text-xs text-muted-foreground">
                            {data.qa_enabled !== false ? "Enabled" : "Disabled"}
                        </span>
                    </div>
                </div>
            </NodeContent>

            <NodeToolbar isVisible={selected} position={Position.Right}>
                <div className="flex flex-col gap-1">
                    <Button onClick={() => handleOpenChange(true)} variant="outline" size="icon">
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
                title="Edit QA Analysis"
                onSave={handleSave}
                isDirty={isDirty}
            >
                {open && (
                    <QANodeEditForm
                        name={name}
                        setName={setName}
                        qaEnabled={qaEnabled}
                        setQaEnabled={setQaEnabled}
                        qaModel={qaModel}
                        setQaModel={setQaModel}
                        qaSystemPrompt={qaSystemPrompt}
                        setQaSystemPrompt={setQaSystemPrompt}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

interface QANodeEditFormProps {
    name: string;
    setName: (value: string) => void;
    qaEnabled: boolean;
    setQaEnabled: (value: boolean) => void;
    qaModel: string;
    setQaModel: (value: string) => void;
    qaSystemPrompt: string;
    setQaSystemPrompt: (value: string) => void;
}

const QANodeEditForm = ({
    name,
    setName,
    qaEnabled,
    setQaEnabled,
    qaModel,
    setQaModel,
    qaSystemPrompt,
    setQaSystemPrompt,
}: QANodeEditFormProps) => {
    return (
        <div className="space-y-4">
            <div className="grid gap-2">
                <Label>Name</Label>
                <Label className="text-xs text-muted-foreground">
                    A display name for this QA analysis node.
                </Label>
                <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>

            <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                <Switch id="qa-enabled" checked={qaEnabled} onCheckedChange={setQaEnabled} />
                <Label htmlFor="qa-enabled">Enabled</Label>
                <Label className="text-xs text-muted-foreground ml-2">
                    Whether this QA analysis runs after each call.
                </Label>
            </div>

            <div className="grid gap-2">
                <Label>Model</Label>
                <Label className="text-xs text-muted-foreground">
                    LLM model to use. Set to &quot;default&quot; to use your configured LLM.
                </Label>
                <Input
                    value={qaModel}
                    onChange={(e) => setQaModel(e.target.value)}
                    placeholder="default"
                />
            </div>

            <div className="grid gap-2">
                <Label>System Prompt</Label>
                <Label className="text-xs text-muted-foreground">
                    The prompt sent to the LLM for QA analysis. Use {'{metrics}'} placeholder for call metrics.
                </Label>
                <Textarea
                    value={qaSystemPrompt}
                    onChange={(e) => setQaSystemPrompt(e.target.value)}
                    className="min-h-[300px] font-mono text-xs"
                    placeholder="Enter QA analysis system prompt..."
                />
            </div>
        </div>
    );
};

QANode.displayName = "QANode";
