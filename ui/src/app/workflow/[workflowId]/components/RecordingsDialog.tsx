import { Loader2, Trash2Icon, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    createRecordingApiV1WorkflowRecordingsPost,
    deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete,
    getUploadUrlApiV1WorkflowRecordingsUploadUrlPost,
    listRecordingsApiV1WorkflowRecordingsGet,
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
import { useUserConfig } from "@/context/UserConfigContext";

interface RecordingsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    onRecordingsChange?: (recordings: RecordingResponseSchema[]) => void;
}

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB

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
    const fileInputRef = useRef<HTMLInputElement>(null);

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

    useEffect(() => {
        if (open) {
            fetchRecordings();
            setError(null);
            setTranscript("");
            setSelectedFile(null);
        }
    }, [open, fetchRecordings]);

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
                    },
                },
            });

            // Reset form and refresh list
            setTranscript("");
            setSelectedFile(null);
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

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg max-h-[80vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>Workflow Recordings</DialogTitle>
                    <DialogDescription>
                        Upload audio recordings for hybrid prompts. Recordings are
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
                    <Label className="text-sm font-medium">Upload New Recording</Label>
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Audio File
                        </Label>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="audio/*"
                            onChange={(e) => {
                                const file = e.target.files?.[0] ?? null;
                                if (file && file.size > MAX_FILE_SIZE) {
                                    setError(
                                        `File size (${(file.size / (1024 * 1024)).toFixed(1)}MB) exceeds the maximum allowed size of 5MB.`
                                    );
                                    setSelectedFile(null);
                                    if (fileInputRef.current) fileInputRef.current.value = "";
                                    return;
                                }
                                setError(null);
                                setSelectedFile(file);
                            }}
                            className="hidden"
                        />
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            className="w-full justify-start text-sm font-normal"
                            onClick={() => fileInputRef.current?.click()}
                        >
                            <Upload className="w-4 h-4 mr-2 shrink-0" />
                            {selectedFile ? (
                                <span className="truncate">
                                    {selectedFile.name} ({(selectedFile.size / (1024 * 1024)).toFixed(1)}MB)
                                </span>
                            ) : (
                                <span className="text-muted-foreground">Choose audio file (max 5MB)</span>
                            )}
                        </Button>
                    </div>
                    <div>
                        <Label className="text-xs text-muted-foreground">
                            Transcript
                        </Label>
                        <Input
                            placeholder="What does this recording say?"
                            value={transcript}
                            onChange={(e) => setTranscript(e.target.value)}
                        />
                    </div>
                    <Button
                        size="sm"
                        onClick={handleUpload}
                        disabled={!selectedFile || !transcript.trim() || uploading}
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
