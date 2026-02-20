'use client';

import { Loader2 } from 'lucide-react';
import React, { createContext, lazy, Suspense, useContext, useEffect, useState } from 'react';

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

// Lazy load provider wrappers only when needed
const StackProviderWrapper = lazy(() =>
  import('./StackProviderWrapper').then(module => ({
    default: module.StackProviderWrapper
  }))
);

const LocalProviderWrapper = lazy(() =>
  import('./LocalProviderWrapper').then(module => ({
    default: module.LocalProviderWrapper
  }))
);

const LoadingFallback = (
  <div className="flex items-center justify-center min-h-screen">
    <Loader2 className="w-8 h-8 animate-spin" />
  </div>
);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authProvider, setAuthProvider] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/config/auth')
      .then((res) => res.json())
      .then((data) => setAuthProvider(data.provider || 'stack'))
      .catch(() => setAuthProvider('local'));
  }, []);

  if (!authProvider) {
    return LoadingFallback;
  }

  // For Stack provider, use the dedicated wrapper
  if (authProvider === 'stack') {
    return (
      <Suspense fallback={LoadingFallback}>
        <StackProviderWrapper>
          {children}
        </StackProviderWrapper>
      </Suspense>
    );
  }

  // For local/OSS provider
  return (
    <Suspense fallback={LoadingFallback}>
      <LocalProviderWrapper>
        {children}
      </LocalProviderWrapper>
    </Suspense>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
