import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { ChevronRight, Edit, FileText, Play, PlusIcon, Settings, Trash2Icon, Wrench } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import type { DocumentResponseSchema, ToolResponse } from "@/client/types.gen";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { DocumentBadges } from "@/components/flow/DocumentBadges";
import { DocumentSelector } from "@/components/flow/DocumentSelector";
import { MentionTextarea } from "@/components/flow/MentionTextarea";
import { TextOrAudioInput } from "@/components/flow/TextOrAudioInput";
import { ToolBadges } from "@/components/flow/ToolBadges";
import { ToolSelector } from "@/components/flow/ToolSelector";
import { ExtractionVariable, FlowNodeData } from "@/components/flow/types";
import { CredentialSelector, UrlInput, validateUrl } from "@/components/http";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { CONTEXT_VARIABLES_DOC_URL, NODE_DOCUMENTATION_URLS, PRE_CALL_DATA_FETCH_DOC_URL } from "@/constants/documentation";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface StartCallEditFormProps {
    nodeData: FlowNodeData;
    greetingType: 'text' | 'audio';
    setGreetingType: (value: 'text' | 'audio') => void;
    greeting: string;
    setGreeting: (value: string) => void;
    greetingRecordingId: string;
    setGreetingRecordingId: (value: string) => void;
    prompt: string;
    setPrompt: (value: string) => void;
    name: string;
    setName: (value: string) => void;
    allowInterrupt: boolean;
    setAllowInterrupt: (value: boolean) => void;
    addGlobalPrompt: boolean;
    setAddGlobalPrompt: (value: boolean) => void;
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
    preCallFetchEnabled: boolean;
    setPreCallFetchEnabled: (value: boolean) => void;
    preCallFetchUrl: string;
    setPreCallFetchUrl: (value: string) => void;
    preCallFetchCredentialUuid: string;
    setPreCallFetchCredentialUuid: (value: string) => void;
    tools: ToolResponse[];
    documents: DocumentResponseSchema[];
    recordings: RecordingResponseSchema[];
}

interface StartCallNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const StartCall = memo(({ data, selected, id }: StartCallNodeProps) => {
    const { open, setOpen, handleSaveNodeData } = useNodeHandlers({
        id,
        additionalData: { is_start: true }
    });
    const { saveWorkflow, tools, documents, recordings } = useWorkflow();

    // Form state
    const [greetingType, setGreetingType] = useState<'text' | 'audio'>(data.greeting_type ?? "text");
    const [greeting, setGreeting] = useState(data.greeting ?? "");
    const [greetingRecordingId, setGreetingRecordingId] = useState(data.greeting_recording_id ?? "");
    const [prompt, setPrompt] = useState(data.prompt ?? "");
    const [name, setName] = useState(data.name);
    const [allowInterrupt, setAllowInterrupt] = useState(data.allow_interrupt ?? true);
    const [addGlobalPrompt, setAddGlobalPrompt] = useState(data.add_global_prompt ?? true);
    const [delayedStart, setDelayedStart] = useState(data.delayed_start ?? false);
    const [delayedStartDuration, setDelayedStartDuration] = useState(data.delayed_start_duration ?? 2);
    const [extractionEnabled, setExtractionEnabled] = useState(data.extraction_enabled ?? false);
    const [extractionPrompt, setExtractionPrompt] = useState(data.extraction_prompt ?? "");
    const [variables, setVariables] = useState<ExtractionVariable[]>(data.extraction_variables ?? []);
    const [toolUuids, setToolUuids] = useState<string[]>(data.tool_uuids ?? []);
    const [documentUuids, setDocumentUuids] = useState<string[]>(data.document_uuids ?? []);
    const [preCallFetchEnabled, setPreCallFetchEnabled] = useState(data.pre_call_fetch_enabled ?? false);
    const [preCallFetchUrl, setPreCallFetchUrl] = useState(data.pre_call_fetch_url ?? "");
    const [preCallFetchCredentialUuid, setPreCallFetchCredentialUuid] = useState(data.pre_call_fetch_credential_uuid ?? "");

    // Compute if form has unsaved changes (only check prompt, name, greeting)
    const isDirty = useMemo(() => {
        return (
            greeting !== (data.greeting ?? "") ||
            prompt !== (data.prompt ?? "") ||
            name !== (data.name ?? "")
        );
    }, [greeting, prompt, name, data]);

    const handleSave = async () => {
        // Validate pre-call fetch URL if enabled
        if (preCallFetchEnabled && preCallFetchUrl) {
            const urlValidation = validateUrl(preCallFetchUrl);
            if (!urlValidation.valid) {
                return;
            }
        }

        handleSaveNodeData({
            ...data,
            greeting_type: greetingType,
            greeting: greetingType === 'text' ? (greeting || undefined) : undefined,
            greeting_recording_id: greetingType === 'audio' ? (greetingRecordingId || undefined) : undefined,
            prompt,
            name,
            allow_interrupt: allowInterrupt,
            add_global_prompt: addGlobalPrompt,
            delayed_start: delayedStart,
            delayed_start_duration: delayedStart ? delayedStartDuration : undefined,
            extraction_enabled: extractionEnabled,
            extraction_prompt: extractionPrompt,
            extraction_variables: variables,
            tool_uuids: toolUuids.length > 0 ? toolUuids : undefined,
            document_uuids: documentUuids.length > 0 ? documentUuids : undefined,
            pre_call_fetch_enabled: preCallFetchEnabled,
            pre_call_fetch_url: preCallFetchEnabled ? preCallFetchUrl || undefined : undefined,
            pre_call_fetch_credential_uuid: preCallFetchEnabled && preCallFetchCredentialUuid ? preCallFetchCredentialUuid : undefined,
        });
        setOpen(false);
        await saveWorkflow();
    };

    // Reset form state when dialog opens
    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            setGreetingType(data.greeting_type ?? "text");
            setGreeting(data.greeting ?? "");
            setGreetingRecordingId(data.greeting_recording_id ?? "");
            setPrompt(data.prompt ?? "");
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setToolUuids(data.tool_uuids ?? []);
            setDocumentUuids(data.document_uuids ?? []);
            setPreCallFetchEnabled(data.pre_call_fetch_enabled ?? false);
            setPreCallFetchUrl(data.pre_call_fetch_url ?? "");
            setPreCallFetchCredentialUuid(data.pre_call_fetch_credential_uuid ?? "");
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setGreetingType(data.greeting_type ?? "text");
            setGreeting(data.greeting ?? "");
            setGreetingRecordingId(data.greeting_recording_id ?? "");
            setPrompt(data.prompt ?? "");
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
            setExtractionEnabled(data.extraction_enabled ?? false);
            setExtractionPrompt(data.extraction_prompt ?? "");
            setVariables(data.extraction_variables ?? []);
            setToolUuids(data.tool_uuids ?? []);
            setDocumentUuids(data.document_uuids ?? []);
            setPreCallFetchEnabled(data.pre_call_fetch_enabled ?? false);
            setPreCallFetchUrl(data.pre_call_fetch_url ?? "");
            setPreCallFetchCredentialUuid(data.pre_call_fetch_credential_uuid ?? "");
        }
    }, [data, open]);

    // Handle cleanup of stale document UUIDs
    const handleStaleDocuments = useCallback(async (staleUuids: string[]) => {
        const cleanedUuids = (data.document_uuids ?? []).filter(uuid => !staleUuids.includes(uuid));
        handleSaveNodeData({
            ...data,
            document_uuids: cleanedUuids.length > 0 ? cleanedUuids : undefined,
        });
        await saveWorkflow();
    }, [data, handleSaveNodeData, saveWorkflow]);

    // Handle cleanup of stale tool UUIDs
    const handleStaleTools = useCallback(async (staleUuids: string[]) => {
        const cleanedUuids = (data.tool_uuids ?? []).filter(uuid => !staleUuids.includes(uuid));
        handleSaveNodeData({
            ...data,
            tool_uuids: cleanedUuids.length > 0 ? cleanedUuids : undefined,
        });
        await saveWorkflow();
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
                documentationUrl={NODE_DOCUMENTATION_URLS.startCall}
            >
                {open && (
                    <StartCallEditForm
                        nodeData={data}
                        greetingType={greetingType}
                        setGreetingType={setGreetingType}
                        greeting={greeting}
                        setGreeting={setGreeting}
                        greetingRecordingId={greetingRecordingId}
                        setGreetingRecordingId={setGreetingRecordingId}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        name={name}
                        setName={setName}
                        allowInterrupt={allowInterrupt}
                        setAllowInterrupt={setAllowInterrupt}
                        addGlobalPrompt={addGlobalPrompt}
                        setAddGlobalPrompt={setAddGlobalPrompt}
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
                        preCallFetchEnabled={preCallFetchEnabled}
                        setPreCallFetchEnabled={setPreCallFetchEnabled}
                        preCallFetchUrl={preCallFetchUrl}
                        setPreCallFetchUrl={setPreCallFetchUrl}
                        preCallFetchCredentialUuid={preCallFetchCredentialUuid}
                        setPreCallFetchCredentialUuid={setPreCallFetchCredentialUuid}
                        tools={tools ?? []}
                        documents={documents ?? []}
                        recordings={recordings ?? []}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const StartCallEditForm = ({
    greetingType,
    setGreetingType,
    greeting,
    setGreeting,
    greetingRecordingId,
    setGreetingRecordingId,
    prompt,
    setPrompt,
    name,
    setName,
    allowInterrupt,
    setAllowInterrupt,
    addGlobalPrompt,
    setAddGlobalPrompt,
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
    preCallFetchEnabled,
    setPreCallFetchEnabled,
    preCallFetchUrl,
    setPreCallFetchUrl,
    preCallFetchCredentialUuid,
    setPreCallFetchCredentialUuid,
    tools,
    documents,
    recordings,
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

            <Label>Greeting</Label>
            <Label className="text-xs text-muted-foreground">
                Optional greeting played when the call starts. Choose between a text message (spoken via TTS) or a pre-recorded audio file.
            </Label>
            <TextOrAudioInput
                type={greetingType}
                onTypeChange={setGreetingType}
                recordingId={greetingRecordingId}
                onRecordingIdChange={setGreetingRecordingId}
                recordings={recordings}
            >
                <Textarea
                    value={greeting}
                    onChange={(e) => setGreeting(e.target.value)}
                    className="min-h-[60px] max-h-[200px] resize-none overflow-y-auto"
                    placeholder="e.g. Hello {{first_name}}, this is Sarah calling from Acme Corp."
                />
            </TextOrAudioInput>

            <Label>Prompt</Label>
            <Label className="text-xs text-muted-foreground">
                Enter the prompt for the agent. This will be used to generate the agent&apos;s response. Supports <a href={CONTEXT_VARIABLES_DOC_URL} target="_blank" rel="noopener noreferrer" className="underline">template variables</a>
            </Label>
            <MentionTextarea
                value={prompt}
                onChange={setPrompt}
                className="min-h-[100px] max-h-[300px] resize-none overflow-y-auto"
                placeholder="Enter a prompt"
                recordings={recordings}
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

            {/* Advanced Settings */}
            <div className="pt-4 border-t mt-4">
                <Collapsible>
                    <CollapsibleTrigger className="flex items-center gap-2 w-full text-sm font-medium hover:text-foreground text-muted-foreground">
                        <Settings className="h-4 w-4" />
                        <span>Advanced Settings</span>
                        <ChevronRight className="h-4 w-4 ml-auto transition-transform [[data-state=open]>svg&]:rotate-90" />
                    </CollapsibleTrigger>
                    <CollapsibleContent className="mt-4 space-y-4">
                        {/* Pre-Call Data Fetch */}
                        <div className="flex items-center space-x-2">
                            <Switch
                                id="pre-call-fetch"
                                checked={preCallFetchEnabled}
                                onCheckedChange={setPreCallFetchEnabled}
                            />
                            <Label htmlFor="pre-call-fetch">Pre-Call Data Fetch</Label>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Fetch data from an external API before the call starts. A standardized POST request with caller/called numbers will be sent. The JSON response fields will be merged into the call context and available as template variables in your prompts.{" "}
                            <a href={PRE_CALL_DATA_FETCH_DOC_URL} target="_blank" rel="noopener noreferrer" className="underline">Learn more</a>
                        </p>

                        {preCallFetchEnabled && (
                            <div className="border rounded-md p-4 space-y-4 bg-muted/20">
                                <div className="grid gap-2">
                                    <Label>Endpoint URL</Label>
                                    <Label className="text-xs text-muted-foreground">
                                        The URL to send the pre-call data fetch request to.
                                    </Label>
                                    <UrlInput
                                        value={preCallFetchUrl}
                                        onChange={setPreCallFetchUrl}
                                        placeholder="https://api.example.com/customer-lookup"
                                        showValidation
                                    />
                                </div>

                                <div className="grid gap-2">
                                    <Label>Authentication</Label>
                                    <CredentialSelector
                                        value={preCallFetchCredentialUuid}
                                        onChange={setPreCallFetchCredentialUuid}
                                    />
                                </div>
                            </div>
                        )}
                    </CollapsibleContent>
                </Collapsible>
            </div>
        </div>
    );
};

StartCall.displayName = "StartCall";
