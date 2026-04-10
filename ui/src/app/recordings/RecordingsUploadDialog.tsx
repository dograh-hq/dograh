"use client";

import { Loader2, Mic, Square, Upload, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createRecordingsApiV1WorkflowRecordingsPost,
    getUploadUrlsApiV1WorkflowRecordingsUploadUrlPost,
    transcribeAudioApiV1WorkflowRecordingsTranscribePost,
} from "@/client";
import type { RecordingUploadResponseSchema } from "@/client/types.gen";
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
interface RecordingsUploadDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onUploadComplete?: () => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

interface PendingFile {
    id: string;
    file: File;
    transcript: string;
    isTranscribing: boolean;
    error?: string;
}

let pendingFileCounter = 0;

export const RecordingsUploadDialog = ({
    open,
    onOpenChange,
    onUploadComplete,
}: RecordingsUploadDialogProps) => {
    const [uploading, setUploading] = useState(false);
    const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [language, setLanguage] = useState("multi");
    const [recordingStep, setRecordingStep] = useState<"idle" | "naming" | "recording">("idle");
    const [recordingFilename, setRecordingFilename] = useState("");
    const [recordingDuration, setRecordingDuration] = useState(0);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const audioChunksRef = useRef<Blob[]>([]);
    const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const languageRef = useRef(language);
    languageRef.current = language;

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
            setError(null);
            setPendingFiles([]);
            setLanguage("multi");
            resetRecordingState();
        }
    }, [open, resetRecordingState]);

    useEffect(() => {
        if (!open) {
            stopRecording();
            stopRecordingTimer();
        }
    }, [open, stopRecording, stopRecordingTimer]);

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

    const handleFileSelect = (fileList: FileList | null) => {
        if (!fileList || fileList.length === 0) return;
        addPendingFiles(Array.from(fileList));
        if (fileInputRef.current) fileInputRef.current.value = "";
    };

    const handleUpload = async () => {
        const ready = pendingFiles.filter((p) => p.transcript.trim() && !p.isTranscribing);
        if (ready.length === 0) return;

        setUploading(true);
        setError(null);

        try {
            const uploadUrlResponse = await getUploadUrlsApiV1WorkflowRecordingsUploadUrlPost({
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

            await Promise.all(
                items.map(async (item: RecordingUploadResponseSchema, idx: number) => {
                    const file = ready[idx].file;
                    const uploadResponse = await fetch(item.upload_url, {
                        method: "PUT",
                        body: file,
                        headers: { "Content-Type": file.type || "audio/wav" },
                    });
                    if (!uploadResponse.ok) {
                        throw new Error(`File upload failed for ${file.name}`);
                    }
                })
            );

            await createRecordingsApiV1WorkflowRecordingsPost({
                body: {
                    recordings: items.map((item: RecordingUploadResponseSchema, idx: number) => ({
                        recording_id: item.recording_id,
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

            setPendingFiles([]);
            setLanguage("multi");
            resetRecordingState();
            if (fileInputRef.current) fileInputRef.current.value = "";
            onUploadComplete?.();
            onOpenChange(false);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to upload recordings");
        } finally {
            setUploading(false);
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
                    <DialogTitle>Upload Recordings</DialogTitle>
                    <DialogDescription>
                        Upload or record audio files. Use{" "}
                        <code className="text-xs bg-muted px-1 rounded">@</code> in
                        prompt fields to insert them into your agents.
                    </DialogDescription>
                </DialogHeader>

                {error && (
                    <div className="text-sm text-destructive bg-destructive/10 rounded-md p-2">
                        {error}
                    </div>
                )}

                {/* Upload Section */}
                <div className="space-y-3">
                    {/* Audio source: file picker or record */}
                    <div>
                        <Label className="text-xs text-muted-foreground">Audio Files</Label>
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
                                        <Label className="text-xs text-muted-foreground">Recording Name</Label>
                                        <Input
                                            placeholder="e.g. greeting, hold-message"
                                            value={recordingFilename}
                                            onChange={(e) => setRecordingFilename(e.target.value)}
                                            autoFocus
                                        />
                                    </div>
                                    <div className="flex gap-2">
                                        <Button size="sm" onClick={startRecording} disabled={!recordingFilename.trim()}>
                                            <Mic className="w-4 h-4 mr-1" />
                                            Start Recording
                                        </Button>
                                        <Button size="sm" variant="ghost" onClick={resetRecordingState}>
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
                                        onClick={() => stopRecording()}
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
                        <Label className="text-xs text-muted-foreground">Language</Label>
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
            </DialogContent>
        </Dialog>
    );
};
