/**
 * Centralized disposition codes used throughout the application
 * Update this array when adding new disposition codes
 */
export const DISPOSITION_CODES = [
  'end_call_tool',
  'user_hangup',
  'call_duration_exceeded',
  'user_idle_max_duration_exceeded',
  'system_connect_error',
  'unknown',
  'voicemail_detected'
] as const;

export type DispositionCode = typeof DISPOSITION_CODES[number];
