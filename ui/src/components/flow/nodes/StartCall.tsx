import { NodeProps, NodeToolbar, Position } from "@xyflow/react";
import { Edit, Play } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { FlowNodeData } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { isOSSMode } from "@/lib/utils";

import { NodeContent } from "./common/NodeContent";
import { NodeEditDialog } from "./common/NodeEditDialog";
import { useNodeHandlers } from "./common/useNodeHandlers";

interface StartCallEditFormProps {
    nodeData: FlowNodeData;
    prompt: string;
    setPrompt: (value: string) => void;
    isStatic: boolean;
    setIsStatic: (value: boolean) => void;
    name: string;
    setName: (value: string) => void;
    allowInterrupt: boolean;
    setAllowInterrupt: (value: boolean) => void;
    addGlobalPrompt: boolean;
    setAddGlobalPrompt: (value: boolean) => void;
    waitForUserResponse: boolean;
    setWaitForUserResponse: (value: boolean) => void;
    detectVoicemail: boolean;
    setDetectVoicemail: (value: boolean) => void;
    delayedStart: boolean;
    setDelayedStart: (value: boolean) => void;
    delayedStartDuration: number;
    setDelayedStartDuration: (value: number) => void;
}

interface StartCallNodeProps extends NodeProps {
    data: FlowNodeData;
}

export const StartCall = memo(({ data, selected, id }: StartCallNodeProps) => {
    const { open, setOpen, handleSaveNodeData } = useNodeHandlers({
        id,
        additionalData: { is_start: true }
    });
    const { saveWorkflow } = useWorkflow();

    // Form state
    const [prompt, setPrompt] = useState(data.prompt ?? "");
    const [isStatic, setIsStatic] = useState(data.is_static ?? true);
    const [name, setName] = useState(data.name);
    const [allowInterrupt, setAllowInterrupt] = useState(data.allow_interrupt ?? true);
    const [addGlobalPrompt, setAddGlobalPrompt] = useState(data.add_global_prompt ?? true);
    const [waitForUserResponse, setWaitForUserResponse] = useState(data.wait_for_user_response ?? false);
    const [detectVoicemail, setDetectVoicemail] = useState(data.detect_voicemail ?? true);
    const [delayedStart, setDelayedStart] = useState(data.delayed_start ?? false);
    const [delayedStartDuration, setDelayedStartDuration] = useState(data.delayed_start_duration ?? 2);

    const handleSave = async () => {
        handleSaveNodeData({
            ...data,
            prompt,
            is_static: isStatic,
            name,
            allow_interrupt: allowInterrupt,
            add_global_prompt: addGlobalPrompt,
            wait_for_user_response: waitForUserResponse,
            detect_voicemail: detectVoicemail,
            delayed_start: delayedStart,
            delayed_start_duration: delayedStart ? delayedStartDuration : undefined
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
            setIsStatic(data.is_static ?? true);
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setWaitForUserResponse(data.wait_for_user_response ?? false);
            setDetectVoicemail(data.detect_voicemail ?? true);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
        }
        setOpen(newOpen);
    };

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setPrompt(data.prompt ?? "");
            setIsStatic(data.is_static ?? true);
            setName(data.name);
            setAllowInterrupt(data.allow_interrupt ?? true);
            setAddGlobalPrompt(data.add_global_prompt ?? true);
            setWaitForUserResponse(data.wait_for_user_response ?? false);
            setDetectVoicemail(data.detect_voicemail ?? true);
            setDelayedStart(data.delayed_start ?? false);
            setDelayedStartDuration(data.delayed_start_duration ?? 3);
        }
    }, [data, open]);

    return (
        <>
            <NodeContent
                selected={selected}
                invalid={data.invalid}
                selected_through_edge={data.selected_through_edge}
                hovered_through_edge={data.hovered_through_edge}
                title="Start Call"
                icon={<Play />}
                bgColor="bg-green-300"
                hasSourceHandle={true}
                onDoubleClick={() => setOpen(true)}
                nodeId={id}
            >
                <div className="text-sm text-muted-foreground">
                    {data.prompt?.length > 30 ? `${data.prompt.substring(0, 30)}...` : data.prompt}
                </div>
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
            >
                {open && (
                    <StartCallEditForm
                        nodeData={data}
                        prompt={prompt}
                        setPrompt={setPrompt}
                        isStatic={isStatic}
                        setIsStatic={setIsStatic}
                        name={name}
                        setName={setName}
                        allowInterrupt={allowInterrupt}
                        setAllowInterrupt={setAllowInterrupt}
                        addGlobalPrompt={addGlobalPrompt}
                        setAddGlobalPrompt={setAddGlobalPrompt}
                        waitForUserResponse={waitForUserResponse}
                        setWaitForUserResponse={setWaitForUserResponse}
                        detectVoicemail={detectVoicemail}
                        setDetectVoicemail={setDetectVoicemail}
                        delayedStart={delayedStart}
                        setDelayedStart={setDelayedStart}
                        delayedStartDuration={delayedStartDuration}
                        setDelayedStartDuration={setDelayedStartDuration}
                    />
                )}
            </NodeEditDialog>
        </>
    );
});

const StartCallEditForm = ({
    prompt,
    setPrompt,
    isStatic,
    setIsStatic,
    name,
    setName,
    allowInterrupt,
    setAllowInterrupt,
    addGlobalPrompt,
    setAddGlobalPrompt,
    waitForUserResponse,
    setWaitForUserResponse,
    detectVoicemail,
    setDetectVoicemail,
    delayedStart,
    setDelayedStart,
    delayedStartDuration,
    setDelayedStartDuration
}: StartCallEditFormProps) => {
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

            <Label>{isStatic ? "Text" : "Prompt"}</Label>
            <Label className="text-xs text-gray-500">
                What would you like the agent to say when the call starts? Its a good idea to have a static greeting that can be used to identify the call.
            </Label>
            <div className="flex items-center space-x-2">
                <Switch id="static-text" checked={isStatic} onCheckedChange={setIsStatic} />
                <Label htmlFor="static-text">Static Text</Label>
            </div>
            <Textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className="min-h-[100px] max-h-[300px] resize-none"
                style={{
                    overflowY: 'auto'
                }}
                placeholder={isStatic ? "Hello, welcome to Dograh. How can I help you today?" : "Enter a dynamic prompt"}
            />
            <div className="flex items-center space-x-2">
                <Switch id="allow-interrupt" checked={allowInterrupt} onCheckedChange={setAllowInterrupt} />
                <Label htmlFor="allow-interrupt">Allow Interruption</Label>
                <Label className="text-xs text-gray-500">
                    Whether you would like user to be able to interrupt the bot.
                </Label>
            </div>
            <div className="flex items-center space-x-2">
                <Switch
                    id="add-global-prompt"
                    checked={addGlobalPrompt}
                    onCheckedChange={setAddGlobalPrompt}
                    disabled={isStatic}
                />
                <Label htmlFor="add-global-prompt" className={isStatic ? "opacity-50" : ""}>
                    Add Global Prompt
                </Label>
                <Label className={`text-xs text-gray-500 ${isStatic ? "opacity-50" : ""}`}>
                    {isStatic
                        ? "Not applicable for static text"
                        : "Whether you want to add global prompt with this node's prompt."}
                </Label>
            </div>
            <div className="flex flex-col space-y-2">
                <div className="flex items-center space-x-2">
                    <Switch
                        id="wait-for-user-response"
                        checked={waitForUserResponse}
                        onCheckedChange={setWaitForUserResponse}
                        disabled={!isStatic}
                    />
                    <Label htmlFor="wait-for-user-response" className={!isStatic ? "opacity-50" : ""}>
                        Wait for user&apos;s response
                    </Label>
                    <Label className={`text-xs text-gray-500 ${!isStatic ? "opacity-50" : ""}`}>
                        {!isStatic
                            ? "Only applicable for static text"
                            : "Wait for user to respond before disconnecting the call."}
                    </Label>
                </div>
            </div>
            {!isOSSMode() && (
                <div className="flex items-center space-x-2">
                    <Switch
                        id="detect-voicemail"
                        checked={detectVoicemail}
                        onCheckedChange={setDetectVoicemail}
                    />
                    <Label htmlFor="detect-voicemail">
                        Detect Voicemail
                    </Label>
                    <Label className="text-xs text-gray-500">
                        Automatically detect and end call if voicemail is reached.
                    </Label>
                </div>
            )}
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
                    <Label className="text-xs text-gray-500">
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
        </div>
    );
};

StartCall.displayName = "StartCall";
