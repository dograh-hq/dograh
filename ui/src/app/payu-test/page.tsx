"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { IntegrationPage } from "@/components/IntegrationPage";
import { Button } from "@/components/ui/button";

// Auto-POST a form to PayU's hosted checkout (the server returns the signed
// fields; the SALT never reaches the browser).
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

export default function PayuTestPage() {
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const outcome = new URLSearchParams(window.location.search).get("payment");
    if (!outcome) return;
    if (outcome === "success") toast.success("PayU test payment succeeded ✅");
    else toast.error("PayU test payment failed or was cancelled.");
    window.history.replaceState({}, "", "/payu-test");
  }, []);

  async function payTest() {
    setBusy(true);
    try {
      const res = await client.post({ url: "/api/v1/billing/payu/test-initiate" });
      if (res.error) throw new Error("initiate_failed");
      const { payment_url, params } = res.data as {
        payment_url: string;
        params: Record<string, string>;
      };
      submitToPayU(payment_url, params);
    } catch {
      toast.error("Couldn't start the test payment — sign in as the owner and retry.");
      setBusy(false);
    }
  }

  return (
    <IntegrationPage
      eyebrow="Billing"
      title="PayU Test Payment"
      subtitle="Owner-only ₹30 live test to verify the PayU gateway end-to-end."
      cardTitle="PayU ₹30 test"
      cardDescription="Runs a real ₹30 PayU Hosted Checkout. On success you return here; a nominal 1 minute is credited (skipped for unlimited orgs)."
    >
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Clicking below redirects you to PayU&apos;s live checkout for <b>₹30</b>.
          If the request hash were wrong, PayU shows an error here — <b>before</b> any
          payment — so you can confirm the page loads with zero money at risk.
        </p>
        <Button onClick={payTest} disabled={busy} variant="brand">
          {busy ? "Starting…" : "Pay ₹30 (test)"}
        </Button>
      </div>
    </IntegrationPage>
  );
}
