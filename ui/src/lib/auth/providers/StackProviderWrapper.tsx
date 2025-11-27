'use client';

import { StackClientApp, StackProvider, StackTheme, useUser as useStackUser } from '@stackframe/stack';
import React, { useMemo, useRef } from 'react';

import type { AuthUser } from '../types';
import { AuthContext } from './AuthProvider';

// Create a singleton StackClientApp instance to prevent multiple initializations
let stackClientAppInstance: StackClientApp<true, string> | null = null;

function getStackClientApp(): StackClientApp<true, string> {
  if (!stackClientAppInstance) {
    stackClientAppInstance = new StackClientApp({
      tokenStore: "nextjs-cookie",
      urls: {
        afterSignIn: "/after-sign-in"
      }
    });
  }
  return stackClientAppInstance;
}

interface StackProviderWrapperProps {
  children: React.ReactNode;
}

// Simple context provider that uses Stack's useUser directly
function StackAuthContextProvider({ children }: { children: React.ReactNode }) {
  const stackUser = useStackUser();

  // Store user in ref for callbacks to access latest value without creating new callbacks
  const userRef = useRef(stackUser);
  userRef.current = stackUser;

  // Derive loading state: loading if we don't have a user yet
  const isLoading = stackUser === null;

  // Stable callbacks that use ref to access current user
  const getAccessToken = React.useCallback(async () => {
    const user = userRef.current;
    if (!user) {
      throw new Error('User not authenticated');
    }
    const authJson = await user.getAuthJson();
    if (!authJson.accessToken) {
      throw new Error('No access token available');
    }
    return authJson.accessToken;
  }, []);

  const redirectToLogin = React.useCallback(() => {
    if (typeof window !== 'undefined') {
      window.location.href = '/handler/sign-in';
    }
  }, []);

  const logout = React.useCallback(async () => {
    const user = userRef.current;
    if (user?.signOut) {
      await user.signOut();
    }
  }, []);

  const getSelectedTeam = React.useCallback(() => {
    return userRef.current?.selectedTeam ?? null;
  }, []);

  const listPermissions = React.useCallback(async (team?: unknown) => {
    const user = userRef.current;
    if (!user?.listPermissions) {
      return [];
    }
    const targetTeam = team || user.selectedTeam;
    if (!targetTeam) {
      return [];
    }
    try {
      const perms = await user.listPermissions(targetTeam);
      return Array.isArray(perms) ? perms : [];
    } catch {
      return [];
    }
  }, []);

  // IMPORTANT: Use primitive values (userId, isLoading) in deps, NOT stackUser object
  // Stack's useUser() returns a new object reference on every render, which would cause infinite re-renders
  const userId = stackUser?.id;

  const contextValue = useMemo(() => ({
    user: userRef.current as AuthUser,
    isAuthenticated: !!userId,
    loading: isLoading,
    getAccessToken,
    redirectToLogin,
    logout,
    provider: 'stack' as const,
    getSelectedTeam,
    listPermissions,
  }), [userId, isLoading, getAccessToken, redirectToLogin, logout, getSelectedTeam, listPermissions]);

  return (
    <AuthContext.Provider value={contextValue}>
      {children}
    </AuthContext.Provider>
  );
}

export function StackProviderWrapper({ children }: StackProviderWrapperProps) {
  const stackClientApp = getStackClientApp();

  return (
    <StackProvider app={stackClientApp}>
      <StackTheme>
        <StackAuthContextProvider>
          {children}
        </StackAuthContextProvider>
      </StackTheme>
    </StackProvider>
  );
}
