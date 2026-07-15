import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    // Expose afterEach etc. globally so @testing-library/react auto-registers
    // its DOM cleanup between tests.
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
    server: {
      deps: {
        // Inline @stackframe so vi.mock('@stackframe/stack') also intercepts the
        // package's internal imports of its own index (e.g. team-switcher.js
        // importing useUser from '../index.js').
        inline: [/@stackframe/],
      },
    },
  },
});
