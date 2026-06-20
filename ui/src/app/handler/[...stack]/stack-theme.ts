// Dark token overrides for the embedded Stack Auth form so it blends into the
// auth card surface (zinc-900 background, zinc-100 foreground, the warm CTA
// accent on the primary button, zinc-800 borders/inputs). Stack's theme parser
// does not accept OKLCH strings, so keep these values in hex.

import type { StackTheme } from "@stackframe/stack";
import type { ComponentProps } from "react";

type ThemeConfig = NonNullable<ComponentProps<typeof StackTheme>["theme"]>;

export const stackAuthDarkTheme: ThemeConfig = {
  dark: {
    background: "#27272a",
    foreground: "#fafafa",
    card: "#27272a",
    cardForeground: "#fafafa",
    popover: "#27272a",
    popoverForeground: "#fafafa",
    primary: "#fbbf24",
    primaryForeground: "#422006",
    secondary: "#3f3f46",
    secondaryForeground: "#fafafa",
    muted: "#3f3f46",
    mutedForeground: "#a1a1aa",
    accent: "#3f3f46",
    accentForeground: "#fafafa",
    destructive: "#ef4444",
    destructiveForeground: "#fafafa",
    border: "#3f3f46",
    input: "#3f3f46",
    ring: "#fbbf24",
  },
  radius: "0.625rem",
};
