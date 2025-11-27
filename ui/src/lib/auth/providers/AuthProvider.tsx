'use client';

import { Loader2 } from 'lucide-react';
import React, { createContext, lazy, Suspense, useContext, useEffect, useMemo, useState } from 'react';

import { createAuthService } from '../services';
import type { AuthUser } from '../types';

// Shared context type for both Stack and Local providers
export interface AuthContextType {
  user: AuthUser | null;
  isAuthenticated: boolean;
  loading: boolean;
  getAccessToken: () => Promise<string>;
  redirectToLogin: () => void;
  logout: () => Promise<void>;
  provider: string;
  // Stack-specific (optional)
  getSelectedTeam?: () => unknown;
  listPermissions?: (team?: unknown) => Promise<Array<{ id: string }>>;
}

export const AuthContext = createContext<AuthContextType | null>(null);

// Lazy load Stack components only when needed
const StackProviderWrapper = lazy(() =>
  import('./StackProviderWrapper').then(module => ({
    default: module.StackProviderWrapper
  }))
);

// Generic context provider for non-Stack providers (local/OSS)
function LocalAuthContextProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const service = useMemo(() => createAuthService('local'), []);

  useEffect(() => {
    const fetchUser = async () => {
      try {
        const currentUser = await service.getCurrentUser();
        setUser(currentUser);
      } catch (error) {
        console.error('Failed to fetch user:', error);
        setUser(null);
      } finally {
        setLoading(false);
      }
    };
    fetchUser();
  }, [service]);

  const getAccessToken = React.useCallback(() => service.getAccessToken(), [service]);
  const redirectToLogin = React.useCallback(() => service.redirectToLogin(), [service]);
  const logout = React.useCallback(() => service.logout(), [service]);

  const contextValue: AuthContextType = useMemo(() => ({
    user,
    isAuthenticated: !!user,
    loading,
    getAccessToken,
    redirectToLogin,
    logout,
    provider: 'local',
  }), [user, loading, getAccessToken, redirectToLogin, logout]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';

  // For Stack provider, use the dedicated wrapper
  if (authProvider === 'stack') {
    return (
      <Suspense fallback={
        <div className="flex items-center justify-center min-h-screen">
          <Loader2 className="w-8 h-8 animate-spin" />
        </div>
      }>
        <StackProviderWrapper>
          {children}
        </StackProviderWrapper>
      </Suspense>
    );
  }

  // For local/OSS provider
  return (
    <LocalAuthContextProvider>
      {children}
    </LocalAuthContextProvider>
  );
}

export function useAuthContext() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthContext must be used within AuthProvider');
  }
  return context;
}
