import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { ChevronDown, ChevronRight, Circle, ClipboardCheck, Edit, Trash2Icon } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import { LLMConfigSelector } from "@/components/LLMConfigSelector";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { NODE_DOCUMENTATION_URLS } from "@/constants/documentation";

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
    const [useWorkflowLlm, setUseWorkflowLlm] = useState(data.qa_use_workflow_llm ?? true);
    const [qaProvider, setQaProvider] = useState(data.qa_provider || "openai");
    const [qaModel, setQaModel] = useState(data.qa_model || "gpt-4.1");
    const [qaApiKey, setQaApiKey] = useState(data.qa_api_key || "");
    const [qaSystemPrompt, setQaSystemPrompt] = useState(data.qa_system_prompt || "");
    const [minCallDuration, setMinCallDuration] = useState(data.qa_min_call_duration ?? 15);
    const [qaVoicemailCalls, setQaVoicemailCalls] = useState(data.qa_voicemail_calls ?? false);
    const [qaSampleRate, setQaSampleRate] = useState(data.qa_sample_rate ?? 100);

    const isDirty = useMemo(() => {
        return (
            name !== (data.name || "QA Analysis") ||
            qaEnabled !== (data.qa_enabled ?? true) ||
            useWorkflowLlm !== (data.qa_use_workflow_llm ?? true) ||
            qaProvider !== (data.qa_provider || "openai") ||
            qaModel !== (data.qa_model || "gpt-4.1") ||
            qaApiKey !== (data.qa_api_key || "") ||
            qaSystemPrompt !== (data.qa_system_prompt || "") ||
            minCallDuration !== (data.qa_min_call_duration ?? 15) ||
            qaVoicemailCalls !== (data.qa_voicemail_calls ?? false) ||
            qaSampleRate !== (data.qa_sample_rate ?? 100)
        );
    }, [name, qaEnabled, useWorkflowLlm, qaProvider, qaModel, qaApiKey, qaSystemPrompt, minCallDuration, qaVoicemailCalls, qaSampleRate, data]);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            name,
            qa_enabled: qaEnabled,
            qa_use_workflow_llm: useWorkflowLlm,
            qa_provider: qaProvider,
            qa_model: qaModel,
            qa_api_key: qaApiKey,
            qa_system_prompt: qaSystemPrompt,
            qa_min_call_duration: minCallDuration,
            qa_voicemail_calls: qaVoicemailCalls,
            qa_sample_rate: qaSampleRate,
        });
        setOpen(false);
        await saveWorkflow();
    };

    const resetFormState = () => {
        setName(data.name || "QA Analysis");
        setQaEnabled(data.qa_enabled ?? true);
        setUseWorkflowLlm(data.qa_use_workflow_llm ?? true);
        setQaProvider(data.qa_provider || "openai");
        setQaModel(data.qa_model || "gpt-4.1");
        setQaApiKey(data.qa_api_key || "");
        setQaSystemPrompt(data.qa_system_prompt || "");
        setMinCallDuration(data.qa_min_call_duration ?? 15);
        setQaVoicemailCalls(data.qa_voicemail_calls ?? false);
        setQaSampleRate(data.qa_sample_rate ?? 100);
    };

    const handleOpenChange = (newOpen: boolean) => {
        if (newOpen) {
            resetFormState();
        }
        setOpen(newOpen);
    };

    useEffect(() => {
        if (open) {
            resetFormState();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
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
                            {data.qa_use_workflow_llm !== false
                                ? "Workflow LLM"
                                : `${data.qa_provider || "openai"}/${data.qa_model || "gpt-4.1"}`}
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
                documentationUrl={NODE_DOCUMENTATION_URLS.qaAnalysis}
            >
                {open && (
                    <QANodeEditForm
                        name={name}
                        setName={setName}
                        qaEnabled={qaEnabled}
                        setQaEnabled={setQaEnabled}
                        useWorkflowLlm={useWorkflowLlm}
                        setUseWorkflowLlm={setUseWorkflowLlm}
                        qaProvider={qaProvider}
                        setQaProvider={setQaProvider}
                        qaModel={qaModel}
                        setQaModel={setQaModel}
                        qaApiKey={qaApiKey}
                        setQaApiKey={setQaApiKey}
                        qaSystemPrompt={qaSystemPrompt}
                        setQaSystemPrompt={setQaSystemPrompt}
                        minCallDuration={minCallDuration}
                        setMinCallDuration={setMinCallDuration}
                        qaVoicemailCalls={qaVoicemailCalls}
                        setQaVoicemailCalls={setQaVoicemailCalls}
                        qaSampleRate={qaSampleRate}
                        setQaSampleRate={setQaSampleRate}
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
    useWorkflowLlm: boolean;
    setUseWorkflowLlm: (value: boolean) => void;
    qaProvider: string;
    setQaProvider: (value: string) => void;
    qaModel: string;
    setQaModel: (value: string) => void;
    qaApiKey: string;
    setQaApiKey: (value: string) => void;
    qaSystemPrompt: string;
    setQaSystemPrompt: (value: string) => void;
    minCallDuration: number;
    setMinCallDuration: (value: number) => void;
    qaVoicemailCalls: boolean;
    setQaVoicemailCalls: (value: boolean) => void;
    qaSampleRate: number;
    setQaSampleRate: (value: number) => void;
}

const QANodeEditForm = ({
    name,
    setName,
    qaEnabled,
    setQaEnabled,
    useWorkflowLlm,
    setUseWorkflowLlm,
    qaProvider,
    setQaProvider,
    qaModel,
    setQaModel,
    qaApiKey,
    setQaApiKey,
    qaSystemPrompt,
    setQaSystemPrompt,
    minCallDuration,
    setMinCallDuration,
    qaVoicemailCalls,
    setQaVoicemailCalls,
    qaSampleRate,
    setQaSampleRate,
}: QANodeEditFormProps) => {
    const [advancedOpen, setAdvancedOpen] = useState(false);

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

            <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                <Switch
                    id="use-workflow-llm"
                    checked={useWorkflowLlm}
                    onCheckedChange={setUseWorkflowLlm}
                />
                <Label htmlFor="use-workflow-llm">Use Workflow LLM</Label>
                <Label className="text-xs text-muted-foreground ml-2">
                    Use the LLM configured in your account settings.
                </Label>
            </div>

            {!useWorkflowLlm && (
                <LLMConfigSelector
                    provider={qaProvider}
                    onProviderChange={setQaProvider}
                    model={qaModel}
                    onModelChange={setQaModel}
                    apiKey={qaApiKey}
                    onApiKeyChange={setQaApiKey}
                />
            )}

            <div className="grid gap-2">
                <Label>System Prompt</Label>
                <Label className="text-xs text-muted-foreground">
                    The prompt sent to the LLM for per-node QA analysis. Available placeholders:{' '}
                    {'{{node_summary}}'} (purpose of the current node), {'{{previous_conversation_summary}}'}{' '}
                    (summary of conversation before this node), {'{{transcript}}'} (this node&apos;s
                    conversation), {'{{metrics}}'} (call metrics for this node).
                </Label>
                <Textarea
                    value={qaSystemPrompt}
                    onChange={(e) => setQaSystemPrompt(e.target.value)}
                    className="min-h-[300px] font-mono text-xs"
                    placeholder={`You are a QA analyst evaluating a specific segment of a voice AI conversation.\n\n## Node Purpose\n{{node_summary}}\n\n## Previous Conversation Context\n{{previous_conversation_summary}}\n\n## Call Metrics\n{{metrics}}\n\nEvaluate the transcript and return JSON with:\n- "tags": array of relevant tags\n- "summary": 2-3 sentence summary of this segment\n- "call_quality_score": number 1-10\n- "overall_sentiment": "positive", "neutral", or "negative"`}
                />
            </div>

            {/* Advanced Configuration */}
            <div className="border rounded-md">
                <button
                    type="button"
                    className="flex items-center gap-2 w-full p-3 text-sm font-medium hover:bg-muted/50 transition-colors"
                    onClick={() => setAdvancedOpen(!advancedOpen)}
                >
                    {advancedOpen ? (
                        <ChevronDown className="h-4 w-4" />
                    ) : (
                        <ChevronRight className="h-4 w-4" />
                    )}
                    Advanced Configuration
                </button>

                {advancedOpen && (
                    <div className="px-3 pb-3 space-y-4 border-t pt-3">
                        <div className="grid gap-2">
                            <Label>Minimum Call Duration (seconds)</Label>
                            <Label className="text-xs text-muted-foreground">
                                Calls shorter than this duration will be skipped from QA analysis.
                            </Label>
                            <Input
                                type="number"
                                min={0}
                                value={minCallDuration}
                                onChange={(e) => setMinCallDuration(Number(e.target.value))}
                            />
                        </div>

                        <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                            <Switch
                                id="qa-voicemail"
                                checked={qaVoicemailCalls}
                                onCheckedChange={setQaVoicemailCalls}
                            />
                            <Label htmlFor="qa-voicemail">QA Voicemail Calls</Label>
                            <Label className="text-xs text-muted-foreground ml-2">
                                Run QA analysis on calls that reached voicemail.
                            </Label>
                        </div>

                        <div className="grid gap-2">
                            <Label>Sample Rate (%)</Label>
                            <Label className="text-xs text-muted-foreground">
                                Percentage of eligible calls to run QA analysis on.
                            </Label>
                            <Input
                                type="number"
                                min={1}
                                max={100}
                                value={qaSampleRate}
                                onChange={(e) =>
                                    setQaSampleRate(
                                        Math.min(100, Math.max(1, Number(e.target.value)))
                                    )
                                }
                            />
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

QANode.displayName = "QANode";
