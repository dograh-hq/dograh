import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { PhoneCallDialog } from './PhoneCallDialog';

const mocks = vi.hoisted(() => ({ call: vi.fn(), dismissed: false, markAction: vi.fn() }));
vi.mock('react-international-phone/style.css', () => ({}));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('posthog-js', () => ({ default: { capture: vi.fn() } }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: '1' }, loading: false }) }));
vi.mock('@/context/AppConfigContext', () => ({ useAppConfig: () => ({ config: { deploymentMode: 'oss' } }) }));
vi.mock('@/context/DograhAccountContext', () => ({ useDograhAccount: () => ({ status: 'missing', refresh: vi.fn() }) }));
vi.mock('@/context/UserConfigContext', () => ({ useUserConfig: () => ({ refreshConfig: vi.fn() }) }));
vi.mock('@/context/OnboardingContext', () => ({ useOnboarding: () => ({ loading: false, hasCompletedAction: () => mocks.dismissed, markActionCompleted: mocks.markAction }) }));
vi.mock('@/components/model-connections/DograhAccountConnect', () => ({
    DograhAccountConnectFlow: ({ onConnected }: { onConnected: () => Promise<void> }) => <button onClick={() => void onConnected()}>Approve on Dograh</button>,
}));
vi.mock('@/client/sdk.gen', () => ({
    getPreferencesApiV1OrganizationsPreferencesGet: async () => ({ data: { test_phone_number: 'SIP/test' } }),
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet: async () => ({ data: { configurations: [{ id: 3, name: 'SIP trunk', provider: 'sip', is_ready_for_outbound: true }] } }),
    listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet: async () => ({ data: { phone_numbers: [] } }),
    initiateCallApiV1TelephonyInitiateCallPost: mocks.call,
}));

beforeEach(() => {
    vi.clearAllMocks();
    mocks.dismissed = false;
    mocks.call.mockResolvedValue({ data: { message: 'Call initiated' } });
});

it('waits for Start Call before showing model setup and preserves the destination', async () => {
    render(<PhoneCallDialog open onOpenChange={vi.fn()} workflowId={41} user={{ id: '1' }} />);
    const start = await screen.findByRole('button', { name: 'Start Call' });
    expect(screen.queryByRole('dialog', { name: 'Set up AI models' })).toBeNull();
    fireEvent.click(start);
    expect(await screen.findByRole('dialog', { name: 'Set up AI models' })).toBeTruthy();
    expect(mocks.call).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Approve on Dograh'));
    fireEvent.click(screen.getByText('Continue'));
    await waitFor(() => expect(mocks.call).toHaveBeenCalledOnce());
    expect(mocks.call).toHaveBeenCalledWith({ body: { workflow_id: 41, phone_number: 'SIP/test', telephony_configuration_id: 3, from_phone_number_id: null } });
});

it('allows a phone call using existing providers when setup is skipped', async () => {
    render(<PhoneCallDialog open onOpenChange={vi.fn()} workflowId={41} user={{ id: '1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start Call' }));
    fireEvent.click(await screen.findByText('Skip for now'));
    await waitFor(() => expect(mocks.call).toHaveBeenCalledOnce());
    expect(mocks.markAction).toHaveBeenCalledWith('model_setup_dismissed');
});
