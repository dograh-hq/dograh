"use client";

import { UserRound } from "lucide-react";
import posthog from "posthog-js";
import { useCallback, useEffect, useState } from "react";

import { getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet } from "@/client/sdk.gen";
import type { MpsCreditsResponse } from "@/client/types.gen";
import { BuyCreditsControl } from "@/components/billing/BuyCreditsControl";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { PostHogEvent } from "@/constants/posthog-events";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useAuth } from "@/lib/auth";

export function DograhCreditsCard() {
  const auth = useAuth();
  const { openHireExpert, openEnterprise } = useLeadForms();
  const [mpsCredits, setMpsCredits] = useState<MpsCreditsResponse | null>(null);
  const [isLoadingCredits, setIsLoadingCredits] = useState(true);

  const fetchMpsCredits = useCallback(async () => {
    if (!auth.isAuthenticated) return;
    try {
      const response = await getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet();
      // The generated client resolves to { data, error } and does NOT throw on
      // 4xx/5xx (see ui/AGENTS.md) — check error explicitly.
      if (response.error) {
        console.error("Failed to fetch MPS credits:", response.error);
      } else if (response.data) {
        setMpsCredits(response.data);
      }
    } catch (error) {
      console.error("Failed to fetch MPS credits:", error);
    } finally {
      setIsLoadingCredits(false);
    }
  }, [auth.isAuthenticated]);

  useEffect(() => {
    if (auth.isAuthenticated) {
      fetchMpsCredits();
    }
  }, [auth.isAuthenticated, fetchMpsCredits]);

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>Dograh Model Credits</CardTitle>
        <CardDescription>
          These track usage of Dograh models using Dograh Service Keys.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoadingCredits ? (
          <div className="animate-pulse space-y-4">
            <div className="h-4 bg-muted rounded w-1/4"></div>
            <div className="h-8 bg-muted rounded"></div>
            <div className="h-4 bg-muted rounded w-1/3"></div>
          </div>
        ) : mpsCredits ? (
          <div className="space-y-4">
            <div className="flex justify-between items-baseline">
              <div>
                <p className="text-2xl font-bold">
                  {mpsCredits.total_credits_used.toFixed(2)}{" "}
                  <span className="text-lg font-normal text-muted-foreground">
                    / {mpsCredits.total_quota.toFixed(2)}
                  </span>
                </p>
                <p className="text-sm text-muted-foreground">Credits Used</p>
              </div>
              <div className="text-right">
                <p className="text-lg font-semibold">{mpsCredits.remaining_credits.toFixed(2)}</p>
                <p className="text-sm text-muted-foreground">Remaining</p>
              </div>
            </div>

            {mpsCredits.total_quota > 0 && (
              <Progress value={Math.min(100, (mpsCredits.total_credits_used / mpsCredits.total_quota) * 100)} className="h-3" />
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">
            No Dograh service keys configured. Set up a service key in your model configuration to see usage.
          </p>
        )}

        {/* Footer CTAs — self-serve + done-for-you side by side, with the
            custom-pricing link directly beneath. */}
        <div className="mt-6 space-y-4 border-t pt-4">
          <div className="space-y-1">
            <p className="text-sm font-medium">Running low?</p>
            <p className="text-sm text-muted-foreground">Top up instantly, or have us build it for you.</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <BuyCreditsControl className="w-full sm:flex-1" />
            <Button
              variant="outline"
              className="w-full gap-2 sm:flex-1"
              onClick={() => openHireExpert("billing_card")}
            >
              <UserRound className="h-4 w-4" />
              Hire an Expert
            </Button>
          </div>
          <button
            type="button"
            onClick={() => {
              posthog.capture(PostHogEvent.CUSTOM_PRICING_CLICKED);
              openEnterprise("billing_custom_pricing");
            }}
            className="block text-xs text-muted-foreground underline decoration-dashed underline-offset-4 hover:text-foreground"
          >
            Book a Strategy Call: custom pricing for committed volume
          </button>
        </div>
      </CardContent>
    </Card>
  );
}
