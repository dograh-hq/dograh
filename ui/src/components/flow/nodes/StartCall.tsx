import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Edit, FileText, Play, PlusIcon, Trash2Icon, Wrench } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { DocumentResponseSchema, ToolResponse } from "@/client/types.gen";
import { DocumentBadges } from "@/components/flow/DocumentBadges";
import { DocumentSelector } from "@/components/flow/DocumentSelector";
import { ToolBadges } from "@/components/flow/ToolBadges";
import { ToolSelector } from "@/components/flow/ToolSelector";
import { ExtractionVariable, FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface StartCallEditFormProps {
    nodeData: FlowNodeData;
    prompt: string;
    setPrompt: (value: string) => void;
    name: string;
    setName: (value: string) => void;
    allowInterrupt: boolean;
    setAllowInterrupt: (value: boolean) => void;
    addGlobalPrompt: boolean;
    setAddGlobalPrompt: (value: boolean) => void;
    detectVoicemail: boolean;
    setDetectVoicemail: (value: boolean) => void;
    delayedStart: boolean;
    setDelayedStart: (value: boolean) => void;
    delayedStartDuration: number;
    setDelayedStartDuration: (value: number) => void;
    extractionEnabled: boolean;
    setExtractionEnabled: (value: boolean) => void;
    extractionPrompt: string;
    setExtractionPrompt: (value: string) => void;
    variables: ExtractionVariable[];
    setVariables: (vars: ExtractionVariable[]) => void;
    toolUuids: string[];
    setToolUuids: (value: string[]) => void;
    documentUuids: string[];
    setDocumentUuids: (value: string[]) => void;
    tools: ToolResponse[];
    documents: DocumentResponseSchema[];
}

interface StartCallNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const StartCall = memo(({ data, selected, id }: StartCallNodeProps) => {
    const { open, setOpen, handleSaveNodeData } = useNodeHandlers({
        id,
        additionalData: { is_start: true }
    });
    const { saveWorkflow, tools, documents } = useWorkflow();

    // Form state
    const [prompt, setPrompt] = useState(data.prompt ?? "");
    const [name, setName] = useState(data.name);
    const [allowInterrupt, setAllowInterrupt] = useState(data.allow_interrupt ?? true);
    const [addGlobalPrompt, setAddGlobalPrompt] = useState(data.add_global_prompt ?? true);
    const [detectVoicemail, setDetectVoicemail] = useState(data.detect_voicemail ?? false);
    const [delayedStart, setDelayedStart] = useState(data.delayed_start ?? false);
    const [delayedStartDuration, setDelayedStartDuration] = useState(data.delayed_start_duration ?? 2);
    const [extractionEnabled, setExtractionEnabled] = useState(data.extraction_enabled ?? false);
    const [extractionPrompt, setExtractionPrompt] = useState(data.extraction_prompt ?? "");
    const [variables, setVariables] = useState<ExtractionVariable[]>(data.extraction_variables ?? []);
    const [toolUuids, setToolUuids] = useState<string[]>(data.tool_uuids ?? []);
    const [documentUuids, setDocumentUuids] = useState<string[]>(data.document_uuids ?? []);

    // Compute if form has unsaved changes (only check prompt, name)
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
            name,
            allow_interrupt: allowInterrupt,
            add_global_prompt: addGlobalPrompt,
            detect_voicemail: detectVoicemail,
            delayed_start: delayedStart,
            delayed_start_duration: delayedStart ? delayedStartDuration : undefined,
            extraction_enabled: extractionEnabled,
            extraction_prompt: extractionPrompt,
            extraction_variables: variables,
            tool_uuids: toolUuids.length > 0 ? toolUuids : undefined,
            document_uuids: documentUuids.length > 0 ? documentUuids : undefined,
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
            setPrompt(data.prompt ?? "");
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setDetectVoicemail(data.detect_voicemail ?? false);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setToolUuids(data.tool_uuids ?? []);
            setDocumentUuids(data.document_uuids ?? []);
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setPrompt(data.prompt ?? "");
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setDetectVoicemail(data.detect_voicemail ?? false);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setToolUuids(data.tool_uuids ?? []);
            setDocumentUuids(data.document_uuids ?? []);
        }
    }, [data, open]);

    // Handle cleanup of stale document UUIDs
    const handleStaleDocuments = useCallback((staleUuids: string[]) => {
        const cleanedUuids = (data.document_uuids ?? []).filter(uuid => !staleUuids.includes(uuid));
        handleSaveNodeData({
            ...data,
            document_uuids: cleanedUuids.length > 0 ? cleanedUuids : undefined,
        });
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    }, [data, handleSaveNodeData, saveWorkflow]);

    // Handle cleanup of stale tool UUIDs
    const handleStaleTools = useCallback((staleUuids: string[]) => {
        const cleanedUuids = (data.tool_uuids ?? []).filter(uuid => !staleUuids.includes(uuid));
        handleSaveNodeData({
            ...data,
            tool_uuids: cleanedUuids.length > 0 ? cleanedUuids : undefined,
        });
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    }, [data, handleSaveNodeData, saveWorkflow]);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title="Start Call"
                icon={<Play />}
                nodeType="start"
                hasSourceHandle={true}
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                <p className="text-sm text-muted-foreground line-clamp-5 leading-relaxed">
                    {data.prompt || 'No prompt configured'}
                </p>
                {data.tool_uuids && data.tool_uuids.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border/50">
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
                            <Wrench className="h-3 w-3" />
                            <span>Tools:</span>
                        </div>
                        <ToolBadges toolUuids={data.tool_uuids} onStaleUuidsDetected={handleStaleTools} />
                    </div>
                )}
                {data.document_uuids && data.document_uuids.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border/50">
                        <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
                            <FileText className="h-3 w-3" />
                            <span>Documents:</span>
                        </div>
                        <DocumentBadges documentUuids={data.document_uuids} onStaleUuidsDetected={handleStaleDocuments} />
                    </div>
                )}
            </NodeContent>

            <NodeToolbar isVisible={selected} position={Position.Right}>
                <Button onClick={() => setOpen(true)} variant="outline" size="icon">
                    <Edit />
                </Button>
            </NodeToolbar>

            <NodeEditDialog
                open={open}
                onOpenChange={handleOpenChange}
                nodeData={data}
                title="Start Call"
                onSave={handleSave}
                isDirty={isDirty}
            >
                {open && (
                    <StartCallEditForm
                        nodeData={data}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        name={name}
                        setName={setName}
                        allowInterrupt={allowInterrupt}
                        setAllowInterrupt={setAllowInterrupt}
                        addGlobalPrompt={addGlobalPrompt}
                        setAddGlobalPrompt={setAddGlobalPrompt}
                        detectVoicemail={detectVoicemail}
                        setDetectVoicemail={setDetectVoicemail}
                        delayedStart={delayedStart}
                        setDelayedStart={setDelayedStart}
                        delayedStartDuration={delayedStartDuration}
                        setDelayedStartDuration={setDelayedStartDuration}
                        extractionEnabled={extractionEnabled}
                        setExtractionEnabled={setExtractionEnabled}
                        extractionPrompt={extractionPrompt}
                        setExtractionPrompt={setExtractionPrompt}
                        variables={variables}
                        setVariables={setVariables}
                        toolUuids={toolUuids}
                        setToolUuids={setToolUuids}
                        documentUuids={documentUuids}
                        setDocumentUuids={setDocumentUuids}
                        tools={tools ?? []}
                        documents={documents ?? []}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const StartCallEditForm = ({
    prompt,
    setPrompt,
    name,
    setName,
    allowInterrupt,
    setAllowInterrupt,
    addGlobalPrompt,
    setAddGlobalPrompt,
    detectVoicemail,
    setDetectVoicemail,
    delayedStart,
    setDelayedStart,
    delayedStartDuration,
    setDelayedStartDuration,
    extractionEnabled,
    setExtractionEnabled,
    extractionPrompt,
    setExtractionPrompt,
    variables,
    setVariables,
    toolUuids,
    setToolUuids,
    documentUuids,
    setDocumentUuids,
    tools,
    documents,
}: StartCallEditFormProps) => {
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
            <Label className="text-xs text-muted-foreground">
                The name of the agent that will be used to identify the agent in the call logs. It should be short and should identify the step in the call.
            </Label>
            <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
            />

            <Label>Prompt</Label>
            <Label className="text-xs text-muted-foreground">
                Enter the prompt for the agent. This will be used to generate the agent&apos;s response. Prompt engineering&apos;s best practices apply.
            </Label>
            <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="min-h-[100px] max-h-[300px] resize-none"
                style={{
                    overflowY: 'auto'
                }}
                placeholder="Enter a prompt"
            />
            <div className="flex items-center space-x-2">
                <Switch id="allow-interrupt" checked={allowInterrupt} onCheckedChange={setAllowInterrupt} />
                <Label htmlFor="allow-interrupt">Allow Interruption</Label>
                <Label className="text-xs text-muted-foreground">
                    Whether you would like user to be able to interrupt the bot.
                </Label>
            </div>
            <div className="flex items-center space-x-2">
                <Switch
                    id="add-global-prompt"
                    checked={addGlobalPrompt}
                    onCheckedChange={setAddGlobalPrompt}
                />
                <Label htmlFor="add-global-prompt">
                    Add Global Prompt
                </Label>
            </div>
            <div className="flex items-center space-x-2">
                <Switch
                    id="detect-voicemail"
                    checked={detectVoicemail}
                    onCheckedChange={setDetectVoicemail}
                />
                <Label htmlFor="detect-voicemail">
                    Detect Voicemail
                </Label>
                <Label className="text-xs text-muted-foreground">
                    Automatically detect and end call if voicemail is reached.
                </Label>
            </div>
            <div className="flex flex-col space-y-2">
                <div className="flex items-center space-x-2">
                    <Switch
                        id="delayed-start"
                        checked={delayedStart}
                        onCheckedChange={setDelayedStart}
                    />
                    <Label htmlFor="delayed-start">
                        Delayed Start
                    </Label>
                    <Label className="text-xs text-muted-foreground">
                        Introduce a delay before the agent starts speaking.
                    </Label>
                </div>
                {delayedStart && (
                    <div className="ml-6 flex items-center space-x-2">
                        <Label htmlFor="delay-duration" className="text-sm">
                            Delay (seconds):
                        </Label>
                        <Input
                            id="delay-duration"
                            type="number"
                            step="0.1"
                            min="0.1"
                            max="10"
                            value={delayedStartDuration}
                            onChange={(e) => setDelayedStartDuration(parseFloat(e.target.value) || 3)}
                            className="w-20"
                        />
                    </div>
                )}
            </div>

            {/* Variable Extraction Section */}
            <div className="flex items-center space-x-2 pt-2">
                <Switch id="enable-extraction" checked={extractionEnabled} onCheckedChange={setExtractionEnabled} />
                <Label htmlFor="enable-extraction">Enable Variable Extraction</Label>
                <Label className="text-xs text-muted-foreground ml-2">
                    Are there any variables you would like to extract from the conversation?
                </Label>
            </div>

            {extractionEnabled && (
                <div className="border rounded-md p-3 mt-2 space-y-2 bg-muted/20">
                    <Label>Extraction Prompt</Label>
                    <Label className="text-xs text-muted-foreground">
                        Provide an overall extraction prompt that guides how variables should be extracted from the conversation.
                    </Label>
                    <Textarea
                        value={extractionPrompt}
                        onChange={(e) => setExtractionPrompt(e.target.value)}
                        className="min-h-[80px] max-h-[200px] resize-none"
                        style={{ overflowY: 'auto' }}
                    />

                    <Label>Variables</Label>
                    <Label className="text-xs text-muted-foreground">
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

            {/* Tools Section */}
            <div className="pt-4 border-t mt-4">
                <ToolSelector
                    value={toolUuids}
                    onChange={setToolUuids}
                    tools={tools}
                    description="Select tools that the agent can invoke during this conversation step."
                />
            </div>

            {/* Documents Section */}
            <div className="pt-4 border-t mt-4">
                <DocumentSelector
                    value={documentUuids}
                    onChange={setDocumentUuids}
                    documents={documents}
                    description="Select documents from the knowledge base that the agent can reference during this conversation step."
                />
            </div>
        </div>
    );
};

StartCall.displayName = "StartCall";
