"use client";

import { Phone } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

interface Did {
  did_id: number;
  did_number: number | string | null;
  type_label?: string | null;
  country_code?: number | string | null;
}

// Buys a phone number from the reseller pool (after KYC), charged to the credit
// balance. Calls go through the shared hey-api client.
export function PhoneNumbersSection() {
  const { user, loading: authLoading } = useAuth();
  const [available, setAvailable] = useState<Did[]>([]);
  const [owned, setOwned] = useState<Did[]>([]);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState<number | null>(null);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    refresh();
  }, [authLoading, user]);

  async function refresh() {
    try {
      const [a, m] = await Promise.all([
        client.get({ url: "/api/v1/telephony/marketplace/numbers" }),
        client.get({ url: "/api/v1/telephony/marketplace/my-numbers" }),
      ]);
      setAvailable(((a.data as { numbers?: Did[] })?.numbers) ?? []);
      setOwned(((m.data as { numbers?: Did[] })?.numbers) ?? []);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }

  async function buy(did: Did) {
    setBuying(did.did_id);
    try {
      const res = await client.post({
        url: "/api/v1/telephony/marketplace/buy",
        body: { did_id: did.did_id },
      });
      if (res.error) {
        const detail = (res.error as { detail?: string })?.detail ?? "";
        if (detail.includes("kyc") || (res.response?.status === 403))
          toast.error("Complete KYC before buying a number");
        else if (detail.includes("not_provisioned"))
          toast.error("Your telephony account isn't set up yet — contact support");
        else if (detail.includes("insufficient"))
          toast.error("Not enough credits — top up first");
        else toast.error("Couldn't buy this number");
        return;
      }
      toast.success(`Number ${did.did_number} is yours!`);
      await refresh();
    } catch {
      toast.error("Couldn't buy this number");
    } finally {
      setBuying(null);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="text-sm font-medium">Your numbers</p>
        {owned.length === 0 ? (
          <p className="text-sm text-muted-foreground">You don&apos;t own any numbers yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {owned.map((d) => (
              <span
                key={d.did_id}
                className="inline-flex items-center gap-1 rounded-md border px-3 py-1 text-sm"
              >
                <Phone className="h-3.5 w-3.5" /> {d.did_number}
                {d.type_label ? (
                  <span className="text-muted-foreground">· {d.type_label}</span>
                ) : null}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">Available to buy</p>
        {available.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No numbers available right now — check back soon.
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {available.map((d) => (
              <div
                key={d.did_id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div>
                  <p className="font-mono text-sm">{d.did_number}</p>
                  {d.type_label ? (
                    <p className="text-xs text-muted-foreground">{d.type_label}</p>
                  ) : null}
                </div>
                <Button size="sm" disabled={buying === d.did_id} onClick={() => buy(d)}>
                  {buying === d.did_id ? "Buying..." : "Buy"}
                </Button>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Requires completed KYC. Charged to your call-credit balance.
        </p>
      </div>
    </div>
  );
}
