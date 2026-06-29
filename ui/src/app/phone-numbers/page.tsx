import { PhoneCall, ShieldCheck, Wallet } from "lucide-react";

import { PhoneNumbersSection } from "@/components/PhoneNumbersSection";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const highlights = [
  {
    icon: PhoneCall,
    title: "Outbound-ready",
    body: "Dedicated numbers for your calling campaigns.",
  },
  {
    icon: ShieldCheck,
    title: "KYC-verified",
    body: "Purchases unlock once your KYC is complete.",
  },
  {
    icon: Wallet,
    title: "Pay from credits",
    body: "Charged straight to your call-credit balance.",
  },
];

export default function PhoneNumbersPage() {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-accent text-accent-foreground shadow-[var(--shadow-card)]">
            <PhoneCall className="h-6 w-6" />
          </div>
          <div>
            <p className="text-eyebrow text-primary">Telephony</p>
            <h1 className="text-h1 mt-1">Phone Numbers</h1>
            <p className="text-body mt-2 text-muted-foreground">
              Buy and manage outbound numbers for your campaigns.
            </p>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          {highlights.map(({ icon: Icon, title, body }) => (
            <div
              key={title}
              className="rounded-2xl border border-border/60 bg-card p-4 shadow-[var(--shadow-card)] transition-all duration-200"
            >
              <Icon className="h-5 w-5 text-primary" />
              <p className="text-label mt-3">{title}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {body}
              </p>
            </div>
          ))}
        </div>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-h3">Phone Numbers</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                KYC required
              </Badge>
            </div>
            <CardDescription className="text-body">
              Buy a phone number for outbound calls. Requires completed KYC;
              charged to your call-credit balance.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PhoneNumbersSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
