import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import APIKeysPage from './page';

const state = vi.hoisted(() => ({
    auth: { user: { id: 'user' }, loading: false, getAccessToken: vi.fn(), redirectToLogin: vi.fn() },
    getApiKeys: vi.fn(),
    getServiceKeys: vi.fn(),
    createServiceKey: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => state.auth }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: { deploymentMode: 'cloud' } }) }));
vi.mock('@/hooks/useOrganizationTimezone', () => ({ useOrganizationTimezone: () => 'UTC' }));
vi.mock('@/lib/logger', () => ({ default: { debug: vi.fn() } }));
vi.mock('@/client/sdk.gen', () => ({
    getApiKeysApiV1UserApiKeysGet: state.getApiKeys,
    getServiceKeysApiV1UserServiceKeysGet: state.getServiceKeys,
    createServiceKeyApiV1UserServiceKeysPost: state.createServiceKey,
    createApiKeyApiV1UserApiKeysPost: vi.fn(),
    archiveApiKeyApiV1UserApiKeysApiKeyIdDelete: vi.fn(),
    archiveServiceKeyApiV1UserServiceKeysServiceKeyIdDelete: vi.fn(),
    reactivateApiKeyApiV1UserApiKeysApiKeyIdReactivatePut: vi.fn(),
}));

const verificationMessage = 'Verify your email before creating a service key';

beforeEach(() => {
    vi.resetAllMocks();
    state.auth.getAccessToken.mockResolvedValue('access-token');
    state.getApiKeys.mockResolvedValue({ data: [] });
    state.getServiceKeys.mockResolvedValue({ data: [] });
    state.createServiceKey.mockResolvedValue({ error: { detail: verificationMessage } });
});

async function openCreateDialog() {
    render(<APIKeysPage />);
    await screen.findByText('No service keys found');
    fireEvent.click(screen.getByRole('button', { name: 'Create Service Key' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Create New Service Key' }));
    fireEvent.change(dialog.getByLabelText('Service Key Name'), { target: { value: 'Production' } });
    return dialog;
}

describe('service key creation errors', () => {
    it.each([
        [{ detail: verificationMessage }, verificationMessage],
        [{ detail: [{ msg: 'Invalid key name', loc: ['body', 'name'], type: 'value_error' }] }, 'Invalid key name'],
        [{}, 'Failed to create service key'],
    ])('shows the API error inside the open dialog: %j', async (error, message) => {
        state.createServiceKey.mockResolvedValue({ error });
        const dialog = await openCreateDialog();
        fireEvent.click(dialog.getByRole('button', { name: 'Create Service Key' }));

        expect((await dialog.findByRole('alert')).textContent).toBe(message);
        expect((dialog.getByLabelText('Service Key Name') as HTMLInputElement).value).toBe('Production');
        expect(screen.queryByRole('dialog', { name: 'Service Key Created Successfully' })).toBeNull();
        expect(state.getServiceKeys).toHaveBeenCalledTimes(1);
    });

    it('clears the error when reopening the dialog', async () => {
        const dialog = await openCreateDialog();
        fireEvent.click(dialog.getByRole('button', { name: 'Create Service Key' }));
        await dialog.findByRole('alert');
        fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
        fireEvent.click(screen.getByRole('button', { name: 'Create Service Key' }));

        expect(within(screen.getByRole('dialog')).queryByRole('alert')).toBeNull();
    });

    it('allows a successful retry after an API error', async () => {
        const dialog = await openCreateDialog();
        fireEvent.click(dialog.getByRole('button', { name: 'Create Service Key' }));
        await dialog.findByRole('alert');
        state.createServiceKey.mockResolvedValue({ data: { service_key: 'mps_sk_test' } });
        fireEvent.click(dialog.getByRole('button', { name: 'Create Service Key' }));

        const success = within(await screen.findByRole('dialog', { name: 'Service Key Created Successfully' }));
        expect(success.getByText('mps_sk_test')).toBeDefined();
        expect(screen.queryByRole('alert')).toBeNull();
        expect(state.createServiceKey).toHaveBeenLastCalledWith({
            body: { name: 'Production', expires_in_days: 90 },
            headers: { Authorization: 'Bearer access-token' },
        });
    });

    it('shows network failures inside the dialog', async () => {
        const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
        try {
            state.createServiceKey.mockRejectedValue(new TypeError('Failed to fetch'));
            const dialog = await openCreateDialog();
            fireEvent.click(dialog.getByRole('button', { name: 'Create Service Key' }));
            expect((await dialog.findByRole('alert')).textContent).toBe('Failed to create service key');
        } finally {
            consoleError.mockRestore();
        }
    });
});
