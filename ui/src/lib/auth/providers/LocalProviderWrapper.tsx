'use client';

import React, { useMemo } from 'react';

import type { AuthUser, LocalUser } from '../types';
import { AuthContext } from './AuthProvider';

// Fixed default user for no-auth mode
const DEFAULT_USER: LocalUser = {
  id: 'default-user',
  email: 'admin@local',
  name: 'Admin',
  provider: 'local',
};

export function LocalProviderWrapper({ children }: { children: React.ReactNode }) {
  const getAccessToken = React.useCallback(async () => {
    return 'no-auth';
  }, []);

  const redirectToLogin = React.useCallback(() => {
    // No-auth mode: nothing to do
  }, []);

  const logout = React.useCallback(async () => {
    // No-auth mode: nothing to do
  }, []);

  const contextValue = useMemo(() => ({
    user: DEFAULT_USER as AuthUser,
    isAuthenticated: true,
    loading: false,
    getAccessToken,
    redirectToLogin,
    logout,
    provider: 'local' as const,
  }), [getAccessToken, redirectToLogin, logout]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}
