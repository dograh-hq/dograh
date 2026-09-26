import { getSignedUrlApiV1S3SignedUrlGet } from "@/client/sdk.gen";

/**
 * Get a signed URL and download a file
 */
export async function downloadFile(url: string | null) {
    if (!url) return;

    // Open a harmless target synchronously while this click still counts as a
    // user gesture. Safari can block a new window opened only after awaiting
    // the authenticated signed-URL request.
    const target = window.open("about:blank", "_blank");
    try {
        const response = await getSignedUrlApiV1S3SignedUrlGet({
            query: {
                key: url
            },
        });

        if (response.error || !response.data?.url) {
            target?.close();
            return;
        }
        // Artifact signed URLs now use Content-Disposition: attachment. A
        // pre-opened target preserves Safari's user-gesture requirement while
        // letting the storage response control the download filename/type.
        if (target && !target.closed) target.location.href = response.data.url;
        else window.location.assign(response.data.url);
    } catch (error) {
        target?.close();
        console.error('Error downloading file:', error);
    }
}

/**
 * Return a signed URL for a given S3 key without triggering a download.
 * Useful for previewing media (audio or transcript) in-browser first.
 */
export async function getSignedUrl(url: string | null, inline: boolean = false): Promise<string | null> {
    if (!url) return null;

    try {
        const response = await getSignedUrlApiV1S3SignedUrlGet({
            query: {
                key: url,
                inline: inline,
            },
        });

        if (response.data?.url) {
            return response.data.url as string;
        }
    } catch (error) {
        console.error('Error getting signed URL:', error);
    }
    return null;
}
