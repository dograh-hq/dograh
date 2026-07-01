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

// PayU Hosted Checkout is a redirect flow: the backend returns the PayU payment
// URL + a server-signed set of form fields, we auto-POST them, and PayU redirects
// back to /credits?payment=success|failed. The credited amount is decided
// server-side from the stored transaction; the SALT never reaches the browser.
function submitToPayU(paymentUrl: string, params: Record<string, string>) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = paymentUrl;
  for (const [name, value] of Object.entries(params)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value ?? "";
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
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

  // Handle the PayU return: /credits?payment=success|failed → toast, refresh
  // the balance, and strip the query param.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const outcome = new URLSearchParams(window.location.search).get("payment");
    if (!outcome) return;
    if (outcome === "success") toast.success("Payment successful — credits added!");
    else toast.error("Payment failed or was cancelled.");
    window.history.replaceState({}, "", "/credits");
    refresh();
  }, []);

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
      const res = await client.post({
        url: "/api/v1/billing/payu/initiate",
        body: { pack_id: pack.id },
      });
      if (res.error) throw new Error("initiate_failed");
      const { payment_url, params } = res.data as {
        payment_url: string;
        params: Record<string, string>;
      };
      // Redirects the browser to PayU; on success it returns to
      // /credits?payment=success and the effect below toasts + refreshes.
      submitToPayU(payment_url, params);
    } catch {
      toast.error("Couldn't start checkout");
      setBusy(null); // on redirect success the page navigates away
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
          Top-ups aren&apos;t enabled yet. Once the payment gateway is connected
          you&apos;ll be able to buy more minutes here.
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
