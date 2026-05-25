import { describe, it, expect } from 'vitest';
import { Toaster } from './sonner';

// Note: sonner uses next-themes which requires a ThemeProvider wrapper
// These tests verify the component exports correctly

describe('Toaster', () => {
  it('exports Toaster component', () => {
    // Verify the named export exists
    expect(Toaster).toBeDefined();
  });

  it('Toaster is a function component', () => {
    expect(typeof Toaster).toBe('function');
  });
});