'use client';

import { Headphones, Loader2 } from 'lucide-react';
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
import { downloadFile, getSignedUrl } from '@/lib/files';

interface MediaPreviewDialogProps {
    accessToken: string | null;
}

export function MediaPreviewDialog({ accessToken }: MediaPreviewDialogProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [audioSignedUrl, setAudioSignedUrl] = useState<string | null>(null);
    const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [recordingKey, setRecordingKey] = useState<string | null>(null);
    const [transcriptKey, setTranscriptKey] = useState<string | null>(null);
    const [mediaLoading, setMediaLoading] = useState(false);

    const openPreview = useCallback(
        async (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => {
            if (!accessToken || (!recordingUrl && !transcriptUrl)) return;
            setMediaLoading(true);
            setAudioSignedUrl(null);
            setTranscriptContent(null);
            setRecordingKey(recordingUrl);
            setTranscriptKey(transcriptUrl);
            setSelectedRunId(runId);
            setIsOpen(true);

            const [audioResult, transcriptResult] = await Promise.all([
                recordingUrl ? getSignedUrl(recordingUrl, accessToken) : null,
                transcriptUrl ? getSignedUrl(transcriptUrl, accessToken, true) : null,
            ]);

            if (audioResult) {
                setAudioSignedUrl(audioResult);
            }

            if (transcriptResult) {
                try {
                    const response = await fetch(transcriptResult);
                    const text = await response.text();
                    setTranscriptContent(text);
                } catch (error) {
                    console.error('Error fetching transcript:', error);
                }
            }

            setMediaLoading(false);
        },
        [accessToken],
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
                        <audio src={audioSignedUrl} controls autoPlay className="w-full mt-4" />
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
                            {recordingKey && accessToken && (
                                <Button variant="outline" onClick={() => downloadFile(recordingKey, accessToken)}>
                                    Download Recording
                                </Button>
                            )}
                            {transcriptKey && accessToken && (
                                <Button variant="outline" onClick={() => downloadFile(transcriptKey, accessToken)}>
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
    onOpenPreview: (recordingUrl: string | null, transcriptUrl: string | null, runId: number) => void;
    onSelect?: (runId: number) => void;
}

export function MediaPreviewButton({
    recordingUrl,
    transcriptUrl,
    runId,
    onOpenPreview,
    onSelect,
}: MediaPreviewButtonProps) {
    if (!recordingUrl && !transcriptUrl) return null;

    const handleOpen = () => {
        onSelect?.(runId);
        onOpenPreview(recordingUrl ?? null, transcriptUrl ?? null, runId);
    };

    return (
        <Button
            variant="outline"
            size="icon"
            onClick={handleOpen}
        >
            <Headphones className="h-4 w-4" />
        </Button>
    );
}
