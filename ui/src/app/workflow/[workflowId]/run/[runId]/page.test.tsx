import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

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
vi.mock('@/app/workflow/WorkflowLayout', () => ({
    default: ({ children }: { children: ReactNode }) => children,
}));
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
