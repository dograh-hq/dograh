"use client";

// Shared chrome for the lead dialogs (HireExpert, Enterprise, post-signup
// Onboarding). Wraps the existing @/components/ui/dialog primitive (which already
// supplies the blurred backdrop) and adds a consistent header (icon + eyebrow +
// title), a scrollable body, a sticky footer (primary CTA + optional ghost
// secondary + optional helper slot), and a bottom trust-line slot. The visual
// language is refined dark minimalism: zinc surface, hairline border, one warm
// accent reserved for the primary action.

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
  icon: LucideIcon;
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
  icon: Icon,
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
          "max-h-[90vh] gap-0 overflow-hidden p-0 sm:max-w-[520px]",
          contentClassName,
        )}
        {...restContentProps}
      >
        {/* Header */}
        <DialogHeader className="space-y-0 border-b border-border/60 px-6 py-5 text-left">
          <div className="flex items-start gap-4">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border/70 bg-muted/40 text-cta">
              <Icon className="size-5" />
            </span>
            <div className="min-w-0 space-y-1">
              {eyebrow && (
                <span className="block text-[0.7rem] font-medium uppercase tracking-[0.14em] text-cta/90">
                  {eyebrow}
                </span>
              )}
              <DialogTitle className="text-lg font-semibold leading-tight">
                {title}
              </DialogTitle>
              {description && (
                <DialogDescription className="text-sm leading-snug">
                  {description}
                </DialogDescription>
              )}
            </div>
          </div>
        </DialogHeader>

        {/* Scrollable body */}
        <div className="max-h-[60vh] overflow-y-auto px-6 py-5">{children}</div>

        {/* Sticky footer — actions first, then the optional helper line BELOW
            the buttons, then the trust line at the very bottom. */}
        <div className="space-y-3 border-t border-border/60 bg-background/80 px-6 py-4 backdrop-blur-sm">
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
              className="bg-cta text-cta-foreground shadow-xs hover:bg-cta/90 focus-visible:ring-cta/50"
            >
              {primary.loading ? "Submitting…" : primary.label}
            </Button>
          </div>
          {helper && <div className="text-center text-xs text-muted-foreground">{helper}</div>}
          {trustLine}
        </div>

        {/* Optional popup floated on top of the entire modal (captcha, etc.). */}
        {overlay && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/85 p-6 backdrop-blur-sm">
            {overlay}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
