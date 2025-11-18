/**
 * Shared utility for backend URL detection
 * Handles both server-side and client-side contexts with proper TypeScript support
 */

// Type guards and utilities
const isServer = (): boolean => typeof window === 'undefined';
const isBrowser = (): boolean => typeof window !== 'undefined';

// Safe process environment access using globalThis
const getProcessEnv = (key: string): string | undefined => {
  try {
    // Use globalThis to safely access process in Node.js environment
    const nodeProcess = (globalThis as any).process;
    if (nodeProcess && nodeProcess.env) {
      return nodeProcess.env[key];
    }
    return undefined;
  } catch {
    return undefined;
  }
};

/**
 * Get backend URL for API calls
 * Supports manual override via environment variable and automatic detection
 */
export function getBackendUrl(): string {
  if (isServer()) {
    const url = getServerSideBackendUrl();
    console.log(`[SSR] getBackendUrl() returning: ${url}`);
    return url;
  } else {
    const url = getClientSideBackendUrl();
    console.log(`[CSR] getBackendUrl() returning: ${url}`);
    return url;
  }
}

/**
 * Get WebSocket URL for real-time connections
 * Converts HTTP(S) to WS(S) protocols
 */
export function getWebSocketUrl(): string {
  const httpUrl = getBackendUrl();
  return httpUrl.replace(/^http/, 'ws');
}

/**
 * Server-side backend URL resolution
 * Uses Docker internal networking for SSR
 */
function getServerSideBackendUrl(): string {
  // Manual override takes precedence
const manualHost = getProcessEnv('DOGRAH_BACKEND_HOST');
  if (manualHost) {
    return `http://${manualHost}:8000`;
  }
  
  // Docker internal network for SSR
  return getProcessEnv('BACKEND_URL') || 'http://api:8000';
}

/**
 * Client-side backend URL resolution  
 * Uses browser location with automatic detection and manual override support
 */
function getClientSideBackendUrl(): string {
  if (!isBrowser()) {
    throw new Error('getClientSideBackendUrl called in non-browser environment');
  }

  // Automatic detection based on current hostname and protocol
  const hostname = window.location.hostname;
  const protocol = window.location.protocol;
  const backendProtocol = protocol === 'https:' ? 'https:' : 'http:';
  
  // Check for manual override in window object (set by docker-compose)
  if ((window as any).__DOGRAH_BACKEND_HOST__) {
    return `${backendProtocol}://${(window as any).__DOGRAH_BACKEND_HOST__}:8000`;
  }
  
  // Use same protocol as current page for all deployments
  return `${backendProtocol}//${hostname}:8000`;
}

/**
 * Validate that a backend URL is accessible
 * Useful for debugging connection issues
 */
export function isValidBackendUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && 
           parsed.hostname.length > 0 &&
           parsed.port === '8000';
  } catch {
    return false;
  }
}

/**
 * Get current backend configuration for debugging
 */
export function getBackendConfig(): {
  url: string;
  websocketUrl: string;
  isServer: boolean;
  hostname?: string;
  hasManualOverride: boolean;
} {
  const url = getBackendUrl();
  const websocketUrl = getWebSocketUrl();
  const hasManualOverride = isServer() 
    ? !!getProcessEnv('DOGRAH_BACKEND_HOST')
    : isBrowser() && !!(window as any).__DOGRAH_BACKEND_HOST__;
    
  return {
    url,
    websocketUrl,
    isServer: isServer(),
    hostname: isBrowser() ? window.location.hostname : undefined,
    hasManualOverride
  };
}