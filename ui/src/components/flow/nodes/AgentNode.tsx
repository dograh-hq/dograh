import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Edit, Headset, PlusIcon,Trash2Icon } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { ExtractionVariable,FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface AgentNodeEditFormProps {
    nodeData: FlowNodeData;
    prompt: string;
    setPrompt: (value: string) => void;
    name: string;
    setName: (value: string) => void;
    allowInterrupt: boolean;
    setAllowInterrupt: (value: boolean) => void;
    extractionEnabled: boolean;
    setExtractionEnabled: (value: boolean) => void;
    extractionPrompt: string;
    setExtractionPrompt: (value: string) => void;
    variables: ExtractionVariable[];
    setVariables: (vars: ExtractionVariable[]) => void;
    addGlobalPrompt: boolean;
    setAddGlobalPrompt: (value: boolean) => void;
}

interface AgentNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const AgentNode = memo(({ data, selected, id }: AgentNodeProps) => {
    const { open, setOpen, handleSaveNodeData, handleDeleteNode } = useNodeHandlers({ id });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [prompt, setPrompt] = useState(data.prompt);
    const [name, setName] = useState(data.name);
    const [allowInterrupt, setAllowInterrupt] = useState(data.allow_interrupt ?? true);

    // Variable Extraction state
    const [extractionEnabled, setExtractionEnabled] = useState(data.extraction_enabled ?? false);
    const [extractionPrompt, setExtractionPrompt] = useState(data.extraction_prompt ?? "");
    const [variables, setVariables] = useState<ExtractionVariable[]>(data.extraction_variables ?? []);
    const [addGlobalPrompt, setAddGlobalPrompt] = useState(data.add_global_prompt ?? true);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            prompt,
            name,
            allow_interrupt: allowInterrupt,
            extraction_enabled: extractionEnabled,
            extraction_prompt: extractionPrompt,
            extraction_variables: variables,
            add_global_prompt: addGlobalPrompt,
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
            setAllowInterrupt(data.allow_interrupt ?? true);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setPrompt(data.prompt);
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
        }
    }, [data, open]);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title={data.name || 'Agent'}
                icon={<Headset />}
                bgColor="bg-blue-300"
                hasSourceHandle={true}
                hasTargetHandle={true}
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
                title="Edit Agent"
                onSave={handleSave}
            >
                {open && (
                    <AgentNodeEditForm
                        nodeData={data}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        name={name}
                        setName={setName}
                        allowInterrupt={allowInterrupt}
                        setAllowInterrupt={setAllowInterrupt}
                        extractionEnabled={extractionEnabled}
                        setExtractionEnabled={setExtractionEnabled}
                        extractionPrompt={extractionPrompt}
                        setExtractionPrompt={setExtractionPrompt}
                        variables={variables}
                        setVariables={setVariables}
                        addGlobalPrompt={addGlobalPrompt}
                        setAddGlobalPrompt={setAddGlobalPrompt}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const AgentNodeEditForm = ({
    prompt,
    setPrompt,
    name,
    setName,
    allowInterrupt,
    setAllowInterrupt,
    extractionEnabled,
    setExtractionEnabled,
    extractionPrompt,
    setExtractionPrompt,
    variables,
    setVariables,
    addGlobalPrompt,
    setAddGlobalPrompt,
}: AgentNodeEditFormProps) => {
    const handleVariableNameChange = (idx: number, value: string) => {
        const newVars = [...variables];
        newVars[idx] = { ...newVars[idx], name: value };
        setVariables(newVars);
    };

    const handleVariableTypeChange = (idx: number, value: 'string' | 'number' | 'boolean') => {
        const newVars = [...variables];
        newVars[idx] = { ...newVars[idx], type: value };
        setVariables(newVars);
    };

    const handleVariablePromptChange = (idx: number, value: string) => {
        const newVars = [...variables];
        newVars[idx] = { ...newVars[idx], prompt: value };
        setVariables(newVars);
    };

    const handleRemoveVariable = (idx: number) => {
        const newVars = variables.filter((_, i) => i !== idx);
        setVariables(newVars);
    };

    const handleAddVariable = () => {
        setVariables([...variables, { name: '', type: 'string', prompt: '' }]);
    };

    return (
        <div className="grid gap-2">
            <Label>Name</Label>
            <Label className="text-xs text-gray-500">
                The name of the agent that will be used to identify the agent in the call logs. It should be short and should identify the step in the call.
            </Label>
            <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
            />

            <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                <Switch id="allow-interrupt" checked={allowInterrupt} onCheckedChange={setAllowInterrupt} />
                <Label htmlFor="allow-interrupt">Allow Interruption</Label>
                <Label className="text-xs text-gray-500 ml-2">
                    Whether you would like user to be able to interrupt the bot.
                </Label>
            </div>

            <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                <Switch id="add-global-prompt" checked={addGlobalPrompt} onCheckedChange={setAddGlobalPrompt} />
                <Label htmlFor="add-global-prompt">Add Global Prompt</Label>
                <Label className="text-xs text-gray-500 ml-2">
                    Whether you want to add global prompt with this node&apos;s prompt.
                </Label>
            </div>


            <div className="pt-2 space-y-2">
                <Label>Prompt</Label>
                <Label className="text-xs text-gray-500">
                    Enter the prompt for the agent. This will be used to generate the agent&apos;s response. Prompt engineering&apos;s best practices apply.
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


            {/* Variable Extraction Section */}
            <div className="flex items-center space-x-2 pt-2">
                <Switch id="enable-extraction" checked={extractionEnabled} onCheckedChange={setExtractionEnabled} />
                <Label htmlFor="enable-extraction">Enable Variable Extraction</Label>
                <Label className="text-xs text-gray-500 ml-2">
                    Are there any variables you would like to extract from the conversation?
                </Label>
            </div>

            {extractionEnabled && (
                <div className="border rounded-md p-3 mt-2 space-y-2 bg-muted/20">
                    <Label>Extraction Prompt</Label>
                    <Label className="text-xs text-gray-500">
                        Provide an overall extraction prompt that guides how variables should be extracted from the conversation.
                    </Label>
                    <Textarea
                        value={extractionPrompt}
                        onChange={(e) => setExtractionPrompt(e.target.value)}
                        className="min-h-[80px] max-h-[200px] resize-none"
                        style={{ overflowY: 'auto' }}
                    />

                    <Label>Variables</Label>
                    <Label className="text-xs text-gray-500">
                        Define each variable you want to extract along with its data type.
                    </Label>

                    {variables.map((v, idx) => (
                        <div key={idx} className="space-y-2 border rounded-md p-2 bg-background">
                            <div className="flex items-center gap-2">
                                <Input
                                    placeholder="Variable name"
                                    value={v.name}
                                    onChange={(e) => handleVariableNameChange(idx, e.target.value)}
                                />
                                <select
                                    className="border rounded-md p-2 text-sm bg-background"
                                    value={v.type}
                                    onChange={(e) => handleVariableTypeChange(idx, e.target.value as 'string' | 'number' | 'boolean')}
                                >
                                    <option value="string">String</option>
                                    <option value="number">Number</option>
                                    <option value="boolean">Boolean</option>
                                </select>
                                <Button variant="outline" size="icon" onClick={() => handleRemoveVariable(idx)}>
                                    <Trash2Icon className="w-4 h-4" />
                                </Button>
                            </div>
                            <Textarea
                                placeholder="Extraction prompt for this variable"
                                value={v.prompt ?? ''}
                                onChange={(e) => handleVariablePromptChange(idx, e.target.value)}
                                className="min-h-[60px] resize-none"
                            />
                        </div>
                    ))}

                    <Button variant="outline" size="sm" className="w-fit" onClick={handleAddVariable}>
                        <PlusIcon className="w-4 h-4 mr-1" /> Add Variable
                    </Button>
                </div>
            )}
        </div>
    );
};

AgentNode.displayName = "AgentNode";

