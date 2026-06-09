"use client";

// Compact self-serve "Buy Credits" control for the billing card. Preset amount
// chips plus a custom amount (min $5) feed the Razorpay seam in
// @/lib/billing/topup. Analytics: chip selection and the buy click are captured
// for funnel analysis. The seam currently throws "not wired yet"; we surface
// that as a calm inline note rather than an error toast.

import posthog from "posthog-js";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PostHogEvent } from "@/constants/posthog-events";
import { MIN_TOPUP_USD, startTopUp, TOPUP_PRESETS } from "@/lib/billing/topup";
import { cn } from "@/lib/utils";

export function BuyCreditsControl() {
  const [selected, setSelected] = useState<number | null>(null);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The effective amount: a parsed custom value takes precedence when present.
  const customAmount = custom.trim() ? Number(custom) : null;
  const amount = customAmount ?? selected;
  const valid = amount != null && Number.isFinite(amount) && amount >= MIN_TOPUP_USD;

  const selectPreset = (value: number) => {
    setSelected(value);
    setCustom("");
    setError(null);
    posthog.capture(PostHogEvent.BUY_CREDITS_AMOUNT_SELECTED, { amount: value });
  };

  const onCustomChange = (raw: string) => {
    setCustom(raw);
    setSelected(null);
    setError(null);
    const parsed = Number(raw);
    if (raw.trim() && Number.isFinite(parsed) && parsed >= MIN_TOPUP_USD) {
      posthog.capture(PostHogEvent.BUY_CREDITS_AMOUNT_SELECTED, { amount: parsed });
    }
  };

  const onBuy = async () => {
    if (!valid || amount == null) return;
    setBusy(true);
    setError(null);
    posthog.capture(PostHogEvent.BUY_CREDITS_CLICKED, { amount });
    try {
      await startTopUp(amount);
    } catch {
      // The seam is intentionally unimplemented until Razorpay lands.
      setError("Self-serve top-up is coming soon. Use \"Hire an Expert\" or contact us for now.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {TOPUP_PRESETS.map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => selectPreset(value)}
            aria-pressed={selected === value}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
              "border-input text-foreground hover:bg-accent",
              selected === value && "border-cta bg-cta/10 text-foreground ring-1 ring-cta/40",
            )}
          >
            ${value}
          </button>
        ))}
        <div className="relative">
          <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">
            $
          </span>
          <Input
            inputMode="decimal"
            value={custom}
            onChange={(e) => onCustomChange(e.target.value)}
            placeholder="Custom"
            aria-label={`Custom amount (min $${MIN_TOPUP_USD})`}
            className="h-9 w-28 pl-5"
          />
        </div>
      </div>

      {error ? (
        <p className="text-xs text-muted-foreground">{error}</p>
      ) : (
        <p className="text-xs text-muted-foreground">Minimum ${MIN_TOPUP_USD}.</p>
      )}

      <Button
        type="button"
        onClick={onBuy}
        disabled={!valid || busy}
        className="bg-cta text-cta-foreground shadow-xs hover:bg-cta/90 focus-visible:ring-cta/50"
      >
        {busy ? "Starting…" : "Buy Credits"}
      </Button>
    </div>
  );
}
