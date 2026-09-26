'use client';

import { Headphones, Loader2 } from 'lucide-react';
import posthog from 'posthog-js';
import { useCallback, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { PostHogEvent } from '@/constants/posthog-events';
import { getCallReplay } from '@/lib/callHistory';
import { downloadFile, getSignedUrl } from '@/lib/files';

export function MediaPreviewDialog() {
    const [isOpen, setIsOpen] = useState(false);
    const [audioSignedUrl, setAudioSignedUrl] = useState<string | null>(null);
    const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [recordingKey, setRecordingKey] = useState<string | null>(null);
    const [transcriptKey, setTranscriptKey] = useState<string | null>(null);
    const [mediaLoading, setMediaLoading] = useState(false);

    const openPreview = useCallback(
        async (recordingUrl: string | null, transcriptUrl: string | null, runId: number, callId?: string | null) => {
            if (!recordingUrl && !transcriptUrl && !callId) return;
            setMediaLoading(true);
            setAudioSignedUrl(null);
            setTranscriptContent(null);
            setRecordingKey(callId ? null : recordingUrl);
            setTranscriptKey(transcriptUrl);
            setSelectedRunId(runId);
            setIsOpen(true);

            if (callId) {
                try {
                    const replay = await getCallReplay(callId);
                    const fallbackRecording = replay.recordings.find((item) => item.track === 'mixed')
                        ?? replay.recordings[0];
                    const transcript = replay.transcript ?? replay.utterances
                        .map((item) => `${item.speaker}: ${item.transcript}`)
                        .join('\n');
                    setAudioSignedUrl(replay.recording_signed_url ?? fallbackRecording?.signed_url ?? null);
                    setTranscriptContent(transcript || null);
                    posthog.capture(PostHogEvent.TRANSCRIPT_VIEWED, {
                        run_id: runId,
                        source: 'call_replay',
                        transcript_length: transcript.length,
                    });
                } catch (error) {
                    console.error('Error loading call replay:', error);
                } finally {
                    setMediaLoading(false);
                }
                return;
            }

            const [audioResult, transcriptResult] = await Promise.all([
                // Request an inline, typed response. Historic recordings may
                // have been stored as application/octet-stream; Safari does
                // not reliably preview those WAV bytes in an <audio> element.
                recordingUrl ? getSignedUrl(recordingUrl, true) : null,
                transcriptUrl ? getSignedUrl(transcriptUrl, true) : null,
            ]);

            if (audioResult) {
                setAudioSignedUrl(audioResult);
            }

            if (transcriptResult) {
                try {
                    const response = await fetch(transcriptResult);
                    const text = await response.text();
                    setTranscriptContent(text);
                    posthog.capture(PostHogEvent.TRANSCRIPT_VIEWED, {
                        run_id: runId,
                        source: 'media_preview_dialog',
                        transcript_length: text.length,
                    });
                } catch (error) {
                    console.error('Error fetching transcript:', error);
                }
            }

            setMediaLoading(false);
        },
        [],
    );

    return {
        openPreview,
        dialog: (
            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            Run Preview
                            {selectedRunId && ` - Run #${selectedRunId}`}
                        </DialogTitle>
                    </DialogHeader>

                    {mediaLoading && (
                        <div className="flex items-center justify-center py-8 space-x-2">
                            <Loader2 className="h-6 w-6 animate-spin" />
                            <span>Loading...</span>
                        </div>
                    )}

                    {!mediaLoading && audioSignedUrl && (
                        <audio
                            src={audioSignedUrl}
                            controls
                            autoPlay
                            className="w-full mt-4"
                            onPlay={() => posthog.capture(PostHogEvent.RECORDING_PLAYED, {
                                run_id: selectedRunId,
                                source: 'media_preview_dialog',
                            })}
                        />
                    )}

                    {!mediaLoading && transcriptContent && (
                        <pre className="w-full h-[60vh] overflow-auto border rounded-md mt-4 p-4 bg-muted text-sm whitespace-pre-wrap font-mono">
                            {transcriptContent}
                        </pre>
                    )}

                    {!mediaLoading && !audioSignedUrl && !transcriptContent && (
                        <div className="flex items-center justify-center py-8 text-muted-foreground">
                            No recording or transcript available.
                        </div>
                    )}

                    <DialogFooter className="pt-4">
                        <DialogClose asChild>
                            <Button variant="secondary">Close</Button>
                        </DialogClose>
                        <div className="flex gap-2">
                            {recordingKey && (
                                <Button variant="outline" onClick={() => downloadFile(recordingKey)}>
                                    Download Recording
                                </Button>
                            )}
                            {transcriptKey && (
                                <Button variant="outline" onClick={() => downloadFile(transcriptKey)}>
                                    Download Transcript
                                </Button>
                            )}
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        ),
    };
}

interface MediaPreviewButtonProps {
    recordingUrl: string | null | undefined;
    transcriptUrl: string | null | undefined;
    runId: number;
    onOpenPreview: (recordingUrl: string | null, transcriptUrl: string | null, runId: number, callId?: string | null) => void;
    callId?: string | null;
    onSelect?: (runId: number) => void;
}

export function MediaPreviewButton({
    recordingUrl,
    transcriptUrl,
    runId,
    onOpenPreview,
    onSelect,
    callId,
}: MediaPreviewButtonProps) {
    if (!recordingUrl && !transcriptUrl && !callId) return null;

    const handleOpen = () => {
        onSelect?.(runId);
        onOpenPreview(recordingUrl ?? null, transcriptUrl ?? null, runId, callId);
    };

    return (
        <Button
            variant="outline"
            size="icon"
            aria-label="Preview recording and transcript"
            title="Preview recording and transcript"
            onClick={handleOpen}
        >
            <Headphones className="h-4 w-4" />
        </Button>
    );
}
