import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet } from "@/client/sdk.gen";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import {
    ApiKeyErrorDialog,
    AudioControls,
    ConnectionStatus,
    WorkflowConfigErrorDialog
} from "./components";
import { useWebSocketRTC } from "./hooks";

const BrowserCall = ({ workflowId, workflowRunId, accessToken, initialContextVariables }: {
    workflowId: number,
    workflowRunId: number,
    accessToken: string | null,
    initialContextVariables?: Record<string, string> | null
}) => {
    const router = useRouter();
    const [checkingForRecording, setCheckingForRecording] = useState(false);

    const {
        audioRef,
        audioInputs,
        selectedAudioInput,
        setSelectedAudioInput,
        connectionActive,
        permissionError,
        isCompleted,
        apiKeyModalOpen,
        setApiKeyModalOpen,
        apiKeyError,
        workflowConfigError,
        workflowConfigModalOpen,
        setWorkflowConfigModalOpen,
        connectionStatus,
        start,
        stop,
        isStarting,
        getAudioInputDevices
    } = useWebSocketRTC({ workflowId, workflowRunId, accessToken, initialContextVariables });

    // Poll for recording availability after call ends
    useEffect(() => {
        if (!isCompleted || !accessToken) return;

        setCheckingForRecording(true);
        const intervalId = setInterval(async () => {
            try {
                const response = await getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet({
                    path: {
                        workflow_id: workflowId,
                        run_id: workflowRunId,
                    },
                    headers: {
                        'Authorization': `Bearer ${accessToken}`,
                    },
                });

                if (response.data?.transcript_url || response.data?.recording_url) {
                    setCheckingForRecording(false);
                    clearInterval(intervalId);
                    // Refresh the page to show the recording
                    window.location.reload();
                }
            } catch (error) {
                console.error('Error checking for recording:', error);
            }
        }, 5000); // Check every 5 seconds

        // Clean up after 2 minutes
        const timeoutId = setTimeout(() => {
            clearInterval(intervalId);
            setCheckingForRecording(false);
        }, 120000);

        return () => {
            clearInterval(intervalId);
            clearTimeout(timeoutId);
        };
    }, [isCompleted, accessToken, workflowId, workflowRunId]);

    const navigateToApiKeys = () => {
        router.push('/api-keys');
    };

    const navigateToWorkflow = () => {
        router.push(`/workflow/${workflowId}`)
    }

    return (
        <>
            <Card className="w-full max-w-4xl mx-auto">
                <CardHeader>
                    <CardTitle>Call Voice Agent</CardTitle>
                </CardHeader>

                <CardContent>
                    {isCompleted && checkingForRecording ? (
                        <div className="flex flex-col items-center justify-center space-y-4 p-8">
                            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
                            <div className="text-center space-y-2">
                                <p className="text-gray-700 font-medium">Processing your call</p>
                                <p className="text-sm text-gray-500">Fetching transcript and recording...</p>
                            </div>
                        </div>
                    ) : (
                        <>
                            <AudioControls
                                audioInputs={audioInputs}
                                selectedAudioInput={selectedAudioInput}
                                setSelectedAudioInput={setSelectedAudioInput}
                                isCompleted={isCompleted}
                                connectionActive={connectionActive}
                                permissionError={permissionError}
                                start={start}
                                stop={stop}
                                isStarting={isStarting}
                                getAudioInputDevices={getAudioInputDevices}
                            />

                            <ConnectionStatus
                                connectionStatus={connectionStatus}
                            />
                        </>
                    )}
                </CardContent>

                <audio ref={audioRef} autoPlay playsInline className="hidden" />
            </Card>

            <ApiKeyErrorDialog
                open={apiKeyModalOpen}
                onOpenChange={setApiKeyModalOpen}
                error={apiKeyError}
                onNavigateToApiKeys={navigateToApiKeys}
            />

            <WorkflowConfigErrorDialog
                open={workflowConfigModalOpen}
                onOpenChange={setWorkflowConfigModalOpen}
                error={workflowConfigError}
                onNavigateToWorkflow={navigateToWorkflow}
            />
        </>
    );
};

export default BrowserCall;
