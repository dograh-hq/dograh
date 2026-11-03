import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RealtimeFeedbackEvent } from '@/components/workflow/conversation/types';

import WorkflowRunPage from './page';

const mocks = vi.hoisted(() => ({
    auth: { isAuthenticated: true, loading: false },
    toolResults: [] as unknown[],
    events: [] as RealtimeFeedbackEvent[],
}));

vi.mock('next/navigation', () => ({ useParams: () => ({ workflowId: '12', runId: '34' }) }));
vi.mock('@/lib/auth', () => ({ useAuth: () => mocks.auth }));
vi.mock('posthog-js', () => ({ default: { capture: vi.fn() } }));
vi.mock('@/hooks/useOrganizationTimezone', () => ({ useOrganizationTimezone: () => 'UTC' }));
vi.mock('@/components/MediaPreviewDialog', () => ({
    MediaPreviewDialog: () => ({ openPreview: vi.fn(), dialog: null }),
    MediaPreviewButton: () => null,
}));
vi.mock('@/components/onboarding/OnboardingTooltip', () => ({ OnboardingTooltip: () => null }));
vi.mock('@/components/workflow/conversation', async (importOriginal) => ({
    ...await importOriginal<typeof import('@/components/workflow/conversation')>(),
    ConversationRailFrame: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('@/lib/files', () => ({
    getSignedUrl: async (url: string) => url,
    downloadFile: vi.fn(),
}));
vi.mock('@/client/sdk.gen', () => ({
    getWorkflowApiV1WorkflowFetchWorkflowIdGet: async () => ({ data: { name: 'Test agent' } }),
    getWorkflowRunApiV1WorkflowWorkflowIdRunsRunIdGet: async () => ({
        data: {
            is_completed: true,
            user_recording_url: '/user.wav',
            bot_recording_url: '/bot.wav',
            gathered_context: { tool_results: mocks.toolResults },
            logs: { realtime_feedback_events: mocks.events },
        },
    }),
}));

beforeEach(() => {
    mocks.toolResults = [];
    mocks.events = [];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(0) }));
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
        // Native playback restarts an ended track, which would desynchronize shorter recordings.
        if (this.currentTime >= this.duration) this.currentTime = 0;
        Object.defineProperty(this, 'paused', { configurable: true, value: false });
        return Promise.resolve();
    });
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
        Object.defineProperty(this, 'paused', { configurable: true, value: true });
    });
});

it('shows the recorded timeout for call 2796 instead of leaving the tool running', async () => {
    mocks.events = [{
        type: 'rtf-function-call-start',
        payload: { function_name: 'long_wait_http_tool', tool_call_id: 'call-2796', arguments: {} },
        timestamp: '2026-10-04T23:02:06.770+00:00',
        turn: 3,
    }];
    mocks.toolResults = [{
        function_name: 'long_wait_http_tool',
        tool_call_id: 'call-2796',
        status: 'timeout',
        result: { status: 'error', error: 'Tool execution timed out' },
    }];
    render(<WorkflowRunPage />);
    await screen.findByText('Timeout');
    expect(screen.queryByText('Running')).toBeNull();
    expect(screen.getByText('Tool Calls').parentElement?.textContent).toBe('Tool Calls1');
    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(screen.getAllByText(/Tool execution timed out/).length).toBeGreaterThan(0);
});

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

async function renderPlayer() {
    const view = render(<WorkflowRunPage />);
    await screen.findByRole('button', { name: 'Play split tracks' });
    const [user, bot] = Array.from(view.container.querySelectorAll('audio'));
    await waitFor(() => expect(bot.getAttribute('src')).toBe('/bot.wav'));
    return { user, bot };
}

function loadMetadata(audio: HTMLAudioElement, duration: number) {
    Object.defineProperty(audio, 'duration', { configurable: true, value: duration });
    fireEvent.loadedMetadata(audio);
}

function seek(time: number) {
    fireEvent.change(screen.getByRole('slider', { name: 'Playback position' }), {
        target: { value: String(time) },
    });
}

async function clickPlayback(name: string) {
    await act(async () => fireEvent.click(screen.getByRole('button', { name })));
}

// Leaves the next play() pending; the returned callback rejects it the way browsers do once a pause() interrupts it.
function holdNextPlay() {
    let interrupt = () => {};
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementationOnce(function (this: HTMLMediaElement) {
        Object.defineProperty(this, 'paused', { configurable: true, value: false });
        return new Promise<void>((_, reject) => {
            interrupt = () => reject(new DOMException('The play() request was interrupted by a call to pause().', 'AbortError'));
        });
    });
    return () => act(async () => interrupt());
}

describe('split track seeking', () => {
    it('waits for both track durations, then skips and scrubs both tracks while paused', async () => {
        const { user, bot } = await renderPlayer();
        const slider = screen.getByRole('slider') as HTMLInputElement;
        expect(slider.disabled).toBe(true);
        loadMetadata(user, 65);
        expect(slider.disabled).toBe(true);
        loadMetadata(bot, 65);
        expect(slider.disabled).toBe(false);

        await clickPlayback('Skip forward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([10, 10]);
        expect(slider.getAttribute('aria-valuetext')).toBe('0:10 of 1:05');

        seek(4);
        await clickPlayback('Skip backward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([0, 0]);
        expect((screen.getByRole('button', { name: 'Skip backward 10 seconds' }) as HTMLButtonElement).disabled).toBe(true);
        expect(user.play).not.toHaveBeenCalled();

        seek(62);
        await clickPlayback('Skip forward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([65, 65]);
        expect((screen.getByRole('button', { name: 'Skip forward 10 seconds' }) as HTMLButtonElement).disabled).toBe(true);
        await clickPlayback('Play split tracks');
        expect([user.currentTime, bot.currentTime]).toEqual([0, 0]);
    });

    it('keeps playback running when seeking and stops when skipping to the end', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 25);
        loadMetadata(bot, 25);
        await clickPlayback('Play split tracks');
        await clickPlayback('Skip forward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([10, 10]);
        expect([user.paused, bot.paused]).toEqual([false, false]);
        expect(screen.getByRole('button', { name: 'Pause split tracks' })).toBeTruthy();

        seek(22);
        await clickPlayback('Skip forward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([25, 25]);
        expect([user.paused, bot.paused]).toEqual([true, true]);
        expect(screen.getByRole('button', { name: 'Play split tracks' })).toBeTruthy();

        await clickPlayback('Skip backward 10 seconds');
        await clickPlayback('Play split tracks');
        expect([user.currentTime, bot.currentTime]).toEqual([15, 15]);
    });

    it('resumes an ended shorter track when seeking back without restarting it past its end', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 20);
        loadMetadata(bot, 40);
        await clickPlayback('Play split tracks');
        seek(25);
        expect([user.currentTime, bot.currentTime]).toEqual([20, 25]);
        expect([user.paused, bot.paused]).toEqual([true, false]);

        await clickPlayback('Pause split tracks');
        await clickPlayback('Play split tracks');
        expect([user.currentTime, bot.currentTime]).toEqual([20, 25]);
        expect(user.paused).toBe(true);

        await clickPlayback('Skip backward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([15, 15]);
        expect([user.paused, bot.paused]).toEqual([false, false]);
    });

    it('preserves the seek position when switching between solo and combined playback', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 50);
        loadMetadata(bot, 70);
        await clickPlayback('Play user track only');
        seek(30);
        await clickPlayback('Play user track');
        await clickPlayback('Skip backward 10 seconds');
        expect([user.currentTime, bot.currentTime]).toEqual([20, 20]);
        expect([user.paused, bot.paused]).toEqual([false, true]);
        expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('50');

        await clickPlayback('Play both tracks');
        expect([user.currentTime, bot.currentTime]).toEqual([20, 20]);
        expect([user.paused, bot.paused]).toEqual([false, false]);
        expect((screen.getByRole('slider') as HTMLInputElement).max).toBe('70');
    });

    it('keeps playing when a later seek interrupts the pending resume of a shorter track', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 20);
        loadMetadata(bot, 40);
        await clickPlayback('Play split tracks');
        seek(25);
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const interruptPlay = holdNextPlay();

        seek(10);
        seek(30);
        await interruptPlay();
        expect([user.paused, bot.paused]).toEqual([true, false]);
        expect(screen.getByRole('button', { name: 'Pause split tracks' })).toBeTruthy();
        expect(consoleError).not.toHaveBeenCalled();
    });

    it('keeps the latest track selection playing when it interrupts a pending switch', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 50);
        loadMetadata(bot, 70);
        await clickPlayback('Play split tracks');
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const interruptPlay = holdNextPlay();

        await clickPlayback('Play user track only');
        await clickPlayback('Play both tracks');
        await interruptPlay();
        expect([user.paused, bot.paused]).toEqual([false, false]);
        expect(screen.getByRole('button', { name: 'Pause split tracks' })).toBeTruthy();
        expect(consoleError).not.toHaveBeenCalled();
    });

    it('starts playback when a second play click interrupts the first', async () => {
        const { user, bot } = await renderPlayer();
        loadMetadata(user, 50);
        loadMetadata(bot, 70);
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        const interruptPlay = holdNextPlay();

        await clickPlayback('Play split tracks');
        await clickPlayback('Play split tracks');
        await interruptPlay();
        expect([user.paused, bot.paused]).toEqual([false, false]);
        expect(screen.getByRole('button', { name: 'Pause split tracks' })).toBeTruthy();
        expect(consoleError).not.toHaveBeenCalled();
    });
});
