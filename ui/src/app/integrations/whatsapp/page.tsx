import { BadgeCheck, MessageCircle, SlidersHorizontal, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { WhatsAppSection } from "@/components/WhatsAppSection";

const highlights = [
  {
    icon: Zap,
    title: "Auto-send after calls",
    body: "Fires the moment a qualifying call completes.",
  },
  {
    icon: BadgeCheck,
    title: "Approved templates",
    body: "Uses your verified Meta template and optional document.",
  },
  {
    icon: SlidersHorizontal,
    title: "Precise targeting",
    body: "Filter by disposition, sentiment and call length.",
  },
];

export default function WhatsAppIntegrationPage() {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-accent text-accent-foreground shadow-[var(--shadow-card)]">
            <MessageCircle className="h-6 w-6" />
          </div>
          <div>
            <p className="text-eyebrow text-primary">Integration</p>
            <h1 className="text-h1 mt-1">WhatsApp Follow-up</h1>
            <p className="text-body mt-2 text-muted-foreground">
              Send an approved WhatsApp template to the lead automatically after
              each call.
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
              <CardTitle className="text-h3">WhatsApp Follow-up</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                Bring your own provider
              </Badge>
            </div>
            <CardDescription className="text-body">
              Automatically send an approved WhatsApp template (with an optional
              document) to the lead after each call. Connect your own provider
              account and API key.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WhatsAppSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
