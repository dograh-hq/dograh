// Dark token overrides for the embedded Stack Auth form so it blends into the
// auth card surface (zinc-900 background, zinc-100 foreground, the warm CTA
// accent on the primary button, zinc-800 borders/inputs). Kept in sync with the
// .dark tokens in globals.css. Values are CSS color strings; Stack applies them
// to its own CSS variables.

import type { StackTheme } from "@stackframe/stack";
import type { ComponentProps } from "react";

type ThemeConfig = NonNullable<ComponentProps<typeof StackTheme>["theme"]>;

export const stackAuthDarkTheme: ThemeConfig = {
  dark: {
    background: "oklch(0.205 0 0)",
    foreground: "oklch(0.985 0 0)",
    card: "oklch(0.205 0 0)",
    cardForeground: "oklch(0.985 0 0)",
    popover: "oklch(0.205 0 0)",
    popoverForeground: "oklch(0.985 0 0)",
    primary: "oklch(0.78 0.16 67)",
    primaryForeground: "oklch(0.16 0.02 60)",
    secondary: "oklch(0.269 0 0)",
    secondaryForeground: "oklch(0.985 0 0)",
    muted: "oklch(0.269 0 0)",
    mutedForeground: "oklch(0.708 0 0)",
    accent: "oklch(0.269 0 0)",
    accentForeground: "oklch(0.985 0 0)",
    destructive: "oklch(0.704 0.191 22.216)",
    destructiveForeground: "oklch(0.985 0 0)",
    border: "oklch(0.269 0 0)",
    input: "oklch(0.269 0 0)",
    ring: "oklch(0.78 0.16 67)",
  },
  radius: "0.625rem",
};
