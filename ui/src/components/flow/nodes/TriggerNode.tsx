import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Check, Copy, Edit, Trash2Icon, Webhook } from "lucide-react";
import Link from "next/link";
import { memo, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface TriggerNodeEditFormProps {
    name: string;
    setName: (value: string) => void;
    endpoint: string;
}

interface TriggerNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const TriggerNode = memo(({ data, selected, id }: TriggerNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [name, setName] = useState(data.name || "API Trigger");

    // Generate trigger_path if not present (should be done on node creation)
    const [triggerPath] = useState(() => data.trigger_path ?? crypto.randomUUID());

    // Get backend URL from environment
    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
    const endpoint = `${backendUrl}/api/v1/public/agent/${triggerPath}`;

    // Copy state for button feedback
    const [copied, setCopied] = useState(false);

    const handleCopy = async () => {
        await navigator.clipboard.writeText(endpoint);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            name,
            trigger_path: triggerPath,
        });
        setOpen(false);
        // Save the workflow after updating node data
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    };

    // Reset form state when dialog opens
    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            setName(data.name || "API Trigger");
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setName(data.name || "API Trigger");
        }
    }, [data, open]);

    // Ensure trigger_path is saved on initial render if it was generated
    useEffect(() => {
        if (!data.trigger_path && triggerPath) {
            handleSaveNodeData({
                ...data,
                trigger_path: triggerPath,
                name: data.name || "API Trigger",
            });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || "API Trigger"}
                icon={<Webhook />}
                nodeType="trigger"
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                <div className="space-y-2">
                    <p className="text-xs text-muted-foreground">API Endpoint:</p>
                    <div className="flex items-center gap-1">
                        <code className="text-xs break-all bg-muted px-1 py-0.5 rounded flex-1">
                            {endpoint}
                        </code>
                        <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 shrink-0"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleCopy();
                            }}
                        >
                            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                        </Button>
                    </div>
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
                title="Edit API Trigger"
                onSave={handleSave}
            >
                {open && (
                    <TriggerNodeEditForm
                        name={name}
                        setName={setName}
                        endpoint={endpoint}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const TriggerNodeEditForm = ({
    name,
    setName,
    endpoint,
}: TriggerNodeEditFormProps) => {
    const [copied, setCopied] = useState(false);
    const [curlCopied, setCurlCopied] = useState(false);

    const handleCopyEndpoint = async () => {
        await navigator.clipboard.writeText(endpoint);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const curlExample = `curl -X POST "${endpoint}" \\
  -H "X-API-Key: YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"phone_number": "+1234567890", "initial_context": {}}'`;

    const handleCopyCurl = async () => {
        await navigator.clipboard.writeText(curlExample);
        setCurlCopied(true);
        setTimeout(() => setCurlCopied(false), 2000);
    };

    return (
        <div className="grid gap-4">
            <div className="grid gap-2">
                <Label>Name</Label>
                <Label className="text-xs text-muted-foreground">
                    A display name for this trigger.
                </Label>
                <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                />
            </div>

            <div className="grid gap-2">
                <Label>API Endpoint</Label>
                <Label className="text-xs text-muted-foreground">
                    Use this endpoint to trigger calls via API. Requires an API key in the X-API-Key header.{" "}
                    <Link href="/api-keys" target="_blank" className="text-primary underline hover:no-underline">
                        Get your API key
                    </Link>
                </Label>
                <div className="flex items-center gap-2">
                    <code className="text-xs break-all bg-muted px-2 py-1 rounded flex-1">
                        {endpoint}
                    </code>
                    <Button
                        variant="outline"
                        size="icon"
                        className="shrink-0"
                        onClick={handleCopyEndpoint}
                    >
                        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </Button>
                </div>
            </div>

            <div className="grid gap-2">
                <Label>Example Request</Label>
                <div className="relative">
                    <pre className="text-xs bg-muted px-3 py-2 rounded overflow-x-auto whitespace-pre-wrap">
                        {curlExample}
                    </pre>
                    <Button
                        variant="outline"
                        size="icon"
                        className="absolute top-2 right-2"
                        onClick={handleCopyCurl}
                    >
                        {curlCopied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </Button>
                </div>
            </div>
        </div>
    );
};

TriggerNode.displayName = "TriggerNode";
