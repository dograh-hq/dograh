# Frontend Testing Plan — Dograh UI

## Overview

The Dograh frontend is a Next.js 15 + React 19 + TypeScript application using:
- **UI**: shadcn/ui + Radix UI primitives + Tailwind CSS
- **State**: React Context (global), Zustand (workflow builder), React Hook Form (forms)
- **API**: Auto-generated OpenAPI client (`@hey-api/openapi-ts`)
- **Auth**: Stack Auth (`@stackframe/stack`)
- **Charts**: Recharts
- **Flow**: React Flow (`@xyflow/react`)

Current test coverage: **Zero** — no test framework, no test files, no CI test step.

---

## Phase 1: Tooling Setup

### 1.1 Install Test Dependencies

```bash
cd ui
npm install -D vitest @vitest/ui @testing-library/react @testing-library/jest-dom \
  @testing-library/user-event @testing-library/dom jsdom msw@latest \
  @playwright/test @types/jest
```

**Rationale**:
- **Vitest**: Fast, Vite-native, excellent TypeScript support, replaces Jest
- **@testing-library/react**: Render components, fire events, query DOM
- **@testing-library/jest-dom**: Custom matchers (`toBeInTheDocument`, `toHaveValue`)
- **jsdom**: Browser-like environment for unit tests
- **MSW (Mock Service Worker)**: Intercept and mock HTTP requests at the network layer
- **Playwright**: E2E testing with real browser automation

### 1.2 Configuration Files

**`ui/vitest.config.ts`**:
```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      exclude: [
        'node_modules/',
        'src/test/',
        'src/client/',
        '**/*.d.ts',
      ],
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
```

**`ui/src/test/setup.ts`**:
```typescript
import '@testing-library/jest-dom';
import { server } from './mocks/server';

// Start MSW before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

// Reset handlers after each test
afterEach(() => server.resetHandlers());

// Close MSW after all tests
afterAll(() => server.close());
```

**`ui/src/test/mocks/server.ts`**:
```typescript
import { setupServer } from 'msw/node';
import { handlers } from './handlers';

export const server = setupServer(...handlers);
```

**`ui/src/test/mocks/handlers.ts`**:
```typescript
import { http, HttpResponse } from 'msw';

export const handlers = [
  // Auth
  http.get('/api/auth/session', () => {
    return HttpResponse.json({ user: { id: '1', email: 'test@example.com' } });
  }),

  // User config
  http.get('/api/v1/user/configurations/user', () => {
    return HttpResponse.json({
      llm: { provider: 'openai', model: 'gpt-4.1', base_url: 'https://api.openai.com/v1' },
      tts: { provider: 'openai', model: 'gpt-4o-mini-tts' },
      stt: { provider: 'deepgram', model: 'nova-2' },
    });
  }),

  http.put('/api/v1/user/configurations/user', async () => {
    return HttpResponse.json({ success: true });
  }),

  // Workflows
  http.get('/api/v1/workflows', () => {
    return HttpResponse.json({ items: [], total: 0 });
  }),

  // Add more handlers as needed
];
```

**`ui/src/test/test-utils.tsx`** — Custom render with providers:
```typescript
import { render as rtlRender, RenderOptions } from '@testing-library/react';
import { ReactElement, ReactNode } from 'react';
import { UserConfigProvider } from '@/context/UserConfigContext';
import { AppConfigProvider } from '@/context/AppConfigContext';

function AllProviders({ children }: { children: ReactNode }) {
  return (
    <AppConfigProvider>
      <UserConfigProvider>
        {children}
      </UserConfigProvider>
    </AppConfigProvider>
  );
}

export function render(ui: ReactElement, options?: Omit<RenderOptions, 'wrapper'>) {
  return rtlRender(ui, { wrapper: AllProviders, ...options });
}

export * from '@testing-library/react';
```

**`ui/playwright.config.ts`**:
```typescript
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
```

### 1.3 Update package.json Scripts

```json
{
  "scripts": {
    "test": "vitest",
    "test:ui": "vitest --ui",
    "test:coverage": "vitest --coverage",
    "test:e2e": "playwright test",
    "test:e2e:ui": "playwright test --ui"
  }
}
```

---

## Phase 2: Unit Tests

### 2.1 UI Primitive Components (`src/components/ui/`)

These are thin wrappers around Radix UI. Test focus: correct props forwarding, accessibility, styling.

| Component | Test File | Key Assertions |
|-----------|-----------|----------------|
| `Button` | `button.test.tsx` | Renders children, handles click, disabled state, variants |
| `Input` | `input.test.tsx` | Accepts value, onChange, placeholder, disabled |
| `Select` | `select.test.tsx` | Opens dropdown, selects option, displays value |
| `Dialog` | `dialog.test.tsx` | Opens/closes, renders title, focus trap, ESC to close |
| `Checkbox` | `checkbox.test.tsx` | Checked/unchecked state, onChange fires |
| `Tabs` | `tabs.test.tsx` | Switches panels, active tab indicator |
| `Switch` | `switch.test.tsx` | Toggles on click, respects checked prop |
| `Badge` | `badge.test.tsx` | Renders text, applies variant classes |
| `Card` | `card.test.tsx` | Renders header, content, footer slots |
| `Table` | `table.test.tsx` | Renders rows/columns, sortable headers |
| `Textarea` | `textarea.test.tsx` | Multi-line input, resize behavior |
| `Calendar` | `calendar.test.tsx` | Date selection, month navigation |
| `DropdownMenu` | `dropdown-menu.test.tsx` | Opens on click, item selection, keyboard nav |
| `Popover` | `popover.test.tsx` | Opens/closes, positions content |
| `Tooltip` | `tooltip.test.tsx` | Shows on hover, hides on leave |
| `RadioGroup` | `radio-group.test.tsx` | Single selection, keyboard navigation |
| `Progress` | `progress.test.tsx` | Renders correct width for value |
| `Separator` | `separator.test.tsx` | Renders horizontal/vertical |
| `Skeleton` | `skeleton.test.tsx` | Renders with pulse animation class |
| `Sonner` | `sonner.test.tsx` | Toast appears, auto-dismisses |
| `Sheet` | `sheet.test.tsx` | Slides in from side, closes on overlay click |
| `Sidebar` | `sidebar.test.tsx` | Expands/collapses, shows/hides items |
| `AlertDialog` | `alert-dialog.test.tsx` | Confirm/cancel actions, focus management |
| `Collapsible` | `collapsible.test.tsx` | Expands/collapses content |
| `ChoiceChips` | `choice-chips.test.tsx` | Single/multi selection, removable chips |
| `JSONEditor` | `json-editor.test.tsx` | Validates JSON, shows error state |

**Example — `button.test.tsx`**:
```typescript
import { render, screen, fireEvent } from '@/test/test-utils';
import { Button } from './button';

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument();
  });

  it('handles click events', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>Click</Button>);
    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('is disabled when disabled prop is true', () => {
    render(<Button disabled>Disabled</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('applies variant classes', () => {
    const { container } = render(<Button variant="destructive">Delete</Button>);
    expect(container.firstChild).toHaveClass('bg-destructive');
  });
});
```

### 2.2 Utility Functions (`src/lib/`)

| File | Test File | Key Test Cases |
|------|-----------|----------------|
| `utils.ts` | `utils.test.ts` | `cn()` class merging, date formatting, slug generation |
| `filters.ts` | `filters.test.ts` | Filter predicate composition, attribute extraction |
| `filterAttributes.ts` | `filterAttributes.test.ts` | Attribute parsing, type coercion |
| `files.ts` | `files.test.ts` | File upload validation, size/format checks |
| `logger.ts` | `logger.test.ts` | Log level filtering, structured output |
| `apiClient.ts` | `apiClient.test.ts` | Base URL resolution (server vs client), interceptor registration |

### 2.3 Custom Hooks (`src/hooks/`)

| Hook | Test File | Key Test Cases |
|------|-----------|----------------|
| `use-mobile.ts` | `use-mobile.test.ts` | Detects mobile viewport, updates on resize |
| `useAudioPlayback.ts` | `useAudioPlayback.test.ts` | Play/pause/stop, progress tracking, error handling |
| `useLatestReleaseVersion.ts` | `useLatestReleaseVersion.test.ts` | Fetches version, caches result, handles errors |

**Example — `use-mobile.test.ts`**:
```typescript
import { renderHook } from '@testing-library/react';
import { useIsMobile } from './use-mobile';

describe('useIsMobile', () => {
  it('returns false for desktop viewport', () => {
    window.innerWidth = 1024;
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it('returns true for mobile viewport', () => {
    window.innerWidth = 375;
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });
});
```

### 2.4 Constants & Types (`src/constants/`, `src/types/`)

- Verify constant values match expected enums
- Type tests using `tsd` or `vitest typecheck`

---

## Phase 3: Integration Tests

### 3.1 Context Providers (`src/context/`)

Test with real provider wrappers, mock API calls via MSW.

| Context | Test File | Key Test Cases |
|---------|-----------|----------------|
| `UserConfigContext` | `UserConfigContext.test.tsx` | Fetches config on mount, saves config, handles errors, loading states |
| `AppConfigContext` | `AppConfigContext.test.tsx` | Provides app config, handles feature flags |
| `OnboardingContext` | `OnboardingContext.test.tsx` | Tracks onboarding step, persists state |
| `UnsavedChangesContext` | `UnsavedChangesContext.test.tsx` | Tracks dirty state, warns on navigation |
| `TelephonyConfigWarningsContext` | `TelephonyConfigWarningsContext.test.tsx` | Displays warnings for missing telephony config |

**Example — `UserConfigContext.test.tsx`**:
```typescript
import { render, screen, waitFor } from '@/test/test-utils';
import { useUserConfig } from './UserConfigContext';

function TestComponent() {
  const { userConfig, loading, saveUserConfig } = useUserConfig();
  if (loading) return <div>Loading</div>;
  return (
    <div>
      <div data-testid="provider">{userConfig?.llm?.provider}</div>
      <button onClick={() => saveUserConfig({ llm: { provider: 'openai', model: 'gpt-4' } })}>
        Save
      </button>
    </div>
  );
}

describe('UserConfigContext', () => {
  it('fetches user config on mount', async () => {
    render(<TestComponent />);
    expect(screen.getByText('Loading')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('provider')).toHaveTextContent('openai');
    });
  });

  it('saves user config', async () => {
    render(<TestComponent />);
    await waitFor(() => screen.getByRole('button'));
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => {
      // Assert API was called
    });
  });
});
```

### 3.2 Complex Components (`src/components/`)

| Component | Test File | Key Test Cases |
|-----------|-----------|----------------|
| `ServiceConfigurationForm` | `ServiceConfigurationForm.test.tsx` | Renders all tabs, switches providers, shows/hides fields, validates, submits |
| `LLMConfigSelector` | `LLMConfigSelector.test.tsx` | Provider dropdown, model selection, custom input toggle |
| `VoiceSelector` | `VoiceSelector.test.tsx` | Voice list, selection, preview |
| `WorkflowCard` | `WorkflowCard.test.tsx` | Renders workflow data, edit/delete actions |
| `WorkflowTable` | `WorkflowTable.test.tsx` | Sorting, pagination, row actions |
| `CreateWorkflowButton` | `CreateWorkflowButton.test.tsx` | Opens modal, creates workflow, shows error |
| `TemplateCard` | `TemplateCard.test.tsx` | Renders template, applies on click |
| `UploadWorkflowButton` | `UploadWorkflowButton.test.tsx` | File upload, validation, error handling |
| `DailyUsageTable` | `DailyUsageTable.test.tsx` | Renders usage data, date formatting |
| `MCPSection` | `MCPSection.test.tsx` | Tool list, add/remove, configuration |
| `MediaPreviewDialog` | `MediaPreviewDialog.test.tsx` | Opens with media, closes, audio playback |
| `ChatwootWidget` | `ChatwootWidget.test.tsx` | Loads widget, handles events |
| `SignInClient` | `SignInClient.test.tsx` | Form validation, auth flow, error display |
| `SpinLoader` | `SpinLoader.test.tsx` | Shows/hides based on loading prop |
| `ThemeSwitcher` | `ThemeSwitcher.test.tsx` | Toggles theme, persists preference |
| `Footer` | `Footer.test.tsx` | Renders links, version info |
| `PostHogIdentify` | `PostHogIdentify.test.tsx` | Identifies user on auth |
| `SentryErrorBoundary` | `SentryErrorBoundary.test.tsx` | Catches errors, shows fallback UI |
| `TelemetrySection` | `TelemetrySection.test.tsx` | Toggle telemetry, persists setting |
| `CallTypeCell` | `CallTypeCell.test.tsx` | Renders call type badge |

### 3.3 Workflow Builder Components (`src/components/workflow/`, `src/components/flow/`)

These use React Flow and Zustand — test with mocked stores.

| Component | Test File | Key Test Cases |
|-----------|-----------|----------------|
| Flow canvas | `flow/*.test.tsx` | Node rendering, edge connections, drag-and-drop |
| Node configuration panels | `workflow/*.test.tsx` | Form validation, state updates, node types |
| Conversation components | `workflow/conversation/*.test.tsx` | Message rendering, send/receive |
| Folder management | `workflow/folders/*.test.tsx` | CRUD operations, drag-drop |

### 3.4 Page Components (`src/app/`)

Test page-level integration with routing and data fetching.

| Page | Test File | Key Test Cases |
|------|-----------|----------------|
| `/model-configurations` | `model-configurations/page.test.tsx` | Loads config, renders form, saves changes |
| `/workflow/create` | `workflow/create/page.test.tsx` | Creates workflow, validates, redirects |
| `/workflow/[id]` | `workflow/[id]/page.test.tsx` | Loads workflow, edits, saves |
| `/settings` | `settings/page.test.tsx` | Renders settings, updates preferences |
| `/auth/login` | `auth/login/page.test.tsx` | Form validation, login flow, error handling |
| `/overview` | `overview/page.test.tsx` | Dashboard stats, recent activity |
| `/recordings` | `recordings/page.test.tsx` | List recordings, play, filter |
| `/tools` | `tools/page.test.tsx` | Tool list, create/edit/delete |
| `/campaigns` | `campaigns/page.test.tsx` | Campaign list, status, actions |
| `/usage` | `usage/page.test.tsx` | Usage charts, date range selection |
| `/api-keys` | `api-keys/page.test.tsx` | Key list, generate, revoke |
| `/telephony-configurations` | `telephony-configurations/page.test.tsx` | Provider config, validation |
| `/files` | `files/page.test.tsx` | File list, upload, delete |
| `/reports` | `reports/page.test.tsx` | Report generation, export |
| `/automation` | `automation/page.test.tsx` | Automation rules, triggers |

---

## Phase 4: E2E Tests (Playwright)

### 4.1 Authentication Flows

| Test File | Scenario |
|-----------|----------|
| `auth/login.spec.ts` | User logs in with valid credentials, sees dashboard |
| `auth/signup.spec.ts` | New user signs up, completes onboarding |
| `auth/logout.spec.ts` | User logs out, redirected to login |
| `auth/session.spec.ts` | Session persists across page reloads |

### 4.2 Core User Journeys

| Test File | Scenario |
|-----------|----------|
| `workflow/create.spec.ts` | Create voice agent from scratch, configure LLM, save |
| `workflow/edit.spec.ts` | Open existing workflow, modify nodes, save changes |
| `workflow/deploy.spec.ts` | Deploy workflow, verify webhook URL generated |
| `model-configurations.spec.ts` | Configure OpenAI with custom base URL, save, verify persisted |
| `campaigns/create.spec.ts` | Create campaign, upload contacts, launch |
| `recordings/play.spec.ts` | Navigate to recordings, play audio, verify playback |
| `tools/mcp.spec.ts` | Add MCP tool, configure server URL, test connection |
| `telephony/configure.spec.ts` | Add telephony provider, configure inbound webhook |

### 4.3 Critical Path — Voice Agent Creation

```typescript
// e2e/workflow/create-voice-agent.spec.ts
import { test, expect } from '@playwright/test';

test('create and configure a voice agent end-to-end', async ({ page }) => {
  // Login
  await page.goto('/auth/login');
  await page.fill('[name="email"]', 'test@example.com');
  await page.fill('[name="password"]', 'password');
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL('/overview');

  // Navigate to create workflow
  await page.click('text=Voice Agents');
  await page.click('text=Create Agent');
  await expect(page).toHaveURL(/\/workflow\/create/);

  // Configure LLM with custom base URL
  await page.click('text=Models');
  await page.selectOption('select[name="llm_provider"]', 'openai');
  await page.fill('input[name="llm_base_url"]', 'https://custom.example.com/v1');
  await page.fill('input[name="llm_api_key"]', 'sk-test-key');
  await page.selectOption('select[name="llm_model"]', 'gpt-4.1');

  // Save configuration
  await page.click('text=Save Configuration');
  await expect(page.locator('.sonner')).toContainText('Saved');

  // Verify persistence
  await page.reload();
  await expect(page.locator('input[name="llm_base_url"]')).toHaveValue('https://custom.example.com/v1');
});
```

### 4.4 Cross-Browser & Responsive

- Run E2E suite on Chromium and Firefox
- Test key flows at mobile viewport (375px width)
- Verify sidebar collapses, tables scroll, modals fit screen

---

## Phase 5: Backend Test Expansion

### 5.1 Service Factory Tests

| Test File | Coverage |
|-----------|----------|
| `test_openai_service_factory.py` | OpenAI LLM with/without custom base_url, gpt-5 reasoning params |
| `test_openrouter_service_factory.py` | OpenRouter with custom base_url |
| `test_speaches_service_factory.py` | Local model endpoint configuration |
| `test_minimax_service_factory.py` | MiniMax with custom base_url and temperature |
| `test_azure_service_factory.py` | Azure endpoint configuration |
| `test_google_vertex_service_factory.py` | Vertex AI project/location/credentials |
| `test_bedrock_service_factory.py` | AWS credentials and region |

### 5.2 Configuration Registry Tests

| Test File | Coverage |
|-----------|----------|
| `test_configuration_registry.py` | All provider schemas validate, defaults correct, required fields |
| `test_configuration_merge.py` | Override merging, fallback to global, partial overrides |
| `test_configuration_validity.py` | API key validation per provider, masked key rejection |
| `test_configuration_defaults_endpoint.py` | `/configurations/defaults` returns complete schemas |

### 5.3 Integration Tests for New Feature

| Test File | Coverage |
|-----------|----------|
| `test_openai_base_url_persistence.py` | Save/load user config with custom base_url |
| `test_openai_base_url_runtime.py` | Pipeline uses custom base_url at runtime |
| `test_openai_base_url_workflow_override.py` | Workflow-level override of base_url |

---

## Phase 6: CI/CD Integration

### 6.1 GitHub Actions Workflow

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '24' }
      - run: cd ui && npm ci
      - run: cd ui && npm run test:coverage
      - uses: codecov/codecov-action@v4

  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '24' }
      - run: cd ui && npm ci
      - run: cd ui && npx playwright install
      - run: cd ui && npm run test:e2e
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: playwright-report, path: ui/playwright-report/ }
```

### 6.2 Coverage Gates

- Unit tests: minimum 70% line coverage
- Integration tests: minimum 50% line coverage
- E2E tests: all critical user journeys must pass
- Block PR merge on test failure

---

## Implementation Priority

| Priority | Phase | Effort | Impact |
|----------|-------|--------|--------|
| P0 | 1. Tooling Setup | Medium | Enables all testing |
| P0 | 2.1 UI Primitives | Medium | Foundation for component tests |
| P0 | 2.2 Utilities + Hooks | Low | Quick wins, high confidence |
| P1 | 3.1 Context Providers | Medium | Core app logic |
| P1 | 3.2 Complex Components | High | User-facing features |
| P1 | 5.1 Service Factory Tests | Medium | Backend stability |
| P2 | 3.3 Workflow Builder | High | Complex React Flow + Zustand |
| P2 | 3.4 Page Components | High | Full page integration |
| P2 | 4.1-4.3 E2E Core Journeys | High | Regression prevention |
| P3 | 4.4 Cross-browser | Medium | Quality assurance |
| P3 | 5.2-5.3 Backend Expansion | Medium | Complete backend coverage |

---

## Estimated Test Count

| Layer | Estimated Tests |
|-------|-----------------|
| Unit (UI primitives) | ~150 |
| Unit (utilities/hooks) | ~30 |
| Integration (contexts) | ~25 |
| Integration (components) | ~80 |
| Integration (pages) | ~45 |
| E2E (core journeys) | ~20 |
| E2E (edge cases) | ~15 |
| Backend unit | ~60 |
| Backend integration | ~25 |
| **Total** | **~450** |
