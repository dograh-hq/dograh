'use client';

import { FileText, Loader2, Video } from 'lucide-react';
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
    const [mediaType, setMediaType] = useState<'audio' | 'transcript' | null>(null);
    const [mediaSignedUrl, setMediaSignedUrl] = useState<string | null>(null);
    const [transcriptContent, setTranscriptContent] = useState<string | null>(null);
    const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
    const [mediaDownloadKey, setMediaDownloadKey] = useState<string | null>(null);
    const [mediaLoading, setMediaLoading] = useState(false);

    const openAudioModal = useCallback(
        async (fileKey: string | null, runId: number) => {
            if (!fileKey || !accessToken) return;
            setMediaLoading(true);
            const signed = await getSignedUrl(fileKey, accessToken);
            if (signed) {
                setMediaType('audio');
                setMediaSignedUrl(signed);
                setMediaDownloadKey(fileKey);
                setSelectedRunId(runId);
                setIsOpen(true);
            }
            setMediaLoading(false);
        },
        [accessToken],
    );

    const openTranscriptModal = useCallback(
        async (fileKey: string | null, runId: number) => {
            if (!fileKey || !accessToken) return;
            setMediaLoading(true);
            setTranscriptContent(null);
            const signed = await getSignedUrl(fileKey, accessToken, true);
            if (signed) {
                setMediaType('transcript');
                setMediaSignedUrl(signed);
                setMediaDownloadKey(fileKey);
                setSelectedRunId(runId);
                setIsOpen(true);
                // Fetch transcript content with proper UTF-8 encoding
                try {
                    const response = await fetch(signed);
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
        openAudioModal,
        openTranscriptModal,
        dialog: (
            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>
                            {mediaType === 'audio' ? 'Recording Preview' : 'Transcript Preview'}
                            {selectedRunId && ` - Run #${selectedRunId}`}
                        </DialogTitle>
                    </DialogHeader>

                    {mediaLoading && (
                        <div className="flex items-center justify-center py-8 space-x-2">
                            <Loader2 className="h-6 w-6 animate-spin" />
                            <span>Loading...</span>
                        </div>
                    )}

                    {!mediaLoading && mediaType === 'audio' && mediaSignedUrl && (
                        <audio src={mediaSignedUrl} controls autoPlay className="w-full mt-4" />
                    )}

                    {!mediaLoading && mediaType === 'transcript' && transcriptContent && (
                        <pre className="w-full h-[60vh] overflow-auto border rounded-md mt-4 p-4 bg-muted text-sm whitespace-pre-wrap font-mono">
                            {transcriptContent}
                        </pre>
                    )}

                    <DialogFooter className="pt-4">
                        <DialogClose asChild>
                            <Button variant="secondary">Close</Button>
                        </DialogClose>
                        {mediaDownloadKey && accessToken && (
                            <Button onClick={() => downloadFile(mediaDownloadKey, accessToken)}>Download</Button>
                        )}
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        ),
    };
}

interface MediaPreviewButtonsProps {
    recordingUrl: string | null | undefined;
    transcriptUrl: string | null | undefined;
    runId: number;
    onOpenAudio: (fileKey: string | null, runId: number) => void;
    onOpenTranscript: (fileKey: string | null, runId: number) => void;
    onSelect?: (runId: number) => void;
}

export function MediaPreviewButtons({
    recordingUrl,
    transcriptUrl,
    runId,
    onOpenAudio,
    onOpenTranscript,
    onSelect,
}: MediaPreviewButtonsProps) {
    const handleOpenAudio = () => {
        onSelect?.(runId);
        onOpenAudio(recordingUrl ?? null, runId);
    };

    const handleOpenTranscript = () => {
        onSelect?.(runId);
        onOpenTranscript(transcriptUrl ?? null, runId);
    };

    return (
        <div className="flex space-x-2">
            {recordingUrl && (
                <Button
                    variant="outline"
                    size="icon"
                    onClick={handleOpenAudio}
                >
                    <Video className="h-4 w-4" />
                </Button>
            )}
            {transcriptUrl && (
                <Button
                    variant="outline"
                    size="icon"
                    onClick={handleOpenTranscript}
                >
                    <FileText className="h-4 w-4" />
                </Button>
            )}
        </div>
    );
}
