import { Database, FileText, SlidersHorizontal, UserPlus } from "lucide-react";

import { CrmSection } from "@/components/CrmSection";
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
    icon: UserPlus,
    title: "Upsert the contact",
    body: "Matches the lead by phone and keeps them current.",
  },
  {
    icon: FileText,
    title: "Log the full call",
    body: "Outcome, recording, transcript and sentiment as a note.",
  },
  {
    icon: SlidersHorizontal,
    title: "Sync what matters",
    body: "Filter by disposition, sentiment and call length.",
  },
];

export default function CrmIntegrationPage() {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-accent text-accent-foreground shadow-[var(--shadow-card)]">
            <Database className="h-6 w-6" />
          </div>
          <div>
            <p className="text-eyebrow text-primary">Integration</p>
            <h1 className="text-h1 mt-1">Connect your CRM</h1>
            <p className="text-body mt-2 text-muted-foreground">
              Push every call to your CRM — contact, outcome, recording,
              transcript and sentiment.
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
              <CardTitle className="text-h3">Connect your CRM</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                Bring your own account
              </Badge>
            </div>
            <CardDescription className="text-body">
              Automatically push every call to your CRM — upsert the contact and
              log the outcome, recording, transcript and sentiment. Connect your
              own CRM account and API token.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CrmSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
