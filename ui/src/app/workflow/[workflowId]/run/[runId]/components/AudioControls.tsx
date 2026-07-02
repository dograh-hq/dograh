import { Loader2, Mic, Phone, PhoneOff } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

interface AudioControlsProps {
    audioInputs: MediaDeviceInfo[];
    selectedAudioInput: string;
    setSelectedAudioInput: (deviceId: string) => void;
    isCompleted: boolean;
    connectionActive: boolean;
    permissionError: string | null;
    start: () => Promise<void>;
    stop: () => void;
    isStarting: boolean;
    getAudioInputDevices: () => Promise<void>;
}

export const AudioControls = ({
    audioInputs,
    selectedAudioInput,
    setSelectedAudioInput,
    isCompleted,
    connectionActive,
    permissionError,
    start,
    stop,
    isStarting,
    getAudioInputDevices
}: AudioControlsProps) => {
    const [isRequestingPermission, setIsRequestingPermission] = useState(false);
    const [permissionDenied, setPermissionDenied] = useState(false);

    // Browsers only provide device labels after permission is granted
    const hasValidDevices = audioInputs.length > 0 && audioInputs.some(device => device.label && device.label.trim() !== '');

    const requestAudioPermissions = async () => {
        setIsRequestingPermission(true);

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach(track => track.stop());
            await getAudioInputDevices();
        } catch (error) {
            if (error instanceof Error && error.name === 'NotAllowedError') {
                setPermissionDenied(true);
            }
        } finally {
            setIsRequestingPermission(false);
        }
    };

    const handleTryAgain = () => {
        setPermissionDenied(false);
        requestAudioPermissions();
    };

    // Handle auto-selection of first device if none selected
    useEffect(() => {
        if (hasValidDevices && !selectedAudioInput) {
            const firstValidDevice = audioInputs.find(device => device.label && device.label.trim() !== '');
            if (firstValidDevice) {
                setSelectedAudioInput(firstValidDevice.deviceId);
            }
        }
    }, [hasValidDevices, selectedAudioInput, audioInputs, setSelectedAudioInput]);

    if (isCompleted) {
        return null; // The parent component will handle showing the loading state
    }

    if (!hasValidDevices) {
        // Show permission denied UI
        if (permissionDenied) {
            return (
                <div className="flex flex-col items-center justify-center space-y-4 p-8">
                    <div className="h-12 w-12 bg-destructive/10 rounded-full flex items-center justify-center">
                        <Mic className="h-6 w-6 text-destructive" />
                    </div>
                    <div className="text-center space-y-2">
                        <p className="text-foreground font-medium">Microphone access denied</p>
                        <p className="text-sm text-muted-foreground max-w-md">
                            To use the voice agent, you need to allow microphone access.
                            Please enable it in your browser settings and try again.
                        </p>
                    </div>
                    <Button
                        onClick={handleTryAgain}
                        size="lg"
                        disabled={isRequestingPermission}
                    >
                        {isRequestingPermission ? (
                            <>
                                <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                                Waiting for permission...
                            </>
                        ) : (
                            <>
                                <Mic className="h-5 w-5 mr-2" />
                                Try Again
                            </>
                        )}
                    </Button>
                </div>
            );
        }

        // Show initial permission request UI
        return (
            <div className="flex flex-col items-center justify-center space-y-4 p-8">
                <div className="text-center space-y-2">
                    <p className="text-foreground font-medium">Audio permissions required</p>
                    <p className="text-sm text-muted-foreground">
                        {isRequestingPermission
                            ? "Please allow microphone access in the browser dialog"
                            : "Click below to grant microphone access"}
                    </p>
                </div>
                <Button
                    onClick={requestAudioPermissions}
                    size="lg"
                    disabled={isRequestingPermission}
                >
                    {isRequestingPermission ? (
                        <>
                            <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                            Waiting for permission...
                        </>
                    ) : (
                        <>
                            <Mic className="h-5 w-5 mr-2" />
                            Grant Audio Permissions
                        </>
                    )}
                </Button>
            </div>
        );
    }

    return (
        <div className="flex flex-col items-center justify-center space-y-6 p-8">
            {!connectionActive ? (
                <>
                    <button
                        onClick={start}
                        disabled={isStarting}
                        className="group relative h-20 w-20 rounded-full bg-emerald-600 hover:bg-emerald-700 transition-all duration-200 shadow-lg hover:shadow-xl disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                        aria-label="Start Call"
                    >
                        <div className="absolute inset-0 rounded-full bg-emerald-600 animate-ping opacity-25"></div>
                        <div className="relative flex items-center justify-center h-full">
                            <Phone className="h-8 w-8 text-white" />
                        </div>
                    </button>
                    <p className="text-sm font-medium text-foreground">Start Call</p>
                </>
            ) : (
                <>
                    <p className="text-sm text-muted-foreground">Call in progress</p>
                    <button
                        onClick={stop}
                        className="group relative h-20 w-20 rounded-full bg-destructive hover:bg-destructive/90 transition-all duration-200 shadow-lg hover:shadow-xl"
                        aria-label="End Call"
                    >
                        <div className="relative flex items-center justify-center h-full">
                            <PhoneOff className="h-8 w-8 text-destructive-foreground" />
                        </div>
                    </button>
                    <p className="text-sm font-medium text-foreground">End Call</p>
                </>
            )}
            {permissionError && (
                <p className="text-sm text-destructive text-center">{permissionError}</p>
            )}
        </div>
    );
};
