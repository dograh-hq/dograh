import { useEffect, useState } from "react";

import { LLMConfigSelector } from "@/components/LLMConfigSelector";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
    DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
    type VoicemailDetectionConfiguration,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

import { AnswerSupervisorFields, isVoicemailMessageMissing, readAnswerSupervisorSettings } from "./AnswerSupervisorFields";

interface VoicemailDetectionDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowConfigurations: WorkflowConfigurations;
    onSave: (configurations: WorkflowConfigurations) => void;
}

export const VoicemailDetectionDialog = ({
    open,
    onOpenChange,
    workflowConfigurations,
    onSave,
}: VoicemailDetectionDialogProps) => {
    const getConfig = (): VoicemailDetectionConfiguration => ({
        ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
        ...workflowConfigurations.voicemail_detection,
    });

    const [enabled, setEnabled] = useState(getConfig().enabled);
    const [useWorkflowLlm, setUseWorkflowLlm] = useState(getConfig().use_workflow_llm);
    const [provider, setProvider] = useState(getConfig().provider || "openai");
    const [model, setModel] = useState(getConfig().model || "gpt-4.1");
    const [apiKey, setApiKey] = useState(getConfig().api_key || "");
    const [answerSettings, setAnswerSettings] = useState(readAnswerSupervisorSettings(getConfig()));

    // Sync state from props whenever the dialog opens
    useEffect(() => {
        if (open) {
            const config = {
                ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
                ...workflowConfigurations.voicemail_detection,
            };
            setEnabled(config.enabled);
            setUseWorkflowLlm(config.use_workflow_llm);
            setProvider(config.provider || "openai");
            setModel(config.model || "gpt-4.1");
            setApiKey(config.api_key || "");
            setAnswerSettings(readAnswerSupervisorSettings(config));
        }
    }, [open, workflowConfigurations]);

    const handleOpenChange = (newOpen: boolean) => {
        onOpenChange(newOpen);
    };

    const handleSave = () => {
        const voicemailConfig: VoicemailDetectionConfiguration = {
            ...answerSettings,
            enabled,
            use_workflow_llm: useWorkflowLlm,
            provider: useWorkflowLlm ? undefined : provider,
            model: useWorkflowLlm ? undefined : model,
            api_key: useWorkflowLlm ? undefined : apiKey,
        };

        onSave({
            ...workflowConfigurations,
            voicemail_detection: voicemailConfig,
        });
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Voicemail & Screening</DialogTitle>
                    <DialogDescription>
                        Choose how the agent handles voicemail and call screening. Applies to outbound calls with separate speech and language models.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                        <Switch
                            id="voicemail-enabled"
                            checked={enabled}
                            onCheckedChange={setEnabled}
                        />
                        <Label htmlFor="voicemail-enabled">Enable voicemail and screening handling</Label>
                    </div>

                    {enabled && (
                        <>
                            <AnswerSupervisorFields value={answerSettings} onChange={setAnswerSettings} />
                            <details className="rounded-md border p-3">
                                <summary className="cursor-pointer text-sm font-medium">Classification model</summary>
                                <div className="mt-3 space-y-3">
                                    <div className="flex items-center space-x-2 p-2 border rounded-md bg-muted/20">
                                        <Switch
                                            id="voicemail-use-workflow-llm"
                                            checked={useWorkflowLlm}
                                            onCheckedChange={setUseWorkflowLlm}
                                        />
                                        <Label htmlFor="voicemail-use-workflow-llm">Use Workflow LLM</Label>
                                        <Label className="text-xs text-muted-foreground ml-2">
                                            Use the LLM configured in your account settings.
                                        </Label>
                                    </div>

                                    {!useWorkflowLlm && (
                                        <LLMConfigSelector
                                            provider={provider}
                                            onProviderChange={setProvider}
                                            model={model}
                                            onModelChange={setModel}
                                            apiKey={apiKey}
                                            onApiKeyChange={setApiKey}
                                        />
                                    )}
                                </div>
                            </details>
                        </>
                    )}
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={enabled && isVoicemailMessageMissing(answerSettings)}>Save</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
