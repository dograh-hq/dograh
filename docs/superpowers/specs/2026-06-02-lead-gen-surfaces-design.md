# Lead-gen surfaces: Credits & Billing, Hire-an-Expert, Top-up & Enterprise intake

**Date:** 2026-06-02
**Status:** Approved for planning
**Scope:** Frontend only (`ui/`). No backend in this milestone. Form submissions fire PostHog events now; a MongoDB-backed endpoint will be wired later via a single `submitLead()` seam.

## Guiding constraints

- **Minimum diff, maximum reuse.** Touch existing files as little as possible; extract rather than rewrite; never duplicate logic. Existing functional code must not regress.
- **Follow established repo patterns** (verified against the codebase):
  - shadcn primitives in `src/components/ui/` (all needed ones exist: `dialog`, `select`, `textarea`, `radio-group`, `input`, `label`, `button`, `card`, `progress`, `tooltip`).
  - PostHog: `import posthog from "posthog-js"` + `import { PostHogEvent } from "@/constants/posthog-events"`, then `posthog.capture(PostHogEvent.X, { ...props })`. **All new event names are registered in `src/constants/posthog-events.ts`** (the central registry) — no string literals.
  - Auto-generated client returns `{ data, error }` and does NOT throw — always check `response.error` and use `detailFromError` from `@/lib/apiError`.
  - React Context providers live in `src/context/` (e.g. `OnboardingContext`); sidebar collapse uses the `isCollapsed` pattern; toasts use `sonner`.

## Summary of changes

| # | Change | Primary files |
|---|--------|---------------|
| 1 | Rename sidebar section `OBSERVE` → `MANAGE`; add `Credits & Billing` link → `/billing`; add `Hire an Expert` footer button | `src/components/layout/AppSidebar.tsx` |
| 2 | New `/billing` page; extract Dograh Model Credits card into a component; add footer CTAs; remove card from Agent Runs | `src/app/billing/page.tsx` (new), `src/components/billing/DograhCreditsCard.tsx` (new), `src/app/usage/page.tsx` (delete card) |
| 3 | Three lead modals + shared building blocks | `src/components/lead-forms/*` (new) |
| 4 | Workflow-builder Hire-an-Expert nudge | `src/components/lead-forms/HireExpertNudge.tsx` (new), `src/app/workflow/[workflowId]/RenderWorkflow.tsx` (mount) |
| 5 | Shared modal state provider | `src/context/LeadFormsContext.tsx` (new), `src/components/layout/AppLayout.tsx` (mount) |
| 6 | New PostHog event names | `src/constants/posthog-events.ts` |

---

## 1. Sidebar (`AppSidebar.tsx`)

Data + footer changes only — no structural rewrite.

- In `NAV_SECTIONS`, rename the section `label` `"OBSERVE"` → `"MANAGE"`.
- Add one item to that section, after `Reports`:
  `{ title: "Credits & Billing", url: "/billing", icon: CircleDollarSign }` (`CircleDollarSign` is already imported).
  Final MANAGE order: Agent Runs, Reports, Credits & Billing.
- In the existing `SidebarFooter`, change the avatar row to `justify-between` and add a **Hire an Expert** button to the right of the avatar circle:
  - **Person icon** (`UserRound` from `lucide-react`), **solid primary** `Button` (default variant), label **"Hire an Expert" always visible** when sidebar is expanded.
  - When `isCollapsed` is true, render **icon-only** with the existing `Tooltip` pattern (mirror how `ThemeToggle`/nav items collapse). Text hides only on sidebar collapse.
  - `onClick`: `openHireExpert("sidebar")` from `useLeadForms()`. Fires `PostHogEvent.HIRE_EXPERT_OPENED` with `{ source: "sidebar" }` inside the provider.
  - Placed for both `provider === "stack"` and `provider !== "stack"` avatar rows (both exist in the footer today).

## 2. `/billing` page + extracted credits card

### `src/components/billing/DograhCreditsCard.tsx` (new)
- Move the existing "Dograh Model Credits" JSX out of `usage/page.tsx` (≈ line 412) verbatim, including its `getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet` fetch, loading skeleton, `Card`/`CardHeader`/`CardContent`, the used/quota numbers, remaining, and `Progress` bar.
- The component owns its own fetch (same call, same `{data,error}` check, same `isLoadingCredits` state) so both `/billing` and any future host stay self-contained.
- **Add a card footer** (mockup option A), inside the existing `Card`:
  - Left: helper text `Running low?` (muted, small).
  - Right-aligned button group: `Hire an Expert` (`variant="outline"`, person icon) + `Request top-up` (default/primary).
  - `Hire an Expert` → `openHireExpert("billing_card")`; `Request top-up` → `openTopUp("billing_card")`.
- Fires `TOPUP_REQUEST_OPENED` / `HIRE_EXPERT_OPENED` with `{ source: "billing_card" }` via the provider.

### `src/app/billing/page.tsx` (new)
- Thin App Router page. Header title **"Credits & Billing"** + short description, then `<DograhCreditsCard />`. Matches the existing page-header style used on `usage`/`reports` pages.
- Auth-guarded fetch pattern per `ui/AGENTS.md` (guard on `authLoading`/`user`) — inherited from the card component.

### `src/app/usage/page.tsx` (edit — deletion)
- Remove the "MPS Credits Card" block and its now-unused credits state/fetch (`mpsCredits`, `isLoadingCredits`, `fetchMpsCredits`, the import of `getMpsCredits...` and `MpsCreditsResponse`) **only if** nothing else on the page uses them. Agent Runs keeps the runs table, filters, and timezone selector untouched.
- Net: a clean removal; credits logic now lives in the extracted component.

## 3. Three lead modals (`src/components/lead-forms/`)

Shared building blocks (new, small):
- `MathCaptcha.tsx` — randomized "What is X + Y?" generated client-side (seeded on mount), validates the numeric answer before submit. Zero deps.
- `submitLead.ts` — single async seam: `submitLead(kind, payload)` fires the appropriate `PostHog` capture with all field values as props, returns success. **Designed so a future MongoDB `POST` is added in one place** without touching the forms.
- `isPersonalEmail.ts` — blocklist of common free domains (`gmail.com`, `yahoo.com`, `outlook.com`, `hotmail.com`, `icloud.com`, `proton.me`/`protonmail.com`, `aol.com`, `gmx.*`, `mail.com`, etc.); returns true for personal. Inline error copy: "Please use your work email."
- `leadFieldOptions.ts` — dropdown option constants reused across forms (timelines, volume buckets, industry, company size, current stage).

Modals (each: shadcn `Dialog`, fields, `MathCaptcha`, Cancel + Submit; on success → sonner `toast.success(...)` then close):

### `TopUpModal.tsx`
- Fields: **How many credits?** (number `Input`), **What's the use case?** (`Input`/short `Textarea`), **Expected monthly call volume** (`Select`: `0–5k`, `5k–20k`, `>20k`).
- **Conditional block** (rendered only when volume === `>20k`): heading "Talk to us about volume pricing", three qualifier fields — **Work email** (validated via `isPersonalEmail`, rejects personal), **Company name**, **Company size** (`Select`: Only me / 2–10 / 10–100 / 100–1000 / 1000+) — and a dashed-underline link **"Need enterprise deployment? (SSO, on-prem, SOC2, data residency)"** → `openEnterprise("topup")`. Below 20k, none of this renders, and those fields are not required.
- Submit → `submitLead("topup", { credits, useCase, volume, workEmail?, company?, companySize?, wantsVolumePricing })`; fires `PostHogEvent.TOPUP_REQUESTED`.

### `HireExpertModal.tsx`
- Fields: **Company name**; **What does your business do?** (1 line); **What do you want the agent to do?** (optional, 2–3 line `Textarea`); **Phone / WhatsApp** (optional); **Timeline** (`Select`: ASAP / 2–4 weeks / 1–2 months / Flexible / Exploring); **Expected monthly call volume** (`Select`: 0–5k / 5k–100k / 100k+ / Not sure); **Existing call scripts/workflows to share?** (`RadioGroup` Yes/No); **Current stage** (`Select`: Have a live process we want to automate / Have an idea, no process yet / Just researching / Already built something, need help fixing).
- Bottom: dashed-underline **enterprise link** → `openEnterprise("hire_expert")`.
- Submit → `submitLead("hire_expert", {...})`; fires `PostHogEvent.HIRE_EXPERT_SUBMITTED`.

### `EnterpriseModal.tsx`
- Fields: **Company name**; **Industry** (`Select`: Financial services / Healthcare / Insurance / Government / Telecom / BPO / Other); **Company size** (`Select`: 50–200 / 200–1000 / 1000–5000 / 5000+); **Timeline** (`Select`: This quarter / Next quarter / 6 months / Exploring); **Work email** (single field, personal-domain rejected); **Phone** (optional); **Anything else we should know?** (optional `Textarea`).
- Submit → `submitLead("enterprise", {...})`; fires `PostHogEvent.ENTERPRISE_LEAD_SUBMITTED`.
- Can be opened standalone or stacked on top of TopUp/Hire (its open state is independent in the provider, so the originating modal can stay open behind it or close — default: open enterprise on top, leave the trigger modal open).

## 4. Workflow-builder nudge (`HireExpertNudge.tsx`)

Mounted inside `RenderWorkflow.tsx` (keyed by `workflowId`).
- A `setTimeout` arms at **5 minutes** of being on the builder. On fire (if eligible), shows a **bottom floating toast/banner** (fixed, bottom-center or bottom-right of the canvas, above the React Flow controls): person icon, **"Hire an Expert"** bold + subtitle **"We'll build your agent for you"**, and a dismiss **×**. Slides in; **auto-fades after 30s**.
- **Eligibility / frequency:** once per `workflowId`, tracked in `localStorage` (`dograh:hireNudge:<workflowId>`). Suppressed entirely if the Hire modal has ever been opened this session (provider exposes an `everOpenedHire` flag) or if the localStorage flag is set.
- **Dismiss semantics:** only an explicit **× dismiss or a click** sets the localStorage "shown/done" flag. **Auto-expiry does NOT** set it — a user who never noticed it remains eligible on a later qualifying visit.
- Clicking the banner → `openHireExpert("builder_nudge")`.
- Events (all with `{ workflowId }`): `HIRE_NUDGE_SHOWN` (on display), `HIRE_NUDGE_CLICKED`, `HIRE_NUDGE_DISMISSED` (×), `HIRE_NUDGE_EXPIRED` (auto-fade).
- Cleanup: clear the timer on unmount / workflow change to avoid firing after navigation.

## 5. Shared modal state (`LeadFormsContext.tsx`)

- New provider in `src/context/`, mounted once in `AppLayout` (wraps the app shell like other providers). Renders the three modals a single time.
- Exposes via `useLeadForms()`:
  - `openHireExpert(source)`, `openTopUp(source)`, `openEnterprise(source)` — each sets its modal open and fires the corresponding `*_OPENED` PostHog event with `{ source }`.
  - `everOpenedHire: boolean` — read by the nudge for suppression.
- Triggers (sidebar button, card buttons, nudge) call the hook — no prop-drilling, no duplicate modal mounts.

## 6. PostHog events (append to `src/constants/posthog-events.ts`)

```ts
HIRE_EXPERT_OPENED: "hire_expert_opened",
HIRE_EXPERT_SUBMITTED: "hire_expert_submitted",
TOPUP_REQUEST_OPENED: "topup_request_opened",
TOPUP_REQUESTED: "topup_requested",
ENTERPRISE_LEAD_OPENED: "enterprise_lead_opened",
ENTERPRISE_LEAD_SUBMITTED: "enterprise_lead_submitted",
HIRE_NUDGE_SHOWN: "hire_nudge_shown",
HIRE_NUDGE_CLICKED: "hire_nudge_clicked",
HIRE_NUDGE_DISMISSED: "hire_nudge_dismissed",
HIRE_NUDGE_EXPIRED: "hire_nudge_expired",
```

Common props: `source` ("sidebar" | "billing_card" | "builder_nudge" | "topup" | "hire_expert"), plus submitted forms include their (non-sensitive) field values; nudge events include `workflowId`.

## Decisions locked during brainstorming

- Route: new `/billing` page; sidebar label **"Credits & Billing"**.
- Credits card **moves** from Agent Runs to `/billing`.
- Card footer = option A (both CTAs in footer, top-up primary, "Running low?" helper left).
- Sidebar footer button = person icon + solid primary, text always visible, collapses with sidebar.
- Hire modal has 3 entry points (sidebar, card, builder nudge) → one shared modal via context.
- Top-up volume gate at **`>20k`** reveals work email + company + enterprise link; hidden below.
- Top-up extra qualifier fields (under `>20k` gate) = **Work email + Company name + Company size** (Only me / 2–10 / 10–100 / 100–1000 / 1000+). Note this company-size scale differs from the Enterprise form's (which is 50–200 / 200–1000 / 1000–5000 / 5000+) — intentional, different audiences.
- Enterprise form uses a **single Work email** field (merged the user's two email lines).
- Captcha = inline client-side math question on all three forms.
- Submissions = PostHog now; one `submitLead()` seam for the future MongoDB endpoint.
- Personal-email rejection via free-domain blocklist.
- Nudge: 5 min trigger, 30s auto-fade, once per `workflowId`, auto-expiry does not consume the eligibility.

## Out of scope (this milestone)

- Backend endpoint / MongoDB persistence (next milestone — wire into `submitLead()`).
- Calendar/Calendly email automation for volume-pricing leads (future).
- Real captcha service (Turnstile/hCaptcha) — inline math is sufficient for OSS for now.
- Any change to the credits/MPS backend API.

## Testing approach

- Type-check (`tsc --noEmit`) and lint clean on all changed/new files.
- Manual dogfood (browse/QA): sidebar rename + new link + footer button (expanded & collapsed); `/billing` renders the card with footer CTAs; Agent Runs no longer shows the card; each modal opens from each trigger, captcha blocks bad answers, personal email rejected, `>20k` reveals the volume block + enterprise link; enterprise opens from both dashed links; nudge appears after the timer (with a shortened dev timer), auto-fades, and respects the once-per-workflow rule. Verify PostHog events fire with correct props.
