import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

import type { CampaignResponse } from '@/client/types.gen';

import CampaignTrafficStats from './CampaignTrafficStats';

const mocks = vi.hoisted(() => ({ stats: vi.fn(), user: { id: '1' } }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: mocks.user, loading: false }) }));
vi.mock('@/client/sdk.gen', () => ({ getCampaignTrafficStatsApiV1CampaignCampaignIdTrafficStatsGet: mocks.stats }));

it('links pinned variants and executed versions by version number, and latest variants to published', async () => {
    mocks.stats.mockResolvedValue({ data: { total_attempts: 2, variants: [
        { id: '12:103', workflow_id: 12, workflow_name: 'Pinned agent', workflow_definition_id: 103, version_number: 3, target_weight: 50, attempts: 1, completed: 1 },
        { id: '12:latest', workflow_id: 12, workflow_name: 'Latest agent', workflow_definition_id: null, target_weight: 50, attempts: 1, completed: 1, definitions: [{ definition_id: 102, version_number: 2, attempts: 1 }] },
    ] } });
    render(<CampaignTrafficStats campaign={{ id: 54, state: 'completed' } as CampaignResponse} />);
    expect((await screen.findByRole('link', { name: 'Pinned agent' })).getAttribute('href')).toBe('/workflow/12?version=3');
    expect(screen.getByRole('link', { name: 'Latest agent' }).getAttribute('href')).toBe('/workflow/12?version=latest');
    expect(screen.getByRole('link', { name: 'v2' }).getAttribute('href')).toBe('/workflow/12?version=2');
});
