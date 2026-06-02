"use client";

import { DograhCreditsCard } from "@/components/billing/DograhCreditsCard";

export default function BillingPage() {
  return (
    <div className="container mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Credits &amp; Billing</h1>
        <p className="text-muted-foreground">
          Track your Dograh model credits and request top-ups.
        </p>
      </div>
      <DograhCreditsCard />
    </div>
  );
}
