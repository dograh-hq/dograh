import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet } from "@/client/sdk.gen";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";

import {
    ApiKeyErrorDialog,
    AudioControls,
    ConnectionStatus,
    RealtimeFeedback,
    WorkflowConfigErrorDialog
} from "./components";
import { useWebSocketRTC } from "./hooks";

const BrowserCall = ({ workflowId, workflowRunId, initialContextVariables }: {
    workflowId: number,
    workflowRunId: number,
    initialContextVariables?: Record<string, string> | null
}) => {
    const router = useRouter();
    const auth = useAuth();
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [checkingForRecording, setCheckingForRecording] = useState(false);

    // Get access token for WebSocket connection (non-SDK usage)
    useEffect(() => {
        if (auth.isAuthenticated && !auth.loading) {
            auth.getAccessToken().then(setAccessToken);
        }
    }, [auth]);

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
        apiKeyErrorCode,
        workflowConfigError,
        workflowConfigModalOpen,
        setWorkflowConfigModalOpen,
        connectionStatus,
        start,
        stop,
        isStarting,
        getAudioInputDevices,
        feedbackMessages,
    } = useWebSocketRTC({ workflowId, workflowRunId, accessToken, initialContextVariables });

    // Poll for recording availability after call ends
    useEffect(() => {
        if (!isCompleted || !auth.isAuthenticated) return;

        setCheckingForRecording(true);
        const intervalId = setInterval(async () => {
            try {
                const response = await getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet({
                    path: {
                        workflow_id: workflowId,
                        run_id: workflowRunId,
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
    }, [isCompleted, auth.isAuthenticated, workflowId, workflowRunId]);

    const navigateToCredits = () => {
        router.push('/api-keys');
    };

    const navigateToModelConfig = () => {
        router.push('/model-configurations');
    };

    const navigateToWorkflow = () => {
        router.push(`/workflow/${workflowId}`)
    }

    return (
        <>
            <div className="flex h-screen w-full overflow-hidden">
                {/* Main content - 2/3 width when panel visible, full width otherwise */}
                <div className="w-2/3 h-full overflow-y-auto">
                    <div className="flex justify-center items-center h-full px-8">
                        <Card className="w-full max-w-xl">
                            <CardHeader>
                                <CardTitle>Call Voice Agent</CardTitle>
                            </CardHeader>

                            <CardContent>
                                {isCompleted && checkingForRecording ? (
                                    <div className="flex flex-col items-center justify-center space-y-4 p-8">
                                        <Loader2 className="h-8 w-8 animate-spin text-primary" />
                                        <div className="text-center space-y-2">
                                            <p className="text-foreground font-medium">Processing your call</p>
                                            <p className="text-sm text-muted-foreground">Fetching transcript and recording...</p>
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
                    </div>
                </div>

                {/* Show transcript panel */}
                <div className="w-1/3 h-full shrink-0 overflow-hidden">
                    <RealtimeFeedback
                        mode="live"
                        messages={feedbackMessages}
                        isCallActive={connectionActive}
                        isCallCompleted={isCompleted}
                    />
                </div>
            </div>

            <ApiKeyErrorDialog
                open={apiKeyModalOpen}
                onOpenChange={setApiKeyModalOpen}
                error={apiKeyError}
                errorCode={apiKeyErrorCode}
                onNavigateToCredits={navigateToCredits}
                onNavigateToModelConfig={navigateToModelConfig}
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
