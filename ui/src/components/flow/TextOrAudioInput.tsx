import type { RecordingResponseSchema } from "@/client/types.gen";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

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
    return (
        <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">
                Select a pre-recorded audio file to play.
            </Label>
            <Select value={value} onValueChange={onChange}>
                <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select a recording" />
                </SelectTrigger>
                <SelectContent>
                    {recordings.length === 0 ? (
                        <SelectItem value="__empty__" disabled>
                            No recordings available
                        </SelectItem>
                    ) : (
                        recordings.map((r) => (
                            <SelectItem key={r.recording_id} value={r.recording_id}>
                                <span className="truncate">
                                    {(r.metadata?.original_filename as string) || r.recording_id}
                                </span>
                                {r.transcript && (
                                    <span className="text-xs text-muted-foreground ml-2 truncate">
                                        — {r.transcript}
                                    </span>
                                )}
                            </SelectItem>
                        ))
                    )}
                </SelectContent>
            </Select>
        </div>
    );
}
