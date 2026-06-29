"use client";

import { ExternalLink } from "lucide-react";

import { MCPSection } from "@/components/MCPSection";
import { OrganizationPreferencesSection } from "@/components/OrganizationPreferencesSection";
import { TelemetrySection } from "@/components/TelemetrySection";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { INTEGRATION_DOCUMENTATION_URLS } from "@/constants/documentation";
import { useFeature } from "@/hooks/useFeature";
import { BRAND } from "@/lib/brand";

export default function SettingsPage() {
  // MCP is a Scale-plan feature (superuser always). Billing, Phone Numbers,
  // WhatsApp and CRM now live on their own pages in the sidebar.
  const mcp = useFeature("mcp");

  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div>
          <p className="text-eyebrow text-primary">Configuration</p>
          <h1 className="text-h1 mt-1">Settings</h1>
          <p className="text-body mt-2 text-muted-foreground">
            Platform configuration. Manage Billing, Phone Numbers, WhatsApp and
            CRM from the Integrations section in the sidebar.
          </p>
        </div>

        <Card className="rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
          <CardHeader className="gap-1.5">
            <CardTitle className="text-h3">Preferences</CardTitle>
            <CardDescription className="text-body">
              Set organization-wide defaults such as the test phone number and
              timezone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <OrganizationPreferencesSection />
          </CardContent>
        </Card>

        {mcp.enabled && (
          <Card className="rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
            <CardHeader className="gap-1.5">
              <CardTitle className="text-h3">MCP Server</CardTitle>
              <CardDescription className="text-body">
                Let AI agents access your {BRAND.name} workspace and documentation
                via the Model Context Protocol.{" "}
                <a
                  href={INTEGRATION_DOCUMENTATION_URLS.mcp}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 font-medium text-foreground underline underline-offset-2 transition-colors hover:text-primary"
                >
                  Learn more <ExternalLink className="h-3 w-3" />
                </a>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <MCPSection />
            </CardContent>
          </Card>
        )}

        <Card className="rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
          <CardHeader className="gap-1.5">
            <CardTitle className="text-h3">Telemetry</CardTitle>
            <CardDescription className="text-body">
              Configure Langfuse tracing for your voice agent calls.{" "}
              <a
                href={INTEGRATION_DOCUMENTATION_URLS.tracing}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-foreground underline underline-offset-2 transition-colors hover:text-primary"
              >
                Learn more <ExternalLink className="h-3 w-3" />
              </a>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TelemetrySection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
