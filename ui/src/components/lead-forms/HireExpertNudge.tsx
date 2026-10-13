"use client";

import { UserRound, X } from "lucide-react";
import posthog from "posthog-js";
import { useEffect, useRef, useState } from "react";

import { PostHogEvent } from "@/constants/posthog-events";
import { useLeadForms } from "@/context/LeadFormsContext";

interface HireExpertNudgeProps {
  workflowId: number;
}

// Timings. Override SHOW_DELAY_MS to a few seconds during manual testing.
const SHOW_DELAY_MS = 5 * 60 * 1000; // 5 minutes on the builder
const AUTO_FADE_MS = 30 * 1000; // visible for 30s

function nudgeDoneKey(workflowId: number) {
  return `dograh:hireNudge:${workflowId}`;
}

export function HireExpertNudge({ workflowId }: HireExpertNudgeProps) {
  const { openHireExpert, hasOpenedHireRef } = useLeadForms();
  const [visible, setVisible] = useState(false);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Arm the 5-minute show timer (once per mount / workflow).
  useEffect(() => {
    if (typeof window === "undefined") return;
    // Already shown+consumed for this workflow → skip.
    if (localStorage.getItem(nudgeDoneKey(workflowId))) return;

    const showTimer = setTimeout(() => {
      if (hasOpenedHireRef.current) return; // they engaged elsewhere; don't nag
      if (localStorage.getItem(nudgeDoneKey(workflowId))) return;
      setVisible(true);
      posthog.capture(PostHogEvent.HIRE_NUDGE_SHOWN, { workflowId });
      // Auto-fade after 30s. Auto-expiry does NOT mark done (per spec).
      fadeTimer.current = setTimeout(() => {
        setVisible(false);
        posthog.capture(PostHogEvent.HIRE_NUDGE_EXPIRED, { workflowId });
      }, AUTO_FADE_MS);
    }, SHOW_DELAY_MS);

    return () => {
      clearTimeout(showTimer);
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
    };
  }, [workflowId, hasOpenedHireRef]);

  if (!visible) return null;

  const markDone = () => {
    if (fadeTimer.current) clearTimeout(fadeTimer.current);
    localStorage.setItem(nudgeDoneKey(workflowId), "1");
    setVisible(false);
  };

  const handleClick = () => {
    posthog.capture(PostHogEvent.HIRE_NUDGE_CLICKED, { workflowId });
    markDone();
    openHireExpert("builder_nudge");
  };

  const handleDismiss = () => {
    posthog.capture(PostHogEvent.HIRE_NUDGE_DISMISSED, { workflowId });
    markDone();
  };

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-6 right-6 z-50 flex max-w-xs items-center gap-3 rounded-lg border border-primary bg-background p-3 shadow-lg animate-in fade-in slide-in-from-bottom-2"
    >
      <button type="button" onClick={handleClick} className="flex flex-1 items-center gap-3 text-left">
        <UserRound className="h-5 w-5 shrink-0 text-primary" />
        <span>
          <span className="block text-sm font-semibold">Hire an Expert</span>
          <span className="block text-xs text-muted-foreground">We&apos;ll build your agent for you</span>
        </span>
      </button>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="shrink-0 text-muted-foreground hover:text-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
