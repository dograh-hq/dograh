"use client";

import { AudioLines, Check, Pause, Pencil, Play, RefreshCw, Search, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
    listRecordingsApiV1WorkflowRecordingsGet,
    updateRecordingApiV1WorkflowRecordingsIdPatch,
} from "@/client/sdk.gen";
import type { RecordingResponseSchema, WorkflowSummaryResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAudioPlayback } from "@/hooks/useAudioPlayback";
import logger from "@/lib/logger";

const ALL_VALUE = "__all__";

export default function RecordingsList() {
    const [recordings, setRecordings] = useState<RecordingResponseSchema[]>([]);
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [searchQuery, setSearchQuery] = useState("");
    const [error, setError] = useState<string | null>(null);

    // Filters
    const [selectedWorkflow, setSelectedWorkflow] = useState<string>(ALL_VALUE);

    // Inline edit state
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editValue, setEditValue] = useState("");

    const { playingId, toggle: togglePlayback, stop: stopPlayback } = useAudioPlayback();
    const hasFetchedWorkflows = useRef(false);

    const workflowMap = useMemo(() => {
        const map = new Map<number, string>();
        for (const w of workflows) {
            map.set(w.id, w.name);
        }
        return map;
    }, [workflows]);

    const fetchWorkflows = useCallback(async () => {
        try {
            const response = await getWorkflowsSummaryApiV1WorkflowSummaryGet();
            if (response.data) {
                setWorkflows(response.data);
            }
        } catch (err) {
            logger.error("Error fetching workflows:", err);
        }
    }, []);

    const fetchRecordings = useCallback(async () => {
        try {
            setIsLoading(true);
            setError(null);

            const response = await listRecordingsApiV1WorkflowRecordingsGet({
                query: {
                    workflow_id: selectedWorkflow !== ALL_VALUE ? Number(selectedWorkflow) : undefined,
                },
            });

            if (response.error || !response.data) {
                throw new Error("Failed to fetch recordings");
            }

            setRecordings(response.data.recordings);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch recordings");
            logger.error("Error fetching recordings:", err);
        } finally {
            setIsLoading(false);
        }
    }, [selectedWorkflow]);

    useEffect(() => {
        if (!hasFetchedWorkflows.current) {
            hasFetchedWorkflows.current = true;
            fetchWorkflows();
        }
    }, [fetchWorkflows]);

    useEffect(() => {
        fetchRecordings();
    }, [fetchRecordings]);

    const handleDelete = async (recordingId: string) => {
        if (!confirm("Are you sure you want to delete this recording?")) return;

        try {
            const response = await deleteRecordingApiV1WorkflowRecordingsRecordingIdDelete({
                path: { recording_id: recordingId },
            });

            if (response.error) {
                throw new Error("Failed to delete recording");
            }

            toast.success("Recording deleted");
            fetchRecordings();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to delete recording");
            logger.error("Error deleting recording:", err);
        }
    };

    const handlePlay = async (rec: RecordingResponseSchema) => {
        try {
            await togglePlayback(rec.recording_id, rec.storage_key, rec.storage_backend);
        } catch {
            toast.error("Failed to play recording");
        }
    };

    const startEditing = (rec: RecordingResponseSchema) => {
        setEditingId(rec.recording_id);
        setEditValue(rec.recording_id);
    };

    const cancelEditing = () => {
        setEditingId(null);
        setEditValue("");
    };

    const saveRecordingId = async (rec: RecordingResponseSchema) => {
        const newId = editValue.trim();
        if (!newId) {
            toast.error("Recording ID cannot be empty");
            return;
        }
        if (newId === rec.recording_id) {
            cancelEditing();
            return;
        }

        try {
            const response = await updateRecordingApiV1WorkflowRecordingsIdPatch({
                path: { id: rec.id },
                body: { recording_id: newId },
            });

            if (response.error) {
                const errData = response.error as { detail?: string };
                throw new Error(errData?.detail || "Failed to update recording ID");
            }

            toast.success(`Recording ID updated to "${newId}"`);
            cancelEditing();
            fetchRecordings();
        } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to update recording ID");
        }
    };

    const formatDate = (dateString: string): string => {
        const date = new Date(dateString);
        return date.toLocaleDateString() + " " + date.toLocaleTimeString();
    };

    const filteredRecordings = recordings.filter((rec) => {
        if (!searchQuery) return true;
        const q = searchQuery.toLowerCase();
        const filename = (rec.metadata?.original_filename as string) || "";
        return (
            filename.toLowerCase().includes(q) ||
            rec.transcript.toLowerCase().includes(q) ||
            rec.recording_id.toLowerCase().includes(q)
        );
    });

    if (isLoading && recordings.length === 0) {
        return (
            <div className="space-y-4">
                {[1, 2, 3].map((i) => (
                    <div key={i} className="flex items-center justify-between p-4 border rounded-lg">
                        <div className="space-y-2 flex-1">
                            <Skeleton className="h-4 w-48" />
                            <Skeleton className="h-3 w-64" />
                        </div>
                        <Skeleton className="h-8 w-24" />
                    </div>
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive">
                {error}
            </div>
        );
    }

    return (
        <div className="space-y-4">
            {/* Filter */}
            <div className="max-w-xs">
                <label className="text-xs text-muted-foreground mb-1 block">Voice Agent</label>
                <Select value={selectedWorkflow} onValueChange={setSelectedWorkflow}>
                    <SelectTrigger className="h-9 text-sm">
                        <SelectValue placeholder="All agents" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL_VALUE}>All agents</SelectItem>
                        {workflows.map((w) => (
                            <SelectItem key={w.id} value={String(w.id)}>
                                {w.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>

            {/* Search and Refresh */}
            <div className="flex items-center gap-4">
                <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search by filename, transcript, or ID..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="pl-10"
                    />
                </div>
                <Button
                    variant="outline"
                    size="icon"
                    onClick={() => { stopPlayback(); fetchRecordings(); }}
                    disabled={isLoading}
                >
                    <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                </Button>
            </div>

            {/* Results count */}
            <div className="text-sm text-muted-foreground">
                {filteredRecordings.length} recording{filteredRecordings.length !== 1 ? "s" : ""}
                {searchQuery && ` matching "${searchQuery}"`}
            </div>

            {/* Recordings List */}
            {filteredRecordings.length === 0 ? (
                <div className="text-center py-12">
                    <AudioLines className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
                    <p className="text-muted-foreground">
                        {searchQuery
                            ? "No recordings match your search"
                            : "No recordings found for the selected filters"}
                    </p>
                </div>
            ) : (
                <div className="space-y-3">
                    {filteredRecordings.map((rec) => {
                        const filename = (rec.metadata?.original_filename as string) || "";
                        const workflowName = workflowMap.get(rec.workflow_id);
                        const isEditing = editingId === rec.recording_id;

                        return (
                            <div
                                key={rec.recording_id}
                                className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors"
                            >
                                <div className="flex items-center gap-4 flex-1 min-w-0">
                                    <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                                        <AudioLines className="w-5 h-5 text-primary" />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        {/* Recording ID (editable) */}
                                        <div className="flex items-center gap-2 mb-1">
                                            {isEditing ? (
                                                <div className="flex items-center gap-1">
                                                    <Input
                                                        value={editValue}
                                                        onChange={(e) => setEditValue(e.target.value)}
                                                        onKeyDown={(e) => {
                                                            if (e.key === "Enter") saveRecordingId(rec);
                                                            if (e.key === "Escape") cancelEditing();
                                                        }}
                                                        className="h-7 text-sm font-mono w-48"
                                                        maxLength={64}
                                                        autoFocus
                                                    />
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-7 w-7 p-0"
                                                        onClick={() => saveRecordingId(rec)}
                                                    >
                                                        <Check className="w-3.5 h-3.5" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-7 w-7 p-0"
                                                        onClick={cancelEditing}
                                                    >
                                                        <X className="w-3.5 h-3.5" />
                                                    </Button>
                                                </div>
                                            ) : (
                                                <div className="flex items-center gap-1.5 group">
                                                    <code className="text-sm font-mono bg-muted px-1.5 py-0.5 rounded truncate max-w-[250px]">
                                                        {rec.recording_id}
                                                    </code>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
                                                        onClick={() => startEditing(rec)}
                                                    >
                                                        <Pencil className="w-3 h-3" />
                                                    </Button>
                                                </div>
                                            )}
                                            {workflowName && (
                                                <Badge variant="outline" className="text-xs shrink-0">
                                                    {workflowName}
                                                </Badge>
                                            )}
                                        </div>
                                        {/* Filename */}
                                        {filename && (
                                            <p className="text-xs text-muted-foreground mb-0.5 truncate max-w-[300px]">
                                                {filename}
                                            </p>
                                        )}
                                        {/* Transcript */}
                                        <p className="text-sm text-muted-foreground line-clamp-1 mb-1">
                                            {rec.transcript}
                                        </p>
                                        <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
                                            <span>{rec.tts_provider}</span>
                                            <span>{rec.tts_model}</span>
                                            <span className="truncate max-w-[150px]">{rec.tts_voice_id}</span>
                                            <span>{formatDate(rec.created_at)}</span>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex items-center gap-1 shrink-0 ml-2">
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => handlePlay(rec)}
                                    >
                                        {playingId === rec.recording_id ? (
                                            <Pause className="w-4 h-4" />
                                        ) : (
                                            <Play className="w-4 h-4" />
                                        )}
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => handleDelete(rec.recording_id)}
                                        className="text-destructive hover:text-destructive/90"
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
