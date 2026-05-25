import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';
import { server } from './mocks/server';

// Mock ResizeObserver for Radix UI components in jsdom
global.ResizeObserver = vi.fn().mockImplementation(function () {
  return {
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  };
});

// Start MSW before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

// Reset handlers after each test
afterEach(() => server.resetHandlers());

// Close MSW after all tests
afterAll(() => server.close());
