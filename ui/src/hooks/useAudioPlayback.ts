import { useCallback, useRef, useState } from "react";

import { getSignedUrlApiV1S3SignedUrlGet } from "@/client/sdk.gen";

/**
 * Hook for playing audio files stored in S3/MinIO via signed URLs.
 *
 * Returns the currently-playing ID (or null), a toggle function, and a stop function.
 */
export function useAudioPlayback() {
    const [playingId, setPlayingId] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    const stop = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause();
            audioRef.current = null;
        }
        setPlayingId(null);
    }, []);

    const toggle = useCallback(
        async (id: string, storageKey: string, storageBackend?: string) => {
            // If already playing this id, stop it
            if (audioRef.current && playingId === id) {
                stop();
                return;
            }

            // Stop any previous playback
            stop();

            const result = await getSignedUrlApiV1S3SignedUrlGet({
                query: {
                    key: storageKey,
                    storage_backend: storageBackend,
                },
            });

            if (!result.data?.url) {
                throw new Error("Failed to get audio URL");
            }

            const audio = new Audio(result.data.url);
            audio.onended = () => {
                audioRef.current = null;
                setPlayingId(null);
            };
            audioRef.current = audio;
            setPlayingId(id);
            await audio.play();
        },
        [playingId, stop],
    );

    return { playingId, toggle, stop } as const;
}
