# UI - Frontend Application

Next.js 15 frontend for the Dograh voice AI platform.

## Project Structure

```
ui/
├── src/
│   ├── app/          # Next.js App Router pages
│   ├── components/   # React components
│   ├── lib/          # Utilities and configurations
│   ├── client/       # Auto-generated API client
│   ├── context/      # React context providers
│   ├── hooks/        # Custom React hooks
│   ├── constants/    # Application constants
│   └── types/        # TypeScript type definitions
├── public/           # Static assets
└── package.json
```

## Where to Find Things

| Looking for...      | Go to...                                             |
| ------------------- | ---------------------------------------------------- |
| Pages/routes        | `src/app/` - Next.js App Router (file-based routing) |
| Reusable components | `src/components/` - organized by feature             |
| Base UI primitives  | `src/components/ui/` - shadcn/ui components          |
| Workflow builder    | `src/components/flow/` - React Flow based            |
| API calls           | `src/client/` - auto-generated from OpenAPI spec     |
| Auth utilities      | `src/lib/auth/`                                      |
| Helper functions    | `src/lib/utils.ts`                                   |
| Global state        | `src/context/` - React context providers             |

## Tech Stack

- Next.js 15 with App Router, React 19, TypeScript
- Tailwind CSS with shadcn/ui components
- Zustand for state management
- @xyflow/react for workflow builder

## API Client

The `src/client/` directory is auto-generated from the backend OpenAPI spec. Whenever you add a
new api route in backend, and wish to use it in the UI, generate the client using below command.

```bash
npm run generate-client
```

## Conventions

### File Uploads

Always use a hidden `<input type="file">` with a visible `<Button>` that triggers it via `fileInputRef.current?.click()`. Never use a visible `<Input type="file">` — the native file input styling is inconsistent and confusing. Show the selected filename next to or below the button.

### Authenticated API Calls

Components that make API calls must wait for auth to be ready before fetching. Use `useAuth()` and guard the `useEffect` with `authLoading` and `user`:

```tsx
const { user, loading: authLoading } = useAuth();
const hasFetched = useRef(false);

useEffect(() => {
  if (authLoading || !user || hasFetched.current) return;
  hasFetched.current = true;
  fetchData();
}, [authLoading, user]);
```

The auth interceptor (which attaches the Bearer token) is only registered once auth is fully loaded. Fetching before that sends unauthenticated requests that silently fail.

## Development

```bash
npm install
npm run dev    # Runs on port 3000
```
