# Sativoice i18n + UI Rebrand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task.

**Goal:** Add Italian/English internationalization via next-intl with subpath routing (`/it/`, `/en/`). During extraction, fix all user-facing "Dograh" strings → "Sativoice" in Italian. English translations keep "Dograh" as upstream reference.

**Architecture:** `next-intl` middleware detects locale from URL subpath, injects messages via `NextIntlClientProvider`. Server Components use `getTranslations()`, Client Components use `useTranslations()`. Translation files in `messages/it.json` and `messages/en.json`. Incremental migration: start with high-visibility pages (login, overview, sidebar), then progressively migrate remaining pages.

**Tech Stack:** next-intl ^4, Next.js 15 App Router, TypeScript

## Global Constraints

- All user-facing strings must use `t("key")` — no hardcoded English text
- Italian (`it`) is the default locale; root `/` redirects to `/it`
- In `it.json`: "Dograh" → "Sativoice", links → sativoice domains
- In `en.json`: keep "Dograh" references (upstream reference)
- Code identifiers (`provider === "dograh"`, `DograhClient`, `dograh_auth_token`) stay
- The `next-intl` middleware must not interfere with existing middleware (OSS auth)
- All existing routes (300+) must work under locale prefix

---

### Task 1: Install next-intl and configure middleware

**Files:**
- Modify: `ui/package.json`
- Create: `ui/src/i18n.ts`
- Create: `ui/src/middleware.ts` (rewrite with locale support)
- Create: `ui/src/app/[locale]/layout.tsx` (locale-aware root layout)
- Create: `ui/src/app/[locale]/page.tsx` (locale-aware root page)
- Create: `ui/src/app/[locale]/[...rest]/page.tsx` (catch-all for existing routes)
- Create: `ui/messages/it.json` (initial)
- Create: `ui/messages/en.json` (initial)

**Interfaces:**
- Produces: locale-aware routing, `useTranslations()` available in all components

- [ ] **Step 1: Install next-intl**

```bash
cd ui && npm install next-intl@latest
```

- [ ] **Step 2: Create i18n.ts config**

`ui/src/i18n.ts`:
```typescript
import { getRequestConfig } from "next-intl/server";
import { routing } from "./i18n/routing";

export default getRequestConfig(async ({ requestLocale }) => {
  let locale = await requestLocale;
  if (!locale || !routing.locales.includes(locale as "it" | "en")) {
    locale = routing.defaultLocale;
  }
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
```

- [ ] **Step 3: Create routing config**

`ui/src/i18n/routing.ts`:
```typescript
import { defineRouting } from "next-intl/routing";
import { createNavigation } from "next-intl/navigation";

export const routing = defineRouting({
  locales: ["it", "en"],
  defaultLocale: "it",
  localePrefix: "always",
});

export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
```

- [ ] **Step 4: Create initial translation files**

`ui/messages/it.json`:
```json
{
  "app": {
    "name": "Sativoice Enterprise",
    "tagline": "Piattaforma Voice AI open-source"
  }
}
```

`ui/messages/en.json`:
```json
{
  "app": {
    "name": "Dograh",
    "tagline": "Open Source Voice AI Platform"
  }
}
```

- [ ] **Step 5: Rewrite middleware.ts** with locale support

```typescript
import createMiddleware from "next-intl/middleware";
import { routing } from "./i18n/routing";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { getServerBackendUrl } from "@/lib/apiClient";

const intlMiddleware = createMiddleware(routing);

const OSS_TOKEN_COOKIE = "dograh_auth_token";
const PUBLIC_PATHS = ["/auth/login", "/auth/signup"];

// [OSS auth logic — same as current middleware, just runs AFTER locale detection]

export default async function middleware(request: NextRequest) {
  // First: handle locale prefix via next-intl
  const intlResponse = intlMiddleware(request);
  if (intlResponse) return intlResponse;

  // Then: OSS auth check (unchanged logic)
  // ... same as current middleware auth code
  return NextResponse.next();
}

export const config = {
  matcher: ["/", "/(it|en)/:path*"],
};
```

- [ ] **Step 6: Create [locale] layout and move root layout**

Move existing `ui/src/app/layout.tsx` to `ui/src/app/[locale]/layout.tsx` and add `NextIntlClientProvider`:

```typescript
import { NextIntlClientProvider } from "next-intl";
import { getMessages } from "next-intl/server";
// ... existing imports

export default async function LocaleLayout({ children, params }) {
  const { locale } = await params;
  const messages = await getMessages();
  return (
    <html lang={locale} className="dark" suppressHydrationWarning>
      <body>
        <NextIntlClientProvider messages={messages}>
          {/* ... existing providers and children ... */}
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
```

Create root `ui/src/app/layout.tsx` (minimal, just redirects):
```typescript
import { redirect } from "next/navigation";

export default function RootLayout() {
  redirect("/it");
}
```

- [ ] **Step 7: Create catch-all for existing pages**

Create `ui/src/app/[locale]/[...rest]/page.tsx` that imports and renders the original page:
```typescript
import { notFound } from "next/navigation";

export default function CatchAllPage({ params }: { params: { rest: string[] } }) {
  const path = params.rest.join("/");
  // Dynamic import of the original page
  return null; // Will be implemented per-page in Task 2
}
```

- [ ] **Step 8: Verify middleware works**

```bash
cd ui && npm run dev
# Visit http://localhost:3000 → should redirect to /it
# Visit http://localhost:3000/en → should work
```

- [ ] **Step 9: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src/i18n.ts ui/src/i18n/ ui/src/middleware.ts ui/src/app/ ui/messages/
git commit -m "feat: next-intl setup — locale routing, middleware, Italian/English"
```

---

### Task 2: Migrate auth & login pages

**Files:**
- Modify: `ui/src/components/auth/AuthShell.tsx`
- Modify: `ui/src/app/auth/login/page.tsx`
- Modify: `ui/src/app/auth/signup/page.tsx`
- Modify: `ui/messages/it.json`, `ui/messages/en.json`

**Scope:** Extract all user-facing strings from AuthShell, login, signup.

- [ ] **Step 1: Add auth messages to translation files**

`messages/it.json` add:
```json
{
  "auth": {
    "heroTitle": "La piattaforma voice AI open-source.",
    "highlights": ["Speech-to-speech", "MCP-native", "BYOK - qualsiasi modello"],
    "enterpriseTitle": "Serve on-prem, data residency e data perimeter?",
    "enterpriseDesc": "Deployamo Sativoice nel tuo ambiente per team regolamentati e high-scale.",
    "loginTitle": "Accedi",
    "signupTitle": "Registrati"
  }
}
```

`messages/en.json` add:
```json
{
  "auth": {
    "heroTitle": "The open-source voice AI platform.",
    "highlights": ["Speech-to-speech", "MCP-native", "BYOK - any model"],
    "enterpriseTitle": "Need on-prem, data residency & a data perimeter?",
    "enterpriseDesc": "We deploy Dograh inside your environment for regulated and high-scale teams.",
    "loginTitle": "Sign In",
    "signupTitle": "Sign Up"
  }
}
```

- [ ] **Step 2: Replace hardcoded strings in AuthShell.tsx**

Replace:
```tsx
<h1>...>The open-source voice AI platform.</h1>
```
With:
```tsx
const t = useTranslations("auth");
<h1>...>{t("heroTitle")}</h1>
```

Same pattern for enterprise title/description and highlights.

- [ ] **Step 3: Migrate login/signup pages**

Each page: convert to client component, wrap strings with `useTranslations()`, add translations to JSON.

- [ ] **Step 4: Commit**

```bash
git add ui/messages/ ui/src/components/auth/ ui/src/app/auth/
git commit -m "feat: i18n auth pages — Italian + English"
```

---

### Task 3: Migrate overview & layout (sidebar, footer)

**Files:** Modify overview page, AppSidebar, AppLayout, Footer, BrandLogo.

**Scope:** Extract all user-facing strings, fix "Dograh" → "Sativoice" in it.json.

Similar pattern to Task 2: extract strings → add to JSON → replace in TSX.

Key changes:
- `"Welcome to Dograh"` → it.json: `"Benvenuto in Sativoice Enterprise"`, en.json: `"Welcome to Dograh"`
- Sidebar links: "Agents", "Settings", etc. → Italian
- Documentation links: `docs.sativoice.com` in it.json, `docs.dograh.com` in en.json
- Footer text

- [ ] **Step 1-4: Extract, translate, replace, commit** (per component)

---

### Task 4: Migrate remaining pages (progressive)

**Files:** All remaining pages under `ui/src/app/` with user-facing strings.

**Scope:** Batch migration by page group:
- Settings, Model Configurations, Telephony
- Workflow editor, Workflow runs, Recordings
- Billing, API Keys, Campaigns, Tools
- Admin pages

Each batch follows: extract strings → add to JSON → replace in TSX → commit.

---

### Task 5: Verification

- [ ] **Step 1: Build check**
```bash
cd ui && npm run build 2>&1 | tail -5
```

- [ ] **Step 2: Verify no hardcoded English in migrated components**
```bash
grep -rn "Welcome to\|Dograh\|Settings\|Dashboard" ui/src/components/auth/ ui/src/app/overview/ --include="*.tsx" | grep -v "import\|// " || echo "✅ Clean"
```

- [ ] **Step 3: Verify it.json has no Dograh references**
```bash
grep -c "Dograh" ui/messages/it.json || echo "0 — ✅ Clean"
```
