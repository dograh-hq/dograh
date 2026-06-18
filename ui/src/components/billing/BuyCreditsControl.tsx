"use client";

// Compact self-serve "Buy Credits" control. The amount chips + custom input live
// in a popover that only opens when the user clicks "Buy Credits" — so the
// billing card stays clean until they intend to top up. Presets + custom (min $5)
// feed the Razorpay seam in @/lib/billing/topup, which currently throws "not
// wired yet"; we surface that as a calm inline note rather than an error toast.

import posthog from "posthog-js";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { PostHogEvent } from "@/constants/posthog-events";
import { MAX_TOPUP_USD, MIN_TOPUP_USD, startTopUp, TOPUP_PRESETS } from "@/lib/billing/topup";
import { cn } from "@/lib/utils";

// Round to whole cents and reject non-positive / non-finite input so a typo
// (e.g. "5.999", "-1", "abc") can't produce a NaN or fractional-cent order.
const parseAmount = (raw: string): number | null => {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.round(n * 100) / 100;
};

export function BuyCreditsControl({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The effective amount: a parsed custom value takes precedence when present.
  const customAmount = custom.trim() ? parseAmount(custom) : null;
  const amount = customAmount ?? selected;
  const valid = amount != null && amount >= MIN_TOPUP_USD && amount <= MAX_TOPUP_USD;

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
    const parsed = parseAmount(raw);
    if (parsed != null && parsed >= MIN_TOPUP_USD && parsed <= MAX_TOPUP_USD) {
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
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          className={cn(
            "bg-cta text-cta-foreground shadow-xs hover:bg-cta/90 focus-visible:ring-cta/50",
            className,
          )}
        >
          Buy Credits
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 space-y-3">
        <div className="space-y-0.5">
          <p className="text-sm font-medium">Top up credits</p>
          <p className="text-xs text-muted-foreground">Pick an amount (min ${MIN_TOPUP_USD}).</p>
        </div>

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
              className="h-9 w-24 pl-5"
            />
          </div>
        </div>

        {error && <p className="text-xs text-muted-foreground">{error}</p>}

        <Button
          type="button"
          onClick={onBuy}
          disabled={!valid || busy}
          className="w-full bg-cta text-cta-foreground shadow-xs hover:bg-cta/90 focus-visible:ring-cta/50"
        >
          {busy ? "Starting…" : valid && amount != null ? `Buy $${amount}` : "Buy Credits"}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
