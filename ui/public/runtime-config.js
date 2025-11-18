/**
 * Runtime configuration for client-side manual overrides
 * This file is replaced by docker-compose startup when DOGRAH_BACKEND_HOST is set
 */

// Check if manual backend host override is provided via environment
// This will be replaced by the actual value if DOGRAH_BACKEND_HOST is set
if ('__DOGRAH_BACKEND_HOST_PLACEHOLDER__' !== '__DOGRAH_BACKEND_HOST_PLACEHOLDER__') {
  window.__DOGRAH_BACKEND_HOST__ = '__DOGRAH_BACKEND_HOST_PLACEHOLDER__';
}

// For debugging - log the backend configuration
if (window.location.hostname !== 'localhost') {
  console.log('[Dograh] Backend detection:', {
    manualOverride: window.__DOGRAH_BACKEND_HOST__ || 'none',
    autoDetected: window.location.hostname + ':8000'
  });
}