import { Loader2, Mic, Pause, Play, Square, Trash2Icon, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createRecordingApiV1WorkflowRecordingsPost,
    deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete,
    getSignedUrlApiV1S3SignedUrlGet,
    getUploadUrlApiV1WorkflowRecordingsUploadUrlPost,
    listRecordingsApiV1WorkflowRecordingsGet,
    transcribeAudioApiV1WorkflowRecordingsTranscribePost,
} from "@/client";
import type { RecordingResponseSchema } from "@/client/types.gen";
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
import { useUserConfig } from "@/context/UserConfigContext";

interface RecordingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    onRecordingsChange?: (recordings: RecordingResponseSchema[]) => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

type RecordingStep = "idle" | "naming" | "recording" | "transcribing";

export const RecordingsDialog = ({
    open,
    onOpenChange,
    workflowId,
    onRecordingsChange,
}: RecordingsDialogProps) => {
    const { userConfig } = useUserConfig();
    const [recordings, setRecordings] = useState<RecordingResponseSchema[]>([]);
    const [loading, setLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [transcript, setTranscript] = useState("");
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [language, setLanguage] = useState("multi");
    const [recordingStep, setRecordingStep] = useState<RecordingStep>("idle");
    const [recordingFilename, setRecordingFilename] = useState("");
    const [recordingDuration, setRecordingDuration] = useState(0);
    const [playingId, setPlayingId] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const audioChunksRef = useRef<Blob[]>([]);
    const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const languageRef = useRef(language);
    languageRef.current = language;

    const ttsProvider = (userConfig?.tts?.provider as string) ?? "";
    const ttsModel = (userConfig?.tts?.model as string) ?? "";
    const ttsVoiceId = (userConfig?.tts?.voice as string) ?? "";

    const fetchRecordings = useCallback(async () => {
        if (!workflowId) return;
        setLoading(true);
        try {
            const result = await listRecordingsApiV1WorkflowRecordingsGet({
                query: {
                    workflow_id: workflowId,
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
    }, [workflowId, ttsProvider, ttsModel, ttsVoiceId, onRecordingsChange]);

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

    const stopPlayback = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause();
            audioRef.current = null;
        }
        setPlayingId(null);
    }, []);

    useEffect(() => {
        if (open) {
            fetchRecordings();
            setError(null);
            setTranscript("");
            setSelectedFile(null);
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

    const transcribeFile = async (file: File) => {
        setRecordingStep("transcribing");
        try {
            const currentLang = languageRef.current;
            const result = await transcribeAudioApiV1WorkflowRecordingsTranscribePost({
                body: { file, language: currentLang },
            });
            const data = result.data as Record<string, unknown> | undefined;
            if (data?.transcript) {
                setTranscript(data.transcript as string);
            }
        } catch {
            // Transcription failed — user can still type manually
            setError("Auto-transcription failed. You can type the transcript manually.");
        } finally {
            setRecordingStep("idle");
        }
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
                setSelectedFile(file);
                setError(null);
                transcribeFile(file);
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

    const handleFileSelect = (file: File | null) => {
        if (file && file.size > MAX_FILE_SIZE) {
            setError(`File size (${(file.size / (1024 * 1024)).toFixed(1)}MB) exceeds the maximum allowed size of 5MB.`);
            setSelectedFile(null);
            if (fileInputRef.current) fileInputRef.current.value = "";
            return;
        }
        setError(null);
        setSelectedFile(file);
        if (file) transcribeFile(file);
    };

    const handleUpload = async () => {
        if (!selectedFile || !transcript.trim()) return;
        if (!ttsProvider || !ttsModel || !ttsVoiceId) {
            setError(
                "TTS configuration (provider, model, voice) must be set in your user configuration before uploading."
            );
            return;
        }

        setUploading(true);
        setError(null);

        try {
            // Step 1: Get presigned URL
            const uploadUrlResponse =
                await getUploadUrlApiV1WorkflowRecordingsUploadUrlPost({
                    body: {
                        workflow_id: workflowId,
                        filename: selectedFile.name,
                        mime_type: selectedFile.type || "audio/wav",
                        file_size: selectedFile.size,
                    },
                });

            if (!uploadUrlResponse.data) {
                throw new Error("Failed to get upload URL");
            }

            const { upload_url, recording_id, storage_key } =
                uploadUrlResponse.data;

            // Step 2: Upload file directly to storage
            const uploadResponse = await fetch(upload_url, {
                method: "PUT",
                body: selectedFile,
                headers: {
                    "Content-Type": selectedFile.type || "audio/wav",
                },
            });

            if (!uploadResponse.ok) {
                throw new Error("File upload failed");
            }

            // Step 3: Create recording record
            await createRecordingApiV1WorkflowRecordingsPost({
                body: {
                    recording_id,
                    workflow_id: workflowId,
                    tts_provider: ttsProvider,
                    tts_model: ttsModel,
                    tts_voice_id: ttsVoiceId,
                    transcript: transcript.trim(),
                    storage_key,
                    metadata: {
                        original_filename: selectedFile.name,
                        file_size_bytes: selectedFile.size,
                        mime_type: selectedFile.type,
                        language,
                    },
                },
            });

            // Reset form and refresh list
            setTranscript("");
            setSelectedFile(null);
            setLanguage("multi");
            resetRecordingState();
            if (fileInputRef.current) fileInputRef.current.value = "";
            await fetchRecordings();
        } catch (err) {
            setError(
                err instanceof Error ? err.message : "Failed to upload recording"
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
        if (playingId === rec.recording_id) {
            stopPlayback();
            return;
        }
        stopPlayback();
        try {
            const result = await getSignedUrlApiV1S3SignedUrlGet({
                query: {
                    key: rec.storage_key,
                    storage_backend: rec.storage_backend,
                },
            });
            if (!result.data?.url) {
                setError("Failed to get audio URL");
                return;
            }
            const audio = new Audio(result.data.url);
            audio.onended = () => setPlayingId(null);
            audioRef.current = audio;
            setPlayingId(rec.recording_id);
            await audio.play();
        } catch {
            setError("Failed to play recording");
        }
    };

    const isRecording = recordingStep === "recording";
    const isTranscribing = recordingStep === "transcribing";
    const isBusy = uploading || isRecording || isTranscribing;

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
                    <Label className="text-sm font-medium">Add New Recording</Label>

                    {/* Audio source: file picker or record */}
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Audio File
                        </Label>
                        <div className="flex gap-2">
                            <input
                                ref={fileInputRef}
                                type="file"
                                accept="audio/*"
                                onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
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
                                {selectedFile && recordingStep !== "naming" ? (
                                    <span className="truncate">
                                        {selectedFile.name} ({(selectedFile.size / (1024 * 1024)).toFixed(1)}MB)
                                    </span>
                                ) : (
                                    <span className="text-muted-foreground">Choose audio file (max 5MB)</span>
                                )}
                            </Button>
                            {recordingStep === "idle" && (
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => setRecordingStep("naming")}
                                    disabled={uploading || isTranscribing}
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

                    {/* Transcribing progress */}
                    {isTranscribing && (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Transcribing audio...
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

                    {/* Transcript */}
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Transcript
                        </Label>
                        <Textarea
                            placeholder={isTranscribing ? "Transcribing..." : "What does this recording say?"}
                            value={transcript}
                            onChange={(e) => setTranscript(e.target.value)}
                            disabled={isTranscribing}
                            rows={3}
                            className="resize-none text-sm"
                        />
                    </div>

                    <Button
                        size="sm"
                        onClick={handleUpload}
                        disabled={!selectedFile || !transcript.trim() || isBusy}
                    >
                        {uploading ? (
                            <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                        ) : (
                            <Upload className="w-4 h-4 mr-1" />
                        )}
                        {uploading ? "Uploading..." : "Upload Recording"}
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
