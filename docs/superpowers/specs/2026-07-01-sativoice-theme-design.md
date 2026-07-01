# Sativoice Theme Alignment — Technical Design

**Date:** 2026-07-01
**Status:** Draft
**Author:** andreab

## Goal

Align the Dograh fork's UI theme with the Sativoice PoC visual identity:
- Brand color: pink (`#fd0b65`) replacing amber (`--cta`)
- Accent: navy (`#161b32`) replacing neutral charcoal
- Fonts: Space Grotesk (headings) + Manrope (body) + keep Geist Mono (code)
- Background gradient (light mode)
- Updated brand-imprint SVG ("sativoice" watermark)
- Keep: dark mode, card-weave, auth-waveform

## Font Mapping

| Before | After | CSS Variable | Usage |
|---|---|---|---|
| `Geist` (next/font) | `Manrope` (next/font) | `--font-body` → `--font-sans` | Body text |
| `Geist_Mono` (next/font) | `Geist_Mono` (unchanged) | `--font-geist-mono` → `--font-mono` | Code blocks |
| _(none)_ | `Space_Grotesk` (next/font) | `--font-display` | Headings (h1-h4) |

### layout.tsx changes

```typescript
import { Manrope, Space_Grotesk } from "next/font/google";
// Geist_Mono stays imported

const bodyFont = Manrope({
  subsets: ["latin"], display: "swap", variable: "--font-body",
});
const displayFont = Space_Grotesk({
  subsets: ["latin"], display: "swap", variable: "--font-display",
});
```

### globals.css @theme changes

```css
--font-sans: var(--font-body);
--font-display: var(--font-display);
--font-mono: var(--font-geist-mono);  /* unchanged */
```

### @layer base addition

```css
h1, h2, h3, h4 {
  font-family: var(--font-display);
}
```

## Complete Variable Mapping

### Light Mode (`:root`)

| CSS Variable | Value (oklch) | Derived From |
|---|---|---|
| `--background` | `oklch(0.985 0.005 5)` | PoC gradient top `#fff7fb` |
| `--foreground` | `oklch(0.18 0.005 80)` | PoC `--ink` `#1d1b17` |
| `--card` | `oklch(1 0 0)` | `#ffffff` |
| `--card-foreground` | `oklch(0.18 0.005 80)` | same as foreground |
| `--popover` | `oklch(1 0 0)` | `#ffffff` |
| `--popover-foreground` | `oklch(0.18 0.005 80)` | same as foreground |
| `--primary` | `oklch(0.62 0.25 5)` | PoC `--brand` `#fd0b65` |
| `--primary-foreground` | `oklch(0.985 0 0)` | white on pink |
| `--secondary` | `oklch(0.975 0.008 5)` | PoC `--surface-2` `#fff3f8` |
| `--secondary-foreground` | `oklch(0.18 0.005 80)` | ink |
| `--muted` | `oklch(0.975 0.008 5)` | same as secondary |
| `--muted-foreground` | `oklch(0.48 0.01 80)` | PoC `--muted` `#6f6a61` |
| `--accent` | `oklch(0.15 0.04 280)` | PoC `--accent` `#161b32` |
| `--accent-foreground` | `oklch(0.985 0 0)` | white on navy |
| `--destructive` | `oklch(0.55 0.22 20)` | red (unchanged feel) |
| `--destructive-foreground` | `oklch(0.985 0 0)` | white |
| `--border` | `oklch(0.90 0.005 5)` | light pink-gray |
| `--input` | `oklch(0.90 0.005 5)` | same as border |
| `--ring` | `oklch(0.62 0.25 5 / 0.3)` | pink with alpha |
| `--chart-1` | `oklch(0.62 0.25 5)` | pink (brand) |
| `--chart-2` | `oklch(0.55 0.18 230)` | blue |
| `--chart-3` | `oklch(0.40 0.06 280)` | navy |
| `--chart-4` | `oklch(0.70 0.15 140)` | green |
| `--chart-5` | `oklch(0.65 0.15 60)` | amber |
| `--sidebar` | `oklch(0.15 0.04 280)` | navy (matches accent) |
| `--sidebar-foreground` | `oklch(0.985 0 0)` | white on navy |
| `--sidebar-primary` | `oklch(0.62 0.25 5)` | pink |
| `--sidebar-primary-foreground` | `oklch(0.985 0 0)` | white |
| `--sidebar-accent` | `oklch(0.20 0.04 280)` | lighter navy |
| `--sidebar-accent-foreground` | `oklch(0.985 0 0)` | white |
| `--sidebar-border` | `oklch(0.25 0.03 280)` | navy border |
| `--sidebar-ring` | `oklch(0.62 0.25 5)` | pink ring |
| `--cta` | `oklch(0.62 0.25 5)` | **MERGED with --primary** (both pink) |
| `--cta-foreground` | `oklch(0.985 0 0)` | white on pink |

### Dark Mode (`.dark`)

| CSS Variable | Value (oklch) | Notes |
|---|---|---|
| `--background` | `oklch(0.13 0.01 340)` | dark with subtle pink tint |
| `--foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--card` | `oklch(0.17 0.01 340)` | dark card |
| `--card-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--popover` | `oklch(0.17 0.01 340)` | same as card |
| `--popover-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--primary` | `oklch(0.68 0.24 5)` | brighter pink for dark bg |
| `--primary-foreground` | `oklch(0.13 0.01 340)` | dark text on pink button |
| `--secondary` | `oklch(0.22 0.01 340)` | dark secondary |
| `--secondary-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--muted` | `oklch(0.22 0.01 340)` | same as secondary |
| `--muted-foreground` | `oklch(0.60 0.01 80)` | muted gray |
| `--accent` | `oklch(0.22 0.05 280)` | lighter navy for dark bg |
| `--accent-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--destructive` | `oklch(0.60 0.24 20)` | brighter red for dark |
| `--destructive-foreground` | `oklch(0.13 0.01 340)` | dark text |
| `--border` | `oklch(0.30 0.01 340)` | dark border |
| `--input` | `oklch(0.25 0.01 340)` | dark input |
| `--ring` | `oklch(0.68 0.24 5 / 0.4)` | pink with alpha |
| `--chart-1` | `oklch(0.68 0.24 5)` | pink |
| `--chart-2` | `oklch(0.62 0.20 230)` | blue |
| `--chart-3` | `oklch(0.50 0.08 280)` | navy |
| `--chart-4` | `oklch(0.72 0.16 140)` | green |
| `--chart-5` | `oklch(0.70 0.16 60)` | amber |
| `--sidebar` | `oklch(0.15 0.04 280)` | navy (same as light) |
| `--sidebar-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--sidebar-primary` | `oklch(0.68 0.24 5)` | pink |
| `--sidebar-primary-foreground` | `oklch(0.13 0.01 340)` | dark text |
| `--sidebar-accent` | `oklch(0.22 0.05 280)` | lighter navy |
| `--sidebar-accent-foreground` | `oklch(0.95 0.005 5)` | near-white |
| `--sidebar-border` | `oklch(0.25 0.04 280)` | navy border |
| `--sidebar-ring` | `oklch(0.68 0.24 5)` | pink ring |
| `--cta` | `oklch(0.68 0.24 5)` | **MERGED with --primary** |
| `--cta-foreground` | `oklch(0.13 0.01 340)` | dark text |

## Background Gradient

Applied to `body` (matches PoC), replacing the current `@apply bg-background`:

```css
body {
  color: var(--foreground);
  background: linear-gradient(
    180deg,
    oklch(0.988 0.006 5) 0%,   /* #fff7fb equivalent */
    oklch(0.975 0.008 5) 100%  /* #fff3f8 equivalent */
  );
}
```

`.dark` override:
```css
.dark body {
  background: linear-gradient(
    180deg,
    oklch(0.13 0.01 340) 0%,
    oklch(0.15 0.01 340) 100%
  );
}
```

`.app-surface` background-color is replaced by this body gradient. The `.app-surface` class keeps the brand-imprint (updated to "sativoice") but drops its background-color (it's transparent, letting the body gradient show through).

## Brand Imprint

Replace `public/brand-imprint-light.svg` and `public/brand-imprint-dark.svg` with simple text-based SVGs:

**Light:** "sativoice" in light gray at very low opacity (0.9%), same sizing as current.
**Dark:** "sativoice" in white at very low opacity (0.9%).

Generated as inline text SVG. The `.app-surface` and `.auth-imprint` CSS classes remain unchanged — they just consume the new SVG files.

## Files Changed

| File | Change |
|---|---|
| `ui/src/app/layout.tsx` | Font imports: add Manrope + Space Grotesk, keep Geist_Mono |
| `ui/src/app/globals.css` | Full variable replacement (light + dark), gradient, heading font rule, @theme font update |
| `ui/public/brand-imprint-light.svg` | Replace: "sativoice" text SVG |
| `ui/public/brand-imprint-dark.svg` | Replace: "sativoice" text SVG |

## What Stays

| Element | Status |
|---|---|
| `card-weave` class | ✅ Unchanged |
| `auth-waveform` animation | ✅ Unchanged |
| `app-sidebar-dock` styles | ✅ Unchanged (backgrounds adapted via variables) |
| `lead-form-slab` / `lead-form-underline` | ✅ Unchanged |
| Dark mode toggle | ✅ Unchanged |
| `--radius` | ✅ Unchanged |
| ThemeProvider, next-themes | ✅ Unchanged |

## CTA Merge Decision

`--cta` is merged into `--primary`. Both now use the pink brand color. This simplifies the system (matches PoC's single-brand approach). Components using `bg-cta` / `text-cta` will need a search-and-replace to `bg-primary` / `text-primary`, OR `--cta` keeps its own value identical to `--primary`. **Decision: keep both variables with identical values** — components built before the merge still work, new code uses `--primary`.
