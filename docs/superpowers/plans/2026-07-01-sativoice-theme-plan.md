# Sativoice Theme Alignment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align the Dograh fork's UI theme with the Sativoice PoC visual identity: pink brand color, navy accent, Space Grotesk + Manrope fonts, gradient background, updated brand-imprint SVG.

**Architecture:** 4 files changed — layout.tsx (font imports), globals.css (complete CSS variable replacement + gradient + heading rule), and two brand-imprint SVGs. All changes are in `ui/` directory. Theme is CSS-variable-based (shadcn/ui), so the variable swap automatically propagates to all components.

**Tech Stack:** Tailwind CSS v4, next/font/google, shadcn/ui CSS variables, SVG

## Global Constraints

- Use oklch color space for all new color values (consistent with existing codebase)
- `--cta` and `--primary` both get pink — same value, two tokens, no component changes needed
- Keep Geist Mono for code (--font-mono unchanged)
- Keep all existing decorative classes: card-weave, auth-waveform, app-sidebar-dock, lead-form-*
- Dark mode must be fully specified — every variable has both `:root` and `.dark` values
- Brand-imprint SVGs: simple text "sativoice", low opacity (~0.9%), matching current sizing

---

### Task 1: Font swap in layout.tsx

**Files:**
- Modify: `ui/src/app/layout.tsx`

**Interfaces:**
- Produces: `--font-body` (Manrope), `--font-display` (Space Grotesk), `--font-geist-mono` (unchanged)

- [ ] **Step 1: Replace font imports and add new fonts**

Open `ui/src/app/layout.tsx`. Current imports:

```typescript
import { Geist, Geist_Mono } from "next/font/google";
```

Replace with:

```typescript
import { Geist_Mono } from "next/font/google";
import { Manrope, Space_Grotesk } from "next/font/google";
```

- [ ] **Step 2: Add new font instances, keep Geist Mono**

Replace the Geist font instance:

```typescript
const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});
```

With:

```typescript
const bodyFont = Manrope({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-body",
});

const displayFont = Space_Grotesk({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});
```

Keep `Geist_Mono` as-is:

```typescript
const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});
```

- [ ] **Step 3: Update body className**

Replace:

```typescript
className={`${geistSans.variable} ${geistMono.variable} antialiased`}
```

With:

```typescript
className={`${bodyFont.variable} ${displayFont.variable} ${geistMono.variable} antialiased`}
```

- [ ] **Step 4: Commit**

```bash
git add ui/src/app/layout.tsx
git commit -m "feat: swap fonts to Manrope + Space Grotesk for Sativoice theme"
```

---

### Task 2: CSS variable replacement in globals.css

**Files:**
- Modify: `ui/src/app/globals.css`

**Interfaces:**
- Consumes: `--font-body`, `--font-display`, `--font-geist-mono` (Task 1)
- Produces: complete pink/navy theme in `:root` and `.dark`

- [ ] **Step 1: Update @theme block — change font variables**

Replace:

```css
--font-sans: var(--font-geist-sans);
--font-mono: var(--font-geist-mono);
```

With:

```css
--font-sans: var(--font-body);
--font-display: var(--font-display);
--font-mono: var(--font-geist-mono);
```

Add after `--font-mono`:

```css
--font-display: var(--font-display);
```

- [ ] **Step 2: Replace entire `:root` block**

Replace the entire `:root { ... }` block with:

```css
:root {
  --radius: 0.625rem;
  --background: oklch(0.985 0.005 5);
  --foreground: oklch(0.18 0.005 80);
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.18 0.005 80);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.18 0.005 80);
  --primary: oklch(0.62 0.25 5);
  --primary-foreground: oklch(0.985 0 0);
  --secondary: oklch(0.975 0.008 5);
  --secondary-foreground: oklch(0.18 0.005 80);
  --muted: oklch(0.975 0.008 5);
  --muted-foreground: oklch(0.50 0.01 80);
  --accent: oklch(0.15 0.04 280);
  --accent-foreground: oklch(0.985 0 0);
  --destructive: oklch(0.55 0.22 20);
  --destructive-foreground: oklch(0.985 0 0);
  --border: oklch(0.90 0.005 5);
  --input: oklch(0.90 0.005 5);
  --ring: oklch(0.62 0.25 5 / 0.3);
  --chart-1: oklch(0.62 0.25 5);
  --chart-2: oklch(0.55 0.18 230);
  --chart-3: oklch(0.40 0.06 280);
  --chart-4: oklch(0.70 0.15 140);
  --chart-5: oklch(0.65 0.15 60);
  --sidebar: oklch(0.15 0.04 280);
  --sidebar-foreground: oklch(0.985 0 0);
  --sidebar-primary: oklch(0.62 0.25 5);
  --sidebar-primary-foreground: oklch(0.985 0 0);
  --sidebar-accent: oklch(0.20 0.04 280);
  --sidebar-accent-foreground: oklch(0.985 0 0);
  --sidebar-border: oklch(0.25 0.03 280);
  --sidebar-ring: oklch(0.62 0.25 5);
  --cta: oklch(0.62 0.25 5);
  --cta-foreground: oklch(0.985 0 0);
  --brand-imprint: url("/brand-imprint-light.svg");
}
```

- [ ] **Step 3: Replace entire `.dark` block**

Replace the entire `.dark { ... }` block with:

```css
.dark {
  --background: oklch(0.13 0.01 340);
  --foreground: oklch(0.95 0.005 5);
  --card: oklch(0.17 0.01 340);
  --card-foreground: oklch(0.95 0.005 5);
  --popover: oklch(0.17 0.01 340);
  --popover-foreground: oklch(0.95 0.005 5);
  --primary: oklch(0.68 0.24 5);
  --primary-foreground: oklch(0.13 0.01 340);
  --secondary: oklch(0.22 0.01 340);
  --secondary-foreground: oklch(0.95 0.005 5);
  --muted: oklch(0.22 0.01 340);
  --muted-foreground: oklch(0.60 0.01 80);
  --accent: oklch(0.22 0.05 280);
  --accent-foreground: oklch(0.95 0.005 5);
  --destructive: oklch(0.60 0.24 20);
  --destructive-foreground: oklch(0.13 0.01 340);
  --border: oklch(0.30 0.01 340);
  --input: oklch(0.25 0.01 340);
  --ring: oklch(0.68 0.24 5 / 0.4);
  --chart-1: oklch(0.68 0.24 5);
  --chart-2: oklch(0.62 0.20 230);
  --chart-3: oklch(0.50 0.08 280);
  --chart-4: oklch(0.72 0.16 140);
  --chart-5: oklch(0.70 0.16 60);
  --sidebar: oklch(0.15 0.04 280);
  --sidebar-foreground: oklch(0.95 0.005 5);
  --sidebar-primary: oklch(0.68 0.24 5);
  --sidebar-primary-foreground: oklch(0.13 0.01 340);
  --sidebar-accent: oklch(0.22 0.05 280);
  --sidebar-accent-foreground: oklch(0.95 0.005 5);
  --sidebar-border: oklch(0.25 0.04 280);
  --sidebar-ring: oklch(0.68 0.24 5);
  --cta: oklch(0.68 0.24 5);
  --cta-foreground: oklch(0.13 0.01 340);
  --brand-imprint: url("/brand-imprint-dark.svg");
}
```

- [ ] **Step 4: Add heading font rule and gradient body**

In `@layer base`, replace:

```css
body {
  @apply bg-background text-foreground;
}
```

With:

```css
h1, h2, h3, h4 {
  font-family: var(--font-display);
}

body {
  color: var(--foreground);
  background: linear-gradient(
    180deg,
    oklch(0.988 0.006 5) 0%,
    oklch(0.975 0.008 5) 100%
  );
}

.dark body {
  background: linear-gradient(
    180deg,
    oklch(0.13 0.01 340) 0%,
    oklch(0.15 0.01 340) 100%
  );
}
```

- [ ] **Step 5: Update .app-surface — remove background-color**

Find `.app-surface` in `@layer components` and remove its `background-color` line (keep `background-image`, `background-size`, etc.). The body gradient now shines through.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/globals.css
git commit -m "feat: pink/navy Sativoice theme — CSS variables, gradient, heading font"
```

---

### Task 3: Brand-imprint SVG replacement

**Files:**
- Modify: `ui/public/brand-imprint-light.svg`
- Modify: `ui/public/brand-imprint-dark.svg`

**Interfaces:**
- Consumes: `--brand-imprint` CSS variable (unchanged in globals.css)
- Produces: "sativoice" watermark at 0.9% opacity

- [ ] **Step 1: Create brand-imprint-light.svg**

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 980 120" width="980" height="120">
  <text x="490" y="75"
    font-family="system-ui, -apple-system, sans-serif"
    font-size="64"
    font-weight="800"
    fill="#000000"
    fill-opacity="0.009"
    text-anchor="middle"
    letter-spacing="-0.02em"
  >sativoice</text>
</svg>
```

- [ ] **Step 2: Create brand-imprint-dark.svg**

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 980 120" width="980" height="120">
  <text x="490" y="75"
    font-family="system-ui, -apple-system, sans-serif"
    font-size="64"
    font-weight="800"
    fill="#ffffff"
    fill-opacity="0.009"
    text-anchor="middle"
    letter-spacing="-0.02em"
  >sativoice</text>
</svg>
```

- [ ] **Step 3: Commit**

```bash
git add ui/public/brand-imprint-light.svg ui/public/brand-imprint-dark.svg
git commit -m "feat: sativoice brand-imprint SVGs for light and dark themes"
```

---

### Task 4: Verification

**Files:**
- Verify: no build errors, CSS variables resolve

- [ ] **Step 1: Check for TypeScript/Next.js build errors**

```bash
cd ui && npx next build 2>&1 | tail -20
```

Expected: no errors. May have warnings (unused CSS, etc.) — acceptable.

- [ ] **Step 2: Verify CSS variable syntax**

```bash
grep -c "oklch" ui/src/app/globals.css
```

Should output a count > 60 (light + dark variables all in oklch).

- [ ] **Step 3: Verify brand-imprint files exist**

```bash
ls -la ui/public/brand-imprint-light.svg ui/public/brand-imprint-dark.svg
```

- [ ] **Step 4: Commit if any residual changes**

```bash
git status
git diff --stat
```
