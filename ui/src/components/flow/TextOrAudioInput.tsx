import { Check, ChevronDown, Pause, Play, Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContentInline, PopoverTrigger } from "@/components/ui/popover";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useAudioPlayback } from "@/hooks/useAudioPlayback";
import { cn } from "@/lib/utils";

interface TextOrAudioInputProps {
    type: 'text' | 'audio';
    onTypeChange: (type: 'text' | 'audio') => void;
    recordingId: string;
    onRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    /** Rendered when type === 'text' */
    children: React.ReactNode;
}

export function TextOrAudioInput({
    type,
    onTypeChange,
    recordingId,
    onRecordingIdChange,
    recordings = [],
    children,
}: TextOrAudioInputProps) {
    return (
        <>
            <RadioGroup
                value={type}
                onValueChange={(value) => onTypeChange(value as 'text' | 'audio')}
                className="flex items-center gap-4"
            >
                <div className="flex items-center gap-2">
                    <RadioGroupItem value="text" id="toa-text" />
                    <Label htmlFor="toa-text" className="font-normal cursor-pointer">Text</Label>
                </div>
                <div className="flex items-center gap-2">
                    <RadioGroupItem value="audio" id="toa-audio" />
                    <Label htmlFor="toa-audio" className="font-normal cursor-pointer">Audio</Label>
                </div>
            </RadioGroup>
            {type === 'text' ? (
                children
            ) : (
                <RecordingSelect
                    value={recordingId}
                    onChange={onRecordingIdChange}
                    recordings={recordings}
                />
            )}
        </>
    );
}

interface RecordingSelectProps {
    value: string;
    onChange: (id: string) => void;
    recordings: RecordingResponseSchema[];
}

/**
 * Dropdown to select a pre-recorded audio file.
 * Re-exported so callers that only need the dropdown (e.g. tool configs with
 * their own none/custom/audio radio) can use it directly.
 */
export function RecordingSelect({ value, onChange, recordings }: RecordingSelectProps) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState("");
    const { playingId, toggle, stop } = useAudioPlayback();

    const selected = recordings.find((r) => String(r.id) === value);

    const filtered = useMemo(() => {
        if (!search) return recordings;
        const q = search.toLowerCase();
        return recordings.filter((r) =>
            r.recording_id.toLowerCase().includes(q) ||
            r.transcript.toLowerCase().includes(q) ||
            ((r.metadata?.original_filename as string) || "").toLowerCase().includes(q)
        );
    }, [recordings, search]);

    const handleSelect = (rec: RecordingResponseSchema) => {
        stop();
        onChange(String(rec.id));
        setOpen(false);
    };

    const handlePlay = async (e: React.MouseEvent, rec: RecordingResponseSchema) => {
        e.stopPropagation();
        try {
            await toggle(rec.recording_id, rec.storage_key, rec.storage_backend);
        } catch {
            // Ignore playback errors
        }
    };

    return (
        <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">
                Select a pre-recorded audio file to play.
            </Label>
            <Popover modal open={open} onOpenChange={(v) => { if (!v) { stop(); setSearch(""); } setOpen(v); }}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={open}
                        className="w-full justify-between h-auto min-h-9 font-normal"
                    >
                        {selected ? (
                            <span className="flex items-center gap-2 text-left">
                                <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono shrink-0">
                                    {selected.recording_id}
                                </code>
                                <span className="text-sm">
                                    {selected.transcript.length > 75
                                        ? `${selected.transcript.slice(0, 75)}…`
                                        : selected.transcript}
                                </span>
                            </span>
                        ) : (
                            <span className="text-muted-foreground">Select a recording</span>
                        )}
                        <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                </PopoverTrigger>
                <PopoverContentInline
                    className="w-[var(--radix-popover-trigger-width)] p-0"
                    align="start"
                >
                    {recordings.length === 0 ? (
                        <div className="p-3 text-sm text-muted-foreground text-center">
                            No recordings available
                        </div>
                    ) : (
                        <div>
                            <div className="p-2 border-b">
                                <div className="relative">
                                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                                    <Input
                                        placeholder="Search by ID, transcript, or filename..."
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        className="h-8 pl-8 text-sm"
                                        autoFocus
                                    />
                                </div>
                            </div>
                            <div className="max-h-56 overflow-y-auto">
                            {filtered.length === 0 ? (
                                <div className="p-3 text-sm text-muted-foreground text-center">
                                    No recordings match &ldquo;{search}&rdquo;
                                </div>
                            ) : filtered.map((r) => {
                                const filename = (r.metadata?.original_filename as string) || "";
                                const isSelected = String(r.id) === value;
                                const isPlaying = playingId === r.recording_id;

                                return (
                                    <div
                                        key={r.id}
                                        className={cn(
                                            "flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-accent transition-colors",
                                            isSelected && "bg-accent"
                                        )}
                                        onClick={() => handleSelect(r)}
                                    >
                                        <Check className={cn(
                                            "h-4 w-4 shrink-0",
                                            isSelected ? "opacity-100" : "opacity-0"
                                        )} />
                                        <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono shrink-0">
                                            {r.recording_id}
                                        </code>
                                        {filename && (
                                            <span className="text-xs text-muted-foreground shrink-0 max-w-[100px] truncate">
                                                {filename}
                                            </span>
                                        )}
                                        <span className="text-xs text-muted-foreground bg-muted/50 px-1.5 py-0.5 rounded truncate flex-1 min-w-0">
                                            {r.transcript}
                                        </span>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="h-7 w-7 p-0 shrink-0"
                                            onClick={(e) => handlePlay(e, r)}
                                        >
                                            {isPlaying ? (
                                                <Pause className="h-3.5 w-3.5" />
                                            ) : (
                                                <Play className="h-3.5 w-3.5" />
                                            )}
                                        </Button>
                                    </div>
                                );
                            })}
                            </div>
                        </div>
                    )}
                </PopoverContentInline>
            </Popover>
        </div>
    );
}
