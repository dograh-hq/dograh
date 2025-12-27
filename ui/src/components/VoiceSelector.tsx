"use client";

import { ChevronDown, Loader2, Search, Volume2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { getVoicesApiV1UserConfigurationsVoicesProviderGet } from "@/client/sdk.gen";
import { VoiceInfo } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useUserConfig } from "@/context/UserConfigContext";
import { cn } from "@/lib/utils";

// Providers that have MPS voice endpoints
type TTSProviderWithVoices = "elevenlabs" | "deepgram" | "sarvam" | "cartesia" | "dograh";
const MPS_VOICE_PROVIDERS: TTSProviderWithVoices[] = ["elevenlabs", "deepgram", "sarvam", "cartesia", "dograh"];

interface VoiceSelectorProps {
    provider: string;
    value: string;
    onChange: (voiceId: string) => void;
    className?: string;
}

export const VoiceSelector: React.FC<VoiceSelectorProps> = ({
    provider,
    value,
    onChange,
    className,
}) => {
    const { accessToken } = useUserConfig();
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const [isManualInput, setIsManualInput] = useState(false);
    const [manualVoiceId, setManualVoiceId] = useState(value || "");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [playingPreview, setPlayingPreview] = useState<string | null>(null);
    const [currentAudio, setCurrentAudio] = useState<HTMLAudioElement | null>(null);

    // Check if provider has MPS voice endpoint
    const hasMPSVoiceEndpoint = useCallback((providerName: string): boolean => {
        return MPS_VOICE_PROVIDERS.includes(providerName.toLowerCase() as TTSProviderWithVoices);
    }, []);

    // Map provider names to API-compatible provider names
    const getProviderKey = useCallback((providerName: string): TTSProviderWithVoices | null => {
        const providerMap: Record<string, TTSProviderWithVoices> = {
            elevenlabs: "elevenlabs",
            deepgram: "deepgram",
            sarvam: "sarvam",
            cartesia: "cartesia",
            dograh: "dograh",
        };
        return providerMap[providerName.toLowerCase()] || null;
    }, []);

    const fetchVoices = useCallback(async () => {
        const providerKey = getProviderKey(provider);
        if (!providerKey || !accessToken) {
            setVoices([]);
            return;
        }

        setIsLoading(true);
        setError(null);

        try {
            const response = await getVoicesApiV1UserConfigurationsVoicesProviderGet({
                path: { provider: providerKey },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data?.voices) {
                setVoices(response.data.voices);
            }
        } catch (err) {
            console.error("Failed to fetch voices:", err);
            setError("Failed to load voices");
            setVoices([]);
        } finally {
            setIsLoading(false);
        }
    }, [provider, getProviderKey, accessToken]);

    useEffect(() => {
        if (provider) {
            fetchVoices();
        }
    }, [provider, fetchVoices]);

    // Check if the current value exists in the voices list
    useEffect(() => {
        if (value && voices.length > 0) {
            const voiceExists = voices.some((v) => v.voice_id === value);
            if (!voiceExists) {
                // If the value doesn't exist in the list, switch to manual input mode
                setIsManualInput(true);
                setManualVoiceId(value);
            }
        }
    }, [value, voices]);

    // Cleanup audio on unmount or when popover closes
    useEffect(() => {
        if (!isOpen && currentAudio) {
            currentAudio.pause();
            currentAudio.currentTime = 0;
            setCurrentAudio(null);
            setPlayingPreview(null);
        }
    }, [isOpen, currentAudio]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (currentAudio) {
                currentAudio.pause();
            }
        };
    }, [currentAudio]);

    const filteredVoices = voices.filter((voice) => {
        const searchLower = searchTerm.toLowerCase();
        return (
            voice.name.toLowerCase().includes(searchLower) ||
            voice.voice_id.toLowerCase().includes(searchLower) ||
            (voice.description?.toLowerCase() || "").includes(searchLower) ||
            (voice.accent?.toLowerCase() || "").includes(searchLower) ||
            (voice.gender?.toLowerCase() || "").includes(searchLower) ||
            (voice.language?.toLowerCase() || "").includes(searchLower)
        );
    });

    const handleSelectVoice = (voiceId: string) => {
        onChange(voiceId);
        setIsOpen(false);
        setSearchTerm("");
    };

    const handleManualInputToggle = (checked: boolean) => {
        setIsManualInput(checked);
        if (checked) {
            setManualVoiceId(value || "");
        } else {
            // When switching back to dropdown, try to find the current value in voices
            const existingVoice = voices.find((v) => v.voice_id === value);
            if (!existingVoice && voices.length > 0) {
                // If current value not in list, select the first voice
                onChange(voices[0].voice_id);
            }
        }
    };

    const handleManualVoiceIdChange = (newValue: string) => {
        setManualVoiceId(newValue);
        onChange(newValue);
    };

    const getSelectedVoiceName = () => {
        if (isManualInput && value) {
            return value;
        }
        const voice = voices.find((v) => v.voice_id === value);
        return voice?.name || value || "Select a voice";
    };

    const playPreview = (previewUrl: string, voiceId: string) => {
        // Stop current audio if playing
        if (currentAudio) {
            currentAudio.pause();
            currentAudio.currentTime = 0;
            setCurrentAudio(null);
        }

        // If clicking the same voice that's playing, just stop it
        if (playingPreview === voiceId) {
            setPlayingPreview(null);
            return;
        }

        setPlayingPreview(voiceId);
        const audio = new Audio(previewUrl);
        setCurrentAudio(audio);
        audio.onended = () => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        };
        audio.onerror = () => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        };
        audio.play().catch(() => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        });
    };

    // For providers without MPS voice endpoint, show simple input
    if (!hasMPSVoiceEndpoint(provider)) {
        return (
            <div className={cn("space-y-2", className)}>
                <Input
                    type="text"
                    placeholder="Enter voice ID"
                    value={value || ""}
                    onChange={(e) => onChange(e.target.value)}
                />
            </div>
        );
    }

    if (isManualInput) {
        return (
            <div className={cn("space-y-2", className)}>
                <Input
                    type="text"
                    placeholder="Enter voice ID"
                    value={manualVoiceId}
                    onChange={(e) => handleManualVoiceIdChange(e.target.value)}
                />
                <div className="flex items-center space-x-2">
                    <Checkbox
                        id="manual-voice-input"
                        checked={isManualInput}
                        onCheckedChange={(checked) => handleManualInputToggle(checked as boolean)}
                    />
                    <Label
                        htmlFor="manual-voice-input"
                        className="text-sm font-normal cursor-pointer"
                    >
                        Add Voice ID Manually
                    </Label>
                </div>
            </div>
        );
    }

    return (
        <div className={cn("space-y-2", className)}>
            <Popover open={isOpen} onOpenChange={setIsOpen}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={isOpen}
                        className={cn(
                            "w-full justify-between",
                            !value && "text-muted-foreground"
                        )}
                        disabled={isLoading}
                    >
                        <span className="truncate">
                            {isLoading ? "Loading voices..." : getSelectedVoiceName()}
                        </span>
                        {isLoading ? (
                            <Loader2 className="ml-2 h-4 w-4 shrink-0 animate-spin" />
                        ) : (
                            <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                        )}
                    </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[400px] p-0" align="start">
                    <div className="p-2 space-y-2">
                        <div className="relative">
                            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search voices..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-8"
                            />
                        </div>

                        <div className="max-h-[300px] overflow-auto space-y-1">
                            {error ? (
                                <p className="text-sm text-red-500 text-center py-4">
                                    {error}
                                </p>
                            ) : isLoading ? (
                                <div className="flex items-center justify-center py-4">
                                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                                </div>
                            ) : filteredVoices.length === 0 ? (
                                <p className="text-sm text-muted-foreground text-center py-4">
                                    No voices found
                                </p>
                            ) : (
                                filteredVoices.map((voice) => (
                                    <div
                                        key={voice.voice_id}
                                        className={cn(
                                            "flex items-start space-x-3 p-2 hover:bg-accent rounded-sm cursor-pointer",
                                            value === voice.voice_id && "bg-accent"
                                        )}
                                        onClick={() => handleSelectVoice(voice.voice_id)}
                                    >
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <p className="text-sm font-medium truncate">
                                                    {voice.name}
                                                </p>
                                                {voice.gender && (
                                                    <span className="text-xs text-muted-foreground capitalize">
                                                        {voice.gender}
                                                    </span>
                                                )}
                                            </div>
                                            {voice.description && (
                                                <p className="text-xs text-muted-foreground line-clamp-2">
                                                    {voice.description}
                                                </p>
                                            )}
                                            <div className="flex items-center gap-2 mt-1">
                                                {voice.accent && (
                                                    <span className="text-xs bg-secondary px-1.5 py-0.5 rounded capitalize">
                                                        {voice.accent}
                                                    </span>
                                                )}
                                                {voice.language && (
                                                    <span className="text-xs bg-secondary px-1.5 py-0.5 rounded uppercase">
                                                        {voice.language}
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                        {voice.preview_url && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-8 w-8 p-0 shrink-0"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    playPreview(voice.preview_url!, voice.voice_id);
                                                }}
                                            >
                                                <Volume2
                                                    className={cn(
                                                        "h-4 w-4",
                                                        playingPreview === voice.voice_id &&
                                                            "text-primary animate-pulse"
                                                    )}
                                                />
                                            </Button>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>

                        <div className="pt-2 border-t flex items-center justify-between">
                            <div className="flex items-center space-x-2">
                                <Checkbox
                                    id="manual-voice-input-popup"
                                    checked={isManualInput}
                                    onCheckedChange={(checked) => {
                                        handleManualInputToggle(checked as boolean);
                                        if (checked) {
                                            setIsOpen(false);
                                        }
                                    }}
                                />
                                <Label
                                    htmlFor="manual-voice-input-popup"
                                    className="text-sm font-normal cursor-pointer"
                                >
                                    Add Voice ID Manually
                                </Label>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                {voices.length} voices available
                            </p>
                        </div>
                    </div>
                </PopoverContent>
            </Popover>
        </div>
    );
};
