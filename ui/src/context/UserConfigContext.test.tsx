import { describe, it, expect } from 'vitest';
import { UserConfigProvider, useUserConfig } from './UserConfigContext';

describe('UserConfigContext', () => {
  it('exports UserConfigProvider component', () => {
    expect(UserConfigProvider).toBeDefined();
    expect(typeof UserConfigProvider).toBe('function');
  });

  it('exports useUserConfig hook', () => {
    expect(useUserConfig).toBeDefined();
    expect(typeof useUserConfig).toBe('function');
  });
});

describe('Other Contexts', () => {
  it('AppConfigContext exports provider and hook', async () => {
    const module = await import('./AppConfigContext');
    expect(module.AppConfigProvider).toBeDefined();
    expect(module.useAppConfig).toBeDefined();
  });

  it('OnboardingContext exports provider and hook', async () => {
    const module = await import('./OnboardingContext');
    expect(module.OnboardingProvider).toBeDefined();
    expect(module.useOnboarding).toBeDefined();
  });

  it('UnsavedChangesContext exports provider and hook', async () => {
    const module = await import('./UnsavedChangesContext');
    expect(module.UnsavedChangesProvider).toBeDefined();
    expect(module.useUnsavedChanges).toBeDefined();
  });

  it('TelephonyConfigWarningsContext exports provider and hook', async () => {
    const module = await import('./TelephonyConfigWarningsContext');
    expect(module.TelephonyConfigWarningsProvider).toBeDefined();
    expect(module.useTelephonyConfigWarnings).toBeDefined();
  });
});