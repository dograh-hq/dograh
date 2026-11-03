import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { TrafficVariantRequest } from '@/client/types.gen';

import TrafficSplitEditor, { trafficSplitError } from './TrafficSplitEditor';

const mocks = vi.hoisted(() => ({
    auth: { user: { id: 1 }, loading: true },
    agents: vi.fn(),
    versions: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => mocks.auth }));
vi.mock('@/client/sdk.gen', () => ({
    getWorkflowsSummaryApiV1WorkflowSummaryGet: mocks.agents,
    getWorkflowVersionSummariesApiV1WorkflowWorkflowIdVersionSummariesGet: mocks.versions,
}));

function Editor({ initial }: { initial: TrafficVariantRequest[] }) {
    const [variants, setVariants] = useState(initial);
    return <TrafficSplitEditor value={variants} onChange={setVariants} />;
}

beforeEach(() => {
    vi.clearAllMocks();
    mocks.auth.loading = false;
    mocks.agents.mockResolvedValue({ data: [] });
    mocks.versions.mockResolvedValue({ data: [] });
});

describe('TrafficSplitEditor', () => {
    it('waits for authentication before loading agents and versions', async () => {
        mocks.auth.loading = true;
        const initial = [{ workflow_id: 1, weight: 100 }];
        const view = render(<Editor initial={initial} />);
        expect(mocks.agents).not.toHaveBeenCalled();
        expect(mocks.versions).not.toHaveBeenCalled();
        mocks.auth.loading = false;
        view.rerender(<Editor initial={initial} />);
        await waitFor(() => expect(mocks.agents).toHaveBeenCalledTimes(1));
        expect(mocks.versions).toHaveBeenCalledWith({ path: { workflow_id: 1 } });
    });

    it('splits whole percentages evenly without losing the remainder', () => {
        render(<Editor initial={[1, 2, 3].map(workflow_id => ({ workflow_id, weight: 10 }))} />);
        fireEvent.click(screen.getByRole('button', { name: 'Split evenly' }));
        expect(screen.getAllByRole('spinbutton').map(input => (input as HTMLInputElement).value)).toEqual(['34', '33', '33']);
        expect(screen.getByText('Total: 100% / 100%')).toBeTruthy();
    });

    it('caps variants at five and retains at least one row', () => {
        render(<Editor initial={[{ workflow_id: 0, weight: 100 }]} />);
        expect((screen.getByRole('button', { name: 'Remove variant 1' }) as HTMLButtonElement).disabled).toBe(true);
        for (let i = 0; i < 4; i++) fireEvent.click(screen.getByRole('button', { name: 'Add agent' }));
        expect(screen.getAllByRole('spinbutton')).toHaveLength(5);
        expect((screen.getByRole('button', { name: 'Add agent' }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole('button', { name: 'Remove variant 3' }));
        expect(screen.getAllByRole('spinbutton')).toHaveLength(4);
    });

    it('renders API validation errors as text and offers a retry', async () => {
        mocks.agents.mockResolvedValueOnce({ error: { detail: [{ msg: 'Unavailable', loc: ['query'], type: 'value_error' }] } });
        render(<Editor initial={[{ workflow_id: 0, weight: 100 }]} />);
        await screen.findByRole('alert');
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        await waitFor(() => expect(mocks.agents).toHaveBeenCalledTimes(2));
    });

    it('allows latest and pinned for one agent but rejects duplicate pairs', () => {
        expect(trafficSplitError([{ workflow_id: 1, weight: 50 }, { workflow_id: 1, workflow_definition_id: 8, weight: 50 }])).toBeNull();
        expect(trafficSplitError([{ workflow_id: 1, weight: 50 }, { workflow_id: 1, workflow_definition_id: null, weight: 50 }])).toMatch(/different agent or version/);
    });
});
