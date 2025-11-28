import { Mic, Phone, PhoneOff } from "lucide-react";
import { useEffect } from "react";

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
    // Check if we have valid audio devices (permissions granted)
    // Browsers only provide device labels after permission is granted
    const hasValidDevices = audioInputs.length > 0 && audioInputs.some(device => device.label && device.label.trim() !== '');

    const requestAudioPermissions = async () => {
        try {
            // Request audio permissions - this triggers the browser permission prompt
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            // Stop the stream immediately - we just needed to trigger the permission prompt
            stream.getTracks().forEach(track => track.stop());
            // Refresh the device list now that we have permissions
            await getAudioInputDevices();
        } catch (error) {
            console.error('Failed to request audio permissions:', error);
        }
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
        return (
            <div className="flex flex-col items-center justify-center space-y-4 p-8">
                <div className="text-center space-y-2">
                    <p className="text-foreground font-medium">Audio permissions required</p>
                    <p className="text-sm text-muted-foreground">Click below to grant microphone access</p>
                </div>
                <Button
                    onClick={requestAudioPermissions}
                    size="lg"
                >
                    <Mic className="h-5 w-5 mr-2" />
                    Grant Audio Permissions
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
