"use client";

import { UserRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet } from "@/client/sdk.gen";
import type { MpsCreditsResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useAuth } from "@/lib/auth";

export function DograhCreditsCard() {
  const auth = useAuth();
  const { openHireExpert, openTopUp } = useLeadForms();
  const [mpsCredits, setMpsCredits] = useState<MpsCreditsResponse | null>(null);
  const [isLoadingCredits, setIsLoadingCredits] = useState(true);

  const fetchMpsCredits = useCallback(async () => {
    if (!auth.isAuthenticated) return;
    try {
      const response = await getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet();
      if (response.data) {
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
              <Progress value={(mpsCredits.total_credits_used / mpsCredits.total_quota) * 100} className="h-3" />
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">
            No Dograh service keys configured. Set up a service key in your model configuration to see usage.
          </p>
        )}

        {/* Footer CTAs — card ends with an action */}
        <div className="mt-6 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-sm text-muted-foreground">Running low?</span>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <Button variant="outline" className="gap-2" onClick={() => openHireExpert("billing_card")}>
              <UserRound className="h-4 w-4" />
              Hire an Expert
            </Button>
            <Button onClick={() => openTopUp("billing_card")}>Request top-up</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
