'use client';

import { Loader2 } from 'lucide-react';
import React, { createContext, lazy, Suspense, useContext, useEffect, useMemo, useState } from 'react';

import { createAuthService, IAuthService, StackAuthService } from '../services';
import type { AuthUser } from '../types';

interface AuthContextType {
  service: IAuthService;
  user: AuthUser | null;             // Union type: CurrentUser | LocalUser
  isAuthenticated: boolean;
  loading: boolean;
  getAccessToken: () => Promise<string>;
  provider: string;
}

const AuthContext = createContext<AuthContextType | null>(null);

interface AuthContextProviderProps {
  service: IAuthService;
  children: React.ReactNode;
}

// Lazy load Stack components only when needed
const StackProviderWrapper = lazy(() =>
  import('./StackProviderWrapper').then(module => ({
    default: module.StackProviderWrapper
  }))
);

// Generic context provider for non-Stack providers
function GenericAuthContextProvider({ service, children }: AuthContextProviderProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Fetch current user
    const fetchUser = async () => {
      setLoading(true);
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

  const contextValue: AuthContextType = React.useMemo(() => ({
    service,
    user,
    isAuthenticated: service.isAuthenticated(),
    loading,
    getAccessToken,
    provider: service.getProviderName(),
  }), [service, user, loading, getAccessToken]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const authProvider = process.env.NEXT_PUBLIC_AUTH_PROVIDER || 'stack';
  const authService = useMemo(() => createAuthService(authProvider), [authProvider]);

  // For Stack provider, wrap with StackProvider and use Stack-specific context
  if (authProvider === 'stack' && authService instanceof StackAuthService) {
    return (
      <Suspense fallback={
        <div className="flex items-center justify-center min-h-screen">
          <Loader2 className="w-8 h-8 animate-spin" />
        </div>
      }>
        <StackProviderWrapper service={authService}>
          {children}
        </StackProviderWrapper>
      </Suspense>
    );
  }

  // For other providers, use generic context provider
  return (
    <GenericAuthContextProvider service={authService}>
      {children}
    </GenericAuthContextProvider>
  );
}

// Export the context for Stack-specific provider
export { AuthContext };

// Stack-specific context provider that uses the useUser hook
export function StackAuthContextProvider({ service, children }: AuthContextProviderProps) {
  const [loading, setLoading] = useState(true);
  const stackUser: AuthUser | null = null;

  useEffect(() => {
    // For Stack provider, we'll get the user from the StackProviderWrapper
    // This is a placeholder that will be overridden by the actual implementation
    if (service instanceof StackAuthService) {
      setLoading(false);
    }
  }, [service]);

  const getAccessToken = React.useCallback(() => service.getAccessToken(), [service]);

  const contextValue: AuthContextType = React.useMemo(() => ({
    service,
    user: stackUser,
    isAuthenticated: service.isAuthenticated(),
    loading,
    getAccessToken,
    provider: service.getProviderName(),
  }), [service, loading, getAccessToken]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuthContext() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuthContext must be used within AuthProvider');
  }
  return context;
}
