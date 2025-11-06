import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { AmbientNoiseConfiguration, VADConfiguration, WorkflowConfigurations } from "@/types/workflow-configurations";

interface ConfigurationsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowConfigurations: WorkflowConfigurations | null;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}

const DEFAULT_VAD_CONFIG: VADConfiguration = {
    confidence: 0.7,
    start_seconds: 0.4,
    stop_seconds: 0.8,
    minimum_volume: 0.6,
};

const DEFAULT_AMBIENT_NOISE_CONFIG: AmbientNoiseConfiguration = {
    enabled: false,
    volume: 0.3,
};

export const ConfigurationsDialog = ({
    open,
    onOpenChange,
    workflowConfigurations,
    workflowName,
    onSave
}: ConfigurationsDialogProps) => {
    const [name, setName] = useState<string>(workflowName);
    const [vadConfig, setVadConfig] = useState<VADConfiguration>(
        workflowConfigurations?.vad_configuration || DEFAULT_VAD_CONFIG
    );
    const [ambientNoiseConfig, setAmbientNoiseConfig] = useState<AmbientNoiseConfiguration>(
        workflowConfigurations?.ambient_noise_configuration || DEFAULT_AMBIENT_NOISE_CONFIG
    );
    const [maxCallDuration, setMaxCallDuration] = useState<number>(
        workflowConfigurations?.max_call_duration || 600  // Default 10 minutes
    );
    const [maxUserIdleTimeout, setMaxUserIdleTimeout] = useState<number>(
        workflowConfigurations?.max_user_idle_timeout || 10  // Default 10 seconds
    );
    const [isSaving, setIsSaving] = useState(false);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave({
                vad_configuration: vadConfig,
                ambient_noise_configuration: ambientNoiseConfig,
                max_call_duration: maxCallDuration,
                max_user_idle_timeout: maxUserIdleTimeout
            }, name);
            onOpenChange(false);
        } catch (error) {
            console.error("Failed to save configurations:", error);
        } finally {
            setIsSaving(false);
        }
    };

    // Sync state with props when dialog opens
    useEffect(() => {
        if (open) {
            setName(workflowName);
            setVadConfig(workflowConfigurations?.vad_configuration || DEFAULT_VAD_CONFIG);
            setAmbientNoiseConfig(workflowConfigurations?.ambient_noise_configuration || DEFAULT_AMBIENT_NOISE_CONFIG);
            setMaxCallDuration(workflowConfigurations?.max_call_duration || 600);
            setMaxUserIdleTimeout(workflowConfigurations?.max_user_idle_timeout || 10);
        }
    }, [open, workflowName, workflowConfigurations]);

    const handleVadChange = (field: keyof VADConfiguration, value: string) => {
        const numValue = parseFloat(value);
        if (!isNaN(numValue)) {
            setVadConfig(prev => ({
                ...prev,
                [field]: numValue
            }));
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Configurations</DialogTitle>
                </DialogHeader>

                <div className="space-y-6">
                    {/* Workflow Name Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Workflow Name</h3>
                            <p className="text-xs text-gray-500">
                                The name of your workflow
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="workflow_name" className="text-xs">
                                Name
                            </Label>
                            <Input
                                id="workflow_name"
                                type="text"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="Enter workflow name"
                            />
                        </div>
                    </div>

                    {/* Voice Activity Detection Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Voice Activity Detection</h3>
                            <p className="text-xs text-gray-500">
                                Hyperparameters to set for voice activity detection. Already configured with defaults.
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="confidence" className="text-xs">
                                    Confidence
                                </Label>
                                <Input
                                    id="confidence"
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="1"
                                    value={vadConfig.confidence}
                                    onChange={(e) => handleVadChange('confidence', e.target.value)}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="start_seconds" className="text-xs">
                                    Start Seconds
                                </Label>
                                <Input
                                    id="start_seconds"
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    value={vadConfig.start_seconds}
                                    onChange={(e) => handleVadChange('start_seconds', e.target.value)}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="stop_seconds" className="text-xs">
                                    Stop Seconds
                                </Label>
                                <Input
                                    id="stop_seconds"
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    value={vadConfig.stop_seconds}
                                    onChange={(e) => handleVadChange('stop_seconds', e.target.value)}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="minimum_volume" className="text-xs">
                                    Minimum Volume
                                </Label>
                                <Input
                                    id="minimum_volume"
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="1"
                                    value={vadConfig.minimum_volume}
                                    onChange={(e) => handleVadChange('minimum_volume', e.target.value)}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Ambient Noise Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Ambient Noise</h3>
                            <p className="text-xs text-gray-500">
                                Add background office ambient noise to make the conversation sound more natural.
                            </p>
                        </div>

                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <Label htmlFor="ambient-noise-enabled" className="text-sm">
                                    Use Ambient Noise
                                </Label>
                                <Switch
                                    id="ambient-noise-enabled"
                                    checked={ambientNoiseConfig.enabled}
                                    onCheckedChange={(checked) =>
                                        setAmbientNoiseConfig(prev => ({ ...prev, enabled: checked }))
                                    }
                                />
                            </div>

                            {ambientNoiseConfig.enabled && (
                                <div className="space-y-2">
                                    <Label htmlFor="ambient-volume" className="text-xs">
                                        Volume
                                    </Label>
                                    <Input
                                        id="ambient-volume"
                                        type="number"
                                        step="0.1"
                                        min="0"
                                        max="1"
                                        value={ambientNoiseConfig.volume}
                                        onChange={(e) => {
                                            const value = parseFloat(e.target.value);
                                            if (!isNaN(value)) {
                                                setAmbientNoiseConfig(prev => ({ ...prev, volume: value }));
                                            }
                                        }}
                                    />
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Call Management Section */}
                    <div className="space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold mb-1">Call Management</h3>
                            <p className="text-xs text-gray-500">
                                Configure call duration limits and idle timeout settings.
                            </p>
                        </div>

                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="max_call_duration" className="text-xs">
                                    Max Call Duration (seconds)
                                </Label>
                                <Input
                                    id="max_call_duration"
                                    type="number"
                                    step="1"
                                    min="1"
                                    value={maxCallDuration}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        if (!isNaN(value) && value > 0) {
                                            setMaxCallDuration(value);
                                        }
                                    }}
                                />
                                <p className="text-xs text-gray-500">Default: 600 (10 minutes)</p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="max_user_idle_timeout" className="text-xs">
                                    Max User Idle Timeout (seconds)
                                </Label>
                                <Input
                                    id="max_user_idle_timeout"
                                    type="number"
                                    step="1"
                                    min="1"
                                    value={maxUserIdleTimeout}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value);
                                        if (!isNaN(value) && value > 0) {
                                            setMaxUserIdleTimeout(value);
                                        }
                                    }}
                                />
                                <p className="text-xs text-gray-500">Default: 10 seconds</p>
                            </div>
                        </div>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={isSaving}>
                        {isSaving ? "Saving..." : "Save"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

