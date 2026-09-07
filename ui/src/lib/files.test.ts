import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getSignedUrlApiV1S3SignedUrlGet } from '@/client/sdk.gen';

import { downloadFile } from './files';

vi.mock('@/client/sdk.gen', () => ({
    getSignedUrlApiV1S3SignedUrlGet: vi.fn(),
}));

describe('downloadFile', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('opens a download target before awaiting the signed URL', async () => {
        const target = { closed: false, location: { href: '' }, close: vi.fn() };
        const open = vi.spyOn(window, 'open').mockReturnValue(target as unknown as Window);
        vi.mocked(getSignedUrlApiV1S3SignedUrlGet).mockResolvedValue({
            data: { url: 'https://storage.example.test/recording.wav' },
        } as never);

        await downloadFile('recordings/run/call.wav');

        expect(open).toHaveBeenCalledWith('about:blank', '_blank');
        expect(target.location.href).toBe('https://storage.example.test/recording.wav');
    });
});
