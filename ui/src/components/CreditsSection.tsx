"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

interface PackFeatures {
  api: boolean;
  mcp: boolean;
}
interface Pack {
  id: string;
  label: string;
  minutes: number;
  price_inr: number;
  per_credit_inr?: number;
  features?: PackFeatures;
}
interface Balance {
  balance_seconds: number | null;
  unlimited: boolean;
  configured: boolean;
  packs: Pack[];
  plan?: string;
  features?: PackFeatures;
}

// Razorpay Checkout is injected on demand (no SDK regen needed; calls go through
// the shared hey-api client). The credited amount is decided server-side from the
// stored transaction — the client only relays Razorpay's signed response to verify.
function loadRazorpay(): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof window !== "undefined" && (window as unknown as { Razorpay?: unknown }).Razorpay)
      return resolve(true);
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve(true);
    s.onerror = () => resolve(false);
    document.body.appendChild(s);
  });
}

export function CreditsSection() {
  const { user, loading: authLoading } = useAuth();
  const [data, setData] = useState<Balance | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    refresh();
  }, [authLoading, user]);

  async function refresh() {
    try {
      const res = await client.get({ url: "/api/v1/billing/balance" });
      setData(res.data as Balance);
    } catch {
      /* ignore */
    }
  }

  async function buy(pack: Pack) {
    setBusy(pack.id);
    try {
      if (!(await loadRazorpay())) {
        toast.error("Couldn't load the payment window");
        return;
      }
      const res = await client.post({
        url: "/api/v1/billing/order",
        body: { pack_id: pack.id },
      });
      if (res.error) throw new Error("order_failed");
      const order = res.data as {
        order_id: string;
        amount_paise: number;
        key_id: string;
      };
      const Razorpay = (window as unknown as { Razorpay: new (o: unknown) => { open: () => void } })
        .Razorpay;
      const rzp = new Razorpay({
        key: order.key_id,
        order_id: order.order_id,
        amount: order.amount_paise,
        currency: "INR",
        name: "auto4you",
        description: `${pack.minutes.toLocaleString()} call minutes`,
        handler: async (resp: {
          razorpay_order_id: string;
          razorpay_payment_id: string;
          razorpay_signature: string;
        }) => {
          const v = await client.post({ url: "/api/v1/billing/verify", body: resp });
          const vd = v.data as { ok: boolean; balance_seconds: number } | undefined;
          if (vd?.ok) {
            toast.success(`${pack.minutes.toLocaleString()} minutes added!`);
            await refresh();
          } else {
            toast.error("Payment verification failed — contact support");
          }
        },
        theme: { color: "#ff9a1f" },
      });
      rzp.open();
    } catch {
      toast.error("Couldn't start checkout");
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <p className="text-sm text-muted-foreground">Loading...</p>;

  const minutes =
    data.balance_seconds == null ? null : Math.floor(data.balance_seconds / 60);
  const planLabel =
    data.packs.find((p) => p.id === data.plan)?.label ??
    (data.plan && data.plan !== "trial" ? data.plan : "Trial");

  return (
    <div className="space-y-5">
      <div className="rounded-md border p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">Current balance</p>
          {!data.unlimited && (
            <span className="rounded-full border border-primary/30 bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              {planLabel} plan
            </span>
          )}
        </div>
        <p className="text-2xl font-bold tabular">
          {data.unlimited
            ? "Unlimited"
            : `${minutes?.toLocaleString()} credits`}
        </p>
        {!data.unlimited && (
          <p className="mt-1 text-xs text-muted-foreground">
            1 credit = 1 minute of calling.
          </p>
        )}
      </div>

      {data.unlimited ? (
        <p className="text-sm text-muted-foreground">
          Your account has unlimited calling — no top-up needed.
        </p>
      ) : !data.configured ? (
        <p className="text-sm text-muted-foreground">
          Top-ups aren&apos;t enabled yet. Once Razorpay is connected you&apos;ll be
          able to buy more minutes here.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          {data.packs.map((pack) => {
            const isCurrent = pack.id === data.plan;
            return (
              <div
                key={pack.id}
                className={`relative flex flex-col justify-between rounded-xl border p-4 transition-shadow ${
                  isCurrent
                    ? "border-primary/50 shadow-[var(--shadow-card)]"
                    : "hover:shadow-[var(--shadow-card)]"
                }`}
              >
                {isCurrent && (
                  <span className="absolute -top-2 right-3 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary-foreground">
                    Current
                  </span>
                )}
                <div>
                  <p className="font-semibold">{pack.label}</p>
                  <p className="text-sm text-muted-foreground">
                    {pack.minutes.toLocaleString()} credits
                  </p>
                  <p className="mt-2 text-lg font-bold tabular">
                    ₹{pack.price_inr.toLocaleString()}
                  </p>
                  {pack.per_credit_inr != null && (
                    <p className="text-xs text-muted-foreground">
                      ₹{pack.per_credit_inr}/credit
                    </p>
                  )}
                  {pack.features && (
                    <ul className="mt-3 space-y-1 text-xs">
                      <li className={pack.features.api ? "text-foreground" : "text-muted-foreground/50"}>
                        {pack.features.api ? "✓" : "✕"} API access
                      </li>
                      <li className={pack.features.mcp ? "text-foreground" : "text-muted-foreground/50"}>
                        {pack.features.mcp ? "✓" : "✕"} MCP server
                      </li>
                    </ul>
                  )}
                </div>
                <Button
                  className="mt-3"
                  variant={isCurrent ? "outline" : "brand"}
                  disabled={busy === pack.id}
                  onClick={() => buy(pack)}
                >
                  {busy === pack.id
                    ? "Opening..."
                    : isCurrent
                      ? "Add more"
                      : "Choose plan"}
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
