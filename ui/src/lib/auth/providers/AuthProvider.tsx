'use client';

import { Loader2 } from 'lucide-react';
import React, { createContext, lazy, Suspense, useContext, useEffect, useState } from 'react';

import logger from '@/lib/logger';

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

interface ResolvedAuthConfig {
  provider: string;
  // Public Stack client config, fetched from the backend at runtime. Null unless
  // the provider is 'stack' and the backend supplied both values.
  stack: { projectId: string; publishableClientKey: string } | null;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<ResolvedAuthConfig | null>(null);

  useEffect(() => {
    fetch('/api/config/auth')
      .then((res) => res.json())
      .then((data) => {
        logger.debug(`Setting auth provider as ${data.provider}`)
        setConfig({
          provider: data.provider || 'local',
          stack:
            data.stackProjectId && data.stackPublishableClientKey
              ? {
                  projectId: data.stackProjectId,
                  publishableClientKey: data.stackPublishableClientKey,
                }
              : null,
        })
      })
      .catch((e) => {
        logger.error(`Got error ${e} while setting auth provider`)
        setConfig({ provider: 'local', stack: null })
      });
  }, []);

  if (!config) {
    return LoadingFallback;
  }

  // For Stack provider, use the dedicated wrapper
  if (config.provider === 'stack') {
    if (!config.stack) {
      logger.error(
        'Auth provider is "stack" but the backend returned no Stack client config. ' +
        'Ensure STACK_AUTH_PROJECT_ID and STACK_PUBLISHABLE_CLIENT_KEY are set on the API service.'
      );
      return LoadingFallback;
    }
    return (
      <Suspense fallback={LoadingFallback}>
        <StackProviderWrapper
          projectId={config.stack.projectId}
          publishableClientKey={config.stack.publishableClientKey}
        >
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
