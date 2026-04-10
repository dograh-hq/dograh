import { Loader2, Mic, Pause, Play, Square, Trash2Icon, Upload, X } from "lucide-react";
import posthog from "posthog-js";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createRecordingsApiV1WorkflowRecordingsPost,
    deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete,
    getUploadUrlsApiV1WorkflowRecordingsUploadUrlPost,
    listRecordingsApiV1WorkflowRecordingsGet,
    transcribeAudioApiV1WorkflowRecordingsTranscribePost,
} from "@/client";
import type { RecordingResponseSchema, RecordingUploadResponseSchema } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { PostHogEvent } from "@/constants/posthog-events";
import { useUserConfig } from "@/context/UserConfigContext";
import { useAudioPlayback } from "@/hooks/useAudioPlayback";

interface RecordingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    onRecordingsChange?: (recordings: RecordingResponseSchema[]) => void;
    ttsOverrides?: {
        provider?: string;
        model?: string;
        voice?: string;
    };
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

type RecordingStep = "idle" | "naming" | "recording";

interface PendingFile {
    id: string;
    file: File;
    transcript: string;
    isTranscribing: boolean;
    error?: string;
}

let pendingFileCounter = 0;

export const RecordingsDialog = ({
    open,
    onOpenChange,
    onRecordingsChange,
    ttsOverrides,
}: RecordingsDialogProps) => {
    const { userConfig } = useUserConfig();
    const [recordings, setRecordings] = useState<RecordingResponseSchema[]>([]);
    const [loading, setLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [language, setLanguage] = useState("multi");
    const [recordingStep, setRecordingStep] = useState<RecordingStep>("idle");
    const [recordingFilename, setRecordingFilename] = useState("");
    const [recordingDuration, setRecordingDuration] = useState(0);
    const { playingId, toggle: togglePlayback, stop: stopPlayback } = useAudioPlayback();
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const audioChunksRef = useRef<Blob[]>([]);
    const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const languageRef = useRef(language);
    languageRef.current = language;

    const ttsProvider = ttsOverrides?.provider ?? (userConfig?.tts?.provider as string) ?? "";
    const ttsModel = ttsOverrides?.model ?? (userConfig?.tts?.model as string) ?? "";
    const ttsVoiceId = ttsOverrides?.voice ?? (userConfig?.tts?.voice as string) ?? "";

    const fetchRecordings = useCallback(async () => {
        setLoading(true);
        try {
            const result = await listRecordingsApiV1WorkflowRecordingsGet({
                query: {
                    tts_provider: ttsProvider || undefined,
                    tts_model: ttsModel || undefined,
                    tts_voice_id: ttsVoiceId || undefined,
                },
            });
            const recs = result.data?.recordings ?? [];
            setRecordings(recs);
            onRecordingsChange?.(recs);
        } catch {
            setError("Failed to load recordings");
        } finally {
            setLoading(false);
        }
    }, [ttsProvider, ttsModel, ttsVoiceId, onRecordingsChange]);

    const stopRecordingTimer = useCallback(() => {
        if (recordingTimerRef.current) {
            clearInterval(recordingTimerRef.current);
            recordingTimerRef.current = null;
        }
    }, []);

    const stopRecording = useCallback(() => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
            mediaRecorderRef.current.stop();
        }
    }, []);

    const resetRecordingState = useCallback(() => {
        setRecordingStep("idle");
        setRecordingFilename("");
        setRecordingDuration(0);
    }, []);


    useEffect(() => {
        if (open) {
            fetchRecordings();
            setError(null);
            setPendingFiles([]);
            setLanguage("multi");
            resetRecordingState();
        }
    }, [open, fetchRecordings, resetRecordingState]);

    useEffect(() => {
        if (!open) {
            stopRecording();
            stopRecordingTimer();
            stopPlayback();
        }
    }, [open, stopRecording, stopRecordingTimer, stopPlayback]);

    const transcribeFile = async (pendingId: string, file: File) => {
        setPendingFiles((prev) =>
            prev.map((p) => (p.id === pendingId ? { ...p, isTranscribing: true } : p))
        );
        try {
            const currentLang = languageRef.current;
            const result = await transcribeAudioApiV1WorkflowRecordingsTranscribePost({
                body: { file, language: currentLang },
            });
            const data = result.data as Record<string, unknown> | undefined;
            if (data?.transcript) {
                setPendingFiles((prev) =>
                    prev.map((p) =>
                        p.id === pendingId ? { ...p, transcript: data.transcript as string, isTranscribing: false } : p
                    )
                );
            } else {
                setPendingFiles((prev) =>
                    prev.map((p) => (p.id === pendingId ? { ...p, isTranscribing: false } : p))
                );
            }
        } catch {
            setPendingFiles((prev) =>
                prev.map((p) =>
                    p.id === pendingId
                        ? { ...p, isTranscribing: false, error: "Auto-transcription failed" }
                        : p
                )
            );
        }
    };

    const addPendingFiles = (files: File[]) => {
        const valid: PendingFile[] = [];
        for (const file of files) {
            if (file.size > MAX_FILE_SIZE) {
                setError(`${file.name} (${(file.size / (1024 * 1024)).toFixed(1)}MB) exceeds 5MB limit — skipped.`);
                continue;
            }
            const id = `pending-${++pendingFileCounter}`;
            valid.push({ id, file, transcript: "", isTranscribing: false });
        }
        if (valid.length === 0) return;
        setPendingFiles((prev) => [...prev, ...valid]);
        setError(null);
        for (const pf of valid) {
            transcribeFile(pf.id, pf.file);
        }
    };

    const removePendingFile = (pendingId: string) => {
        setPendingFiles((prev) => prev.filter((p) => p.id !== pendingId));
    };

    const updateTranscript = (pendingId: string, transcript: string) => {
        setPendingFiles((prev) =>
            prev.map((p) => (p.id === pendingId ? { ...p, transcript } : p))
        );
    };

    const startRecording = async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const mediaRecorder = new MediaRecorder(stream);
            mediaRecorderRef.current = mediaRecorder;
            audioChunksRef.current = [];

            mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) audioChunksRef.current.push(e.data);
            };

            const filename = recordingFilename.trim() || "recording";
            mediaRecorder.onstop = () => {
                stream.getTracks().forEach((t) => t.stop());
                stopRecordingTimer();

                const blob = new Blob(audioChunksRef.current, { type: mediaRecorder.mimeType });
                if (blob.size > MAX_FILE_SIZE) {
                    setError(`Recording (${(blob.size / (1024 * 1024)).toFixed(1)}MB) exceeds the maximum allowed size of 5MB.`);
                    resetRecordingState();
                    return;
                }
                const ext = mediaRecorder.mimeType.includes("webm") ? "webm" : "mp4";
                const file = new File([blob], `${filename}.${ext}`, { type: mediaRecorder.mimeType });
                resetRecordingState();
                addPendingFiles([file]);
            };

            mediaRecorder.start();
            setRecordingStep("recording");
            setRecordingDuration(0);
            setError(null);
            recordingTimerRef.current = setInterval(() => {
                setRecordingDuration((d) => d + 1);
            }, 1000);
        } catch {
            setError("Microphone access denied. Please allow microphone permissions.");
            resetRecordingState();
        }
    };

    const handleStopRecording = () => {
        stopRecording();
    };

    const handleFileSelect = (fileList: FileList | null) => {
        if (!fileList || fileList.length === 0) return;
        addPendingFiles(Array.from(fileList));
        if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const handleUpload = async () => {
        const ready = pendingFiles.filter((p) => p.transcript.trim() && !p.isTranscribing);
        if (ready.length === 0) return;
        if (!ttsProvider || !ttsModel || !ttsVoiceId) {
            setError(
                "TTS configuration (provider, model, voice) must be set in your user configuration before uploading."
            );
            return;
        }

        setUploading(true);
        setError(null);

        try {
            // Step 1: Get presigned URLs for all files
            const uploadUrlResponse =
                await getUploadUrlsApiV1WorkflowRecordingsUploadUrlPost({
                    body: {
                        files: ready.map((p) => ({
                            filename: p.file.name,
                            mime_type: p.file.type || "audio/wav",
                            file_size: p.file.size,
                        })),
                    },
                });

            if (!uploadUrlResponse.data?.items) {
                throw new Error("Failed to get upload URLs");
            }

            const items = uploadUrlResponse.data.items;

            // Step 2: Upload all files to storage in parallel
            await Promise.all(
                items.map(async (item: RecordingUploadResponseSchema, idx: number) => {
                    const file = ready[idx].file;
                    const uploadResponse = await fetch(item.upload_url, {
                        method: "PUT",
                        body: file,
                        headers: {
                            "Content-Type": file.type || "audio/wav",
                        },
                    });
                    if (!uploadResponse.ok) {
                        throw new Error(`File upload failed for ${file.name}`);
                    }
                })
            );

            // Step 3: Create all recording records
            await createRecordingsApiV1WorkflowRecordingsPost({
                body: {
                    recordings: items.map((item: RecordingUploadResponseSchema, idx: number) => ({
                        recording_id: item.recording_id,
                        tts_provider: ttsProvider,
                        tts_model: ttsModel,
                        tts_voice_id: ttsVoiceId,
                        transcript: ready[idx].transcript.trim(),
                        storage_key: item.storage_key,
                        metadata: {
                            original_filename: ready[idx].file.name,
                            file_size_bytes: ready[idx].file.size,
                            mime_type: ready[idx].file.type,
                            language,
                        },
                    })),
                },
            });

            // Reset form and refresh list
            setPendingFiles([]);
            setLanguage("multi");
            resetRecordingState();
            if (fileInputRef.current) fileInputRef.current.value = "";
            await fetchRecordings();
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to upload recordings"
            );
        } finally {
            setUploading(false);
        }
    };

    const handleDelete = async (recordingId: string) => {
        try {
            await deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete({
                path: { recording_id: recordingId },
            });
            await fetchRecordings();
        } catch {
            setError("Failed to delete recording");
        }
    };

    const handlePlay = async (rec: RecordingResponseSchema) => {
        try {
            await togglePlayback(rec.recording_id, rec.storage_key, rec.storage_backend);
            posthog.capture(PostHogEvent.RECORDING_PLAYED, {
                recording_id: rec.recording_id,
                source: 'recordings_dialog',
            });
        } catch {
            setError("Failed to play recording");
        }
    };

    const isRecording = recordingStep === "recording";
    const anyTranscribing = pendingFiles.some((p) => p.isTranscribing);
    const readyCount = pendingFiles.filter((p) => p.transcript.trim() && !p.isTranscribing).length;
    const isBusy = uploading || isRecording || anyTranscribing;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Workflow Recordings</DialogTitle>
                    <DialogDescription>
                        Upload or record audio for hybrid prompts. Recordings are
                        scoped to your current TTS configuration. Use{" "}
                        <code className="text-xs bg-muted px-1 rounded">@</code> in
                        prompt fields to insert them.
                    </DialogDescription>
                </DialogHeader>

                {/* Current TTS Config */}
                <div className="rounded-md border p-3 bg-muted/30 text-sm space-y-1">
                    <div className="font-medium text-xs text-muted-foreground uppercase tracking-wide">
                        Current TTS Configuration
                    </div>
                    {ttsProvider ? (
                        <div className="flex flex-wrap gap-2 text-xs">
                            <span className="bg-background px-2 py-0.5 rounded border">
                                Provider: {ttsProvider}
                            </span>
                            <span className="bg-background px-2 py-0.5 rounded border">
                                Model: {ttsModel}
                            </span>
                            <span className="bg-background px-2 py-0.5 rounded border truncate max-w-[200px]">
                                VoiceID: {ttsVoiceId}
                            </span>
                        </div>
                    ) : (
                        <p className="text-xs text-destructive">
                            No TTS configuration found. Set it in Model Configurations.
                        </p>
                    )}
                </div>

                {error && (
                    <div className="text-sm text-destructive bg-destructive/10 rounded-md p-2">
                        {error}
                    </div>
                )}

                {/* Upload Section */}
                <div className="space-y-3 border rounded-md p-3">
                    <Label className="text-sm font-medium">Add New Recordings</Label>

                    {/* Audio source: file picker or record */}
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Audio Files
                        </Label>
                        <div className="flex gap-2">
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="audio/*"
                                multiple
                                onChange={(e) => handleFileSelect(e.target.files)}
                                className="hidden"
                            />
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="flex-1 justify-start text-sm font-normal"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={isBusy}
                            >
                                <Upload className="w-4 h-4 mr-2 shrink-0" />
                                <span className="text-muted-foreground">Choose audio files (max 5MB each)</span>
                            </Button>
                            {recordingStep === "idle" && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setRecordingStep("naming")}
                                    disabled={uploading || anyTranscribing}
                                >
                                    <Mic className="w-4 h-4 mr-1" />
                                    Record
                                </Button>
                            )}
                        </div>
                    </div>

                    {/* Recording: filename + start/stop */}
                    {(recordingStep === "naming" || isRecording) && (
                        <div className="space-y-2 rounded-md border border-dashed p-3 bg-muted/20">
                            {recordingStep === "naming" && (
                                <>
                                    <div>
                                        <Label className="text-xs text-muted-foreground">
                                            Recording Name
                                        </Label>
                                        <Input
                                            placeholder="e.g. greeting, hold-message"
                                            value={recordingFilename}
                                            onChange={(e) => setRecordingFilename(e.target.value)}
                                            autoFocus
                                        />
                                    </div>
                                    <div className="flex gap-2">
                                        <Button
                                            size="sm"
                                            onClick={startRecording}
                                            disabled={!recordingFilename.trim()}
                                        >
                                            <Mic className="w-4 h-4 mr-1" />
                                            Start Recording
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            onClick={resetRecordingState}
                                        >
                                            Cancel
                                        </Button>
                                    </div>
                                </>
                            )}
                            {isRecording && (
                                <div className="flex items-center gap-3">
                                    <span className="relative flex h-3 w-3">
                                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                                        <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500" />
                                    </span>
                                    <span className="text-sm font-mono">
                                        {Math.floor(recordingDuration / 60)}:{(recordingDuration % 60).toString().padStart(2, "0")}
                                    </span>
                                    <span className="text-xs text-muted-foreground">{recordingFilename}</span>
                                    <Button
                                        size="sm"
                                        variant="destructive"
                                        onClick={handleStopRecording}
                                        className="ml-auto"
                                    >
                                        <Square className="w-4 h-4 mr-1" />
                                        Stop
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Pending files list */}
                    {pendingFiles.length > 0 && (
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">
                                Pending ({pendingFiles.length} file{pendingFiles.length !== 1 ? "s" : ""})
                            </Label>
                            {pendingFiles.map((pf) => (
                                <div key={pf.id} className="rounded-md border p-2 space-y-1.5 bg-muted/10">
                                    <div className="flex items-center gap-2">
                                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate flex-1">
                                            {pf.file.name} ({(pf.file.size / (1024 * 1024)).toFixed(1)}MB)
                                        </code>
                                        {pf.isTranscribing && (
                                            <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground shrink-0" />
                                        )}
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            className="h-6 w-6 p-0 shrink-0"
                                            onClick={() => removePendingFile(pf.id)}
                                            disabled={uploading}
                                        >
                                            <X className="w-3.5 h-3.5" />
                                        </Button>
                                    </div>
                                    {pf.error && (
                                        <p className="text-xs text-destructive">{pf.error}</p>
                                    )}
                                    <Textarea
                                        placeholder={pf.isTranscribing ? "Transcribing..." : "What does this recording say?"}
                                        value={pf.transcript}
                                        onChange={(e) => updateTranscript(pf.id, e.target.value)}
                                        disabled={pf.isTranscribing}
                                        rows={2}
                                        className="resize-none text-sm"
                                    />
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Language */}
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Language
                        </Label>
                        <Select value={language} onValueChange={setLanguage}>
                            <SelectTrigger className="h-9 text-sm">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {Object.entries(LANGUAGE_DISPLAY_NAMES).map(([code, name]) => (
                                    <SelectItem key={code} value={code}>
                                        {name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    <Button
                        size="sm"
                        onClick={handleUpload}
                        disabled={readyCount === 0 || isBusy}
                    >
                        {uploading ? (
                            <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                        ) : (
                            <Upload className="w-4 h-4 mr-1" />
                        )}
                        {uploading
                            ? "Uploading..."
                            : `Upload ${readyCount} Recording${readyCount !== 1 ? "s" : ""}`}
                    </Button>
                </div>

                {/* Recordings List */}
                <div className="space-y-2">
                    <Label className="text-sm font-medium">
                        Recordings{" "}
                        {!loading && (
                            <span className="text-muted-foreground font-normal">
                                ({recordings.length})
                            </span>
                        )}
                    </Label>
                    {loading ? (
                        <div className="flex items-center justify-center py-4">
                            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
                        </div>
                    ) : recordings.length === 0 ? (
                        <p className="text-sm text-muted-foreground py-2">
                            No recordings yet for this TTS configuration.
                        </p>
                    ) : (
                        recordings.map((rec) => (
                            <div
                                key={rec.recording_id}
                                className="flex items-start gap-2 p-2 border rounded-md"
                            >
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-[300px]">
                                            {(rec.metadata?.original_filename as string) || rec.recording_id}
                                        </code>
                                    </div>
                                    <p className="text-sm text-muted-foreground mt-1 break-all line-clamp-2">
                                        {rec.transcript}
                                    </p>
                                </div>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => handlePlay(rec)}
                                >
                                    {playingId === rec.recording_id ? (
                                        <Pause className="w-4 h-4" />
                                    ) : (
                                        <Play className="w-4 h-4" />
                                    )}
                                </Button>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => handleDelete(rec.recording_id)}
                                >
                                    <Trash2Icon className="w-4 h-4" />
                                </Button>
                            </div>
                        ))
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
};
