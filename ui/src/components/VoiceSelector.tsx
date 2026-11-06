"use client";

import { ChevronDown, Loader2, RotateCw, Search, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";

import { getVoicesApiV1UserConfigurationsVoicesProviderGet } from "@/client/sdk.gen";
import type { GetVoicesApiV1UserConfigurationsVoicesProviderGetData, VoiceInfo } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type VoiceCatalogProvider = GetVoicesApiV1UserConfigurationsVoicesProviderGetData["path"]["provider"];
const ALL_FILTER_VALUE = "__all__";

const voiceCatalogCache = new Map<string, VoiceInfo[]>();

export function clearVoiceCache(providerConnectionUuid?: string) {
    if (!providerConnectionUuid) {
        voiceCatalogCache.clear();
        return;
    }
    for (const key of Array.from(voiceCatalogCache.keys())) {
        const parts = key.split(":");
        if (parts[2] === providerConnectionUuid) {
            voiceCatalogCache.delete(key);
        }
    }
}

function getCacheKey(
    userId?: string,
    provider?: string,
    providerConnectionUuid?: string,
    connectionRevision?: number,
    model?: string,
    language?: string
): string | null {
    if (!userId || !provider) return null;
    return `${userId}:${provider}:${providerConnectionUuid || ""}:${connectionRevision ?? ""}:${model || ""}:${language || ""}`;
}

interface VoiceSelectorProps {
    id?: string;
    provider: string;
    providerConnectionUuid?: string;
    connectionRevision?: number;
    value: string;
    onChange: (voiceId: string) => void;
    model?: string;
    language?: string;
    showFilters?: boolean;
    className?: string;
    staticVoices?: VoiceInfo[];
}

export const VoiceSelector: React.FC<VoiceSelectorProps> = ({
    id,
    provider,
    providerConnectionUuid,
    connectionRevision,
    value,
    onChange,
    model,
    language,
    showFilters = false,
    className,
    staticVoices,
}) => {
    const isStatic = Boolean(staticVoices && staticVoices.length > 0);
    const { user, loading: authLoading } = useAuth();
    const userId = user?.id;
    const cacheKey = !authLoading && !isStatic ? getCacheKey(userId, provider, providerConnectionUuid, connectionRevision, model, language) : null;
    const cachedVoices = cacheKey ? voiceCatalogCache.get(cacheKey) : undefined;

    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const [genderFilter, setGenderFilter] = useState(ALL_FILTER_VALUE);
    const [languageFilter, setLanguageFilter] = useState(ALL_FILTER_VALUE);
    const [accentFilter, setAccentFilter] = useState(ALL_FILTER_VALUE);
    const [voices, setVoices] = useState<VoiceInfo[]>(() => staticVoices || cachedVoices || []);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [playingPreview, setPlayingPreview] = useState<string | null>(null);
    const [currentAudio, setCurrentAudio] = useState<HTMLAudioElement | null>(null);

    const handleReload = () => {
        if (cacheKey) {
            voiceCatalogCache.delete(cacheKey);
        }
        setReload((prev) => prev + 1);
    };

    useEffect(() => {
        if (staticVoices) {
            setVoices(staticVoices);
            setError(null);
            setIsLoading(false);
            return;
        }

        if (authLoading || !userId) {
            setVoices([]);
            setError(null);
            setIsLoading(false);
            return;
        }
        // The backend's voice_catalog field metadata opts providers into this control.
        const providerKey = provider as VoiceCatalogProvider;
        if (providerKey !== "dograh" && !providerConnectionUuid) {
            setVoices([]);
            setError("Select a provider connection to load voices.");
            setIsLoading(false);
            return;
        }

        const currentCacheKey = getCacheKey(userId, providerKey, providerConnectionUuid, connectionRevision, model, language);
        if (currentCacheKey && voiceCatalogCache.has(currentCacheKey)) {
            setVoices(voiceCatalogCache.get(currentCacheKey)!);
            setError(null);
            setIsLoading(false);
            return;
        }

        setVoices([]);
        setError(null);
        let active = true;
        const controller = new AbortController();
        setIsLoading(true);
        (async () => {
            try {
                const response = await getVoicesApiV1UserConfigurationsVoicesProviderGet({
                    path: { provider: providerKey },
                    query: {
                        ...(model ? { model } : {}),
                        ...(language ? { language } : {}),
                        ...(providerKey !== "dograh" ? { provider_connection_uuid: providerConnectionUuid } : {}),
                    },
                    signal: controller.signal,
                });
                if (!active) return;
                if (response.error) {
                    setError(detailFromError(response.error, "Failed to load voices"));
                } else {
                    const fetchedVoices = response.data?.voices || [];
                    if (currentCacheKey) {
                        voiceCatalogCache.set(currentCacheKey, fetchedVoices);
                    }
                    setVoices(fetchedVoices);
                }
            } catch {
                if (!active) return;
                setError("Failed to load voices");
                setVoices([]);
            } finally {
                if (active) setIsLoading(false);
            }
        })();
        return () => {
            active = false;
            controller.abort();
        };
    }, [provider, providerConnectionUuid, connectionRevision, model, language, authLoading, userId, reload, staticVoices]);

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
        const matchesSearch = (
            voice.name.toLowerCase().includes(searchLower) ||
            voice.voice_id.toLowerCase().includes(searchLower) ||
            (voice.description?.toLowerCase() || "").includes(searchLower) ||
            (voice.accent?.toLowerCase() || "").includes(searchLower) ||
            (voice.gender?.toLowerCase() || "").includes(searchLower) ||
            (voice.language?.toLowerCase() || "").includes(searchLower)
        );
        if (!matchesSearch) return false;
        if (genderFilter !== ALL_FILTER_VALUE && (voice.gender || "").toLowerCase() !== genderFilter) return false;
        if (languageFilter !== ALL_FILTER_VALUE && (voice.language || "").toLowerCase() !== languageFilter) return false;
        if (accentFilter !== ALL_FILTER_VALUE && (voice.accent || "").toLowerCase() !== accentFilter) return false;
        return true;
    });

    const genderOptions = Array.from(
        new Set(voices.map((voice) => voice.gender?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();
    const languageOptions = Array.from(
        new Set(voices.map((voice) => voice.language?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();
    const accentOptions = Array.from(
        new Set(voices.map((voice) => voice.accent?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();

    const handleSelectVoice = (voiceId: string) => {
        onChange(voiceId);
        setIsOpen(false);
        setSearchTerm("");
    };

    const getSelectedVoiceName = () => {
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


    return (
        <div className={cn("space-y-2", className)}>
            <Popover open={isOpen} onOpenChange={setIsOpen}>
                <PopoverTrigger asChild>
                    <Button
                        id={id}
                        type="button"
                        variant="outline"
                        role="combobox"
                        aria-expanded={isOpen}
                        className={cn(
                            "w-full justify-between",
                            !value && "text-muted-foreground"
                        )}
                        disabled={isLoading || authLoading || !userId}
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
                <PopoverContent
                    className="w-[400px] max-h-[var(--radix-popover-content-available-height,calc(100vh-32px))] p-0 flex flex-col overflow-hidden"
                    align="start"
                    collisionPadding={16}
                    sideOffset={4}
                >
                    <div className="p-2 space-y-2 border-b bg-popover shrink-0">
                        <div className="relative">
                            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search voices..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-8"
                            />
                        </div>

                        {showFilters && (
                            <div className="grid gap-2 sm:grid-cols-3">
                                <Select value={genderFilter} onValueChange={setGenderFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Gender" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All genders</SelectItem>
                                        {genderOptions.map((gender) => (
                                            <SelectItem key={gender} value={gender} className="capitalize">
                                                {gender}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>

                                <Select value={languageFilter} onValueChange={setLanguageFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Language" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All languages</SelectItem>
                                        {languageOptions.map((voiceLanguage) => (
                                            <SelectItem key={voiceLanguage} value={voiceLanguage} className="uppercase">
                                                {voiceLanguage}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>

                                <Select value={accentFilter} onValueChange={setAccentFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Accent" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All accents</SelectItem>
                                        {accentOptions.map((accent) => (
                                            <SelectItem key={accent} value={accent} className="uppercase">
                                                {accent}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                    </div>

                    <div className="flex-1 overflow-y-auto min-h-0 max-h-[320px] p-2 space-y-1">
                        {error ? (
                            <div className="py-4 text-center">
                                <p role="alert" className="text-sm text-red-500">{error}</p>
                                <Button type="button" variant="ghost" size="sm" onClick={handleReload}>Retry</Button>
                            </div>
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

                        <div className="p-2 border-t flex items-center justify-between shrink-0 bg-popover">
                            {!isStatic && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 text-xs text-muted-foreground hover:text-foreground px-2"
                                    onClick={handleReload}
                                    disabled={isLoading}
                                >
                                    <RotateCw className={cn("h-3 w-3 mr-1", isLoading && "animate-spin")} />
                                    Refresh
                                </Button>
                            )}
                            <p className={cn("text-xs text-muted-foreground", isStatic && "ml-auto")}>
                                {filteredVoices.length} of {voices.length} voices
                            </p>
                        </div>
                </PopoverContent>
            </Popover>
        </div>
    );
};
