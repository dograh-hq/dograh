"use client";

// Shared chrome for the lead dialogs (HireExpert, Enterprise, post-signup
// Onboarding). Wraps the existing @/components/ui/dialog primitive (which already
// supplies the blurred backdrop) and adds a consistent header band (eyebrow +
// title + description), a scrollable body with underline fields, a footer
// (primary CTA + optional ghost secondary + optional helper slot), and a bottom
// trust-line slot. The visual language ("Ledger", user-approved): flat charcoal
// slab where ONLY the header band is darker (footer matches the body), NO
// gradients/glows/icons, Geist type only, one warm accent reserved for the
// primary action and the focused-field underline (see .lead-form-* in
// globals.css).

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface LeadModalShellProps {
  // Accepted for caller compatibility; the Ledger design renders no icon.
  icon?: LucideIcon;
  title: string;
  eyebrow?: string;
  description?: string;
  children: ReactNode;
  // Primary action — rendered with the warm CTA accent.
  primary: { label: string; onClick: () => void; disabled?: boolean; loading?: boolean };
  // Optional ghost secondary (e.g. Cancel / Skip).
  secondary?: { label: string; onClick: () => void; disabled?: boolean };
  // Optional helper rendered in the footer below the actions (e.g. a link).
  helper?: ReactNode;
  // Optional trust line beneath the footer (we pass <FormTrustLine/>).
  trustLine?: ReactNode;
  // Optional layer floated ON TOP of the whole modal (e.g. the captcha popup).
  overlay?: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // Forwarded to DialogContent so callers can lock dismissal (onboarding gate).
  contentProps?: React.ComponentProps<typeof DialogContent>;
}

export function LeadModalShell({
  title,
  eyebrow,
  description,
  children,
  primary,
  secondary,
  helper,
  trustLine,
  overlay,
  open,
  onOpenChange,
  contentProps,
}: LeadModalShellProps) {
  const { className: contentClassName, ...restContentProps } = contentProps ?? {};

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "lead-form-slab max-h-[90vh] gap-0 overflow-hidden rounded-2xl border-border/70 bg-card p-0 shadow-2xl sm:max-w-[560px]",
          contentClassName,
        )}
        {...restContentProps}
      >
        {/* Header: a slightly darker band, separated by a hairline. */}
        <DialogHeader className="space-y-0 border-b border-border/40 bg-black/[0.04] px-8 pb-5 pt-6 text-left dark:bg-black/25">
          <div className="min-w-0">
            {eyebrow && (
              <span className="block text-[0.7rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                {eyebrow}
              </span>
            )}
            <DialogTitle className="mt-1.5 text-2xl font-semibold leading-tight tracking-tight">
              {title}
            </DialogTitle>
            {description && (
              <DialogDescription className="mt-1.5 text-sm leading-snug">
                {description}
              </DialogDescription>
            )}
          </div>
        </DialogHeader>

        {/* Scrollable body: flat, compact underline fields. */}
        <div className="max-h-[60vh] overflow-y-auto px-8 py-6">
          <div className="lead-form-underline">{children}</div>
        </div>

        {/* Footer — same surface as the body (only the header band differs);
            actions first, then the optional helper line BELOW the buttons,
            then the trust line at the very bottom. */}
        <div className="space-y-3 border-t border-border/40 px-8 py-4">
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
            {secondary && (
              <Button
                type="button"
                variant="ghost"
                onClick={secondary.onClick}
                disabled={secondary.disabled}
              >
                {secondary.label}
              </Button>
            )}
            <Button
              type="button"
              onClick={primary.onClick}
              disabled={primary.disabled || primary.loading}
              className="bg-cta text-cta-foreground shadow-md shadow-cta/25 hover:bg-cta/90 hover:shadow-cta/35 focus-visible:ring-cta/50"
            >
              {primary.loading ? "Submitting…" : primary.label}
            </Button>
          </div>
          {helper && <div className="text-center text-xs text-muted-foreground">{helper}</div>}
          {trustLine}
        </div>

        {/* Optional popup floated on top of the entire modal (captcha, etc.). */}
        {overlay && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/70 p-6 backdrop-blur-md">
            {overlay}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
