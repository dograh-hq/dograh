// Theme overrides for the embedded Stack Auth form. Stack's theme parser
// does not accept OKLCH strings, so keep these values in hex.
// Primary/ring use teal (#5aabb2) to match the --cta accent.

import type { StackTheme } from "@stackframe/stack";
import type { ComponentProps } from "react";

type ThemeConfig = NonNullable<ComponentProps<typeof StackTheme>["theme"]>;

export const stackAuthTheme: ThemeConfig = {
  light: {
    background: "#ffffff",
    foreground: "#1a1a1a",
    card: "#ffffff",
    cardForeground: "#1a1a1a",
    popover: "#ffffff",
    popoverForeground: "#1a1a1a",
    primary: "#5aabb2",
    primaryForeground: "#ffffff",
    secondary: "#f5f5f5",
    secondaryForeground: "#1a1a1a",
    muted: "#f5f5f5",
    mutedForeground: "#737373",
    accent: "#f5f5f5",
    accentForeground: "#1a1a1a",
    destructive: "#ef4444",
    destructiveForeground: "#ffffff",
    border: "#e5e7eb",
    input: "#e5e7eb",
    ring: "#5aabb2",
  },
  dark: {
    background: "#27272a",
    foreground: "#fafafa",
    card: "#27272a",
    cardForeground: "#fafafa",
    popover: "#27272a",
    popoverForeground: "#fafafa",
    primary: "#5aabb2",
    primaryForeground: "#ffffff",
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
    ring: "#5aabb2",
  },
  radius: "0.625rem",
};

/** @deprecated Use `stackAuthTheme` instead. Kept for any imports referencing the old name. */
export const stackAuthDarkTheme = stackAuthTheme;
