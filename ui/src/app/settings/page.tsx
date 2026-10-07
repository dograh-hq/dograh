"use client";

import { ExternalLink } from "lucide-react";

import { CallEventsSection } from "@/components/CallEventsSection";
import { PageLayout } from "@/components/layout/PageLayout";
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

export default function SettingsPage() {
  return (
    <PageLayout title="Platform Settings" description="Manage your platform configuration and integrations.">

      <Card>
        <CardHeader>
          <CardTitle>Preferences</CardTitle>
          <CardDescription>
            Set organization-wide defaults such as the test phone number and
            timezone.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <OrganizationPreferencesSection />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>MCP Server</CardTitle>
          <CardDescription>
            Let AI agents access your Dograh workspace and documentation via
            the Model Context Protocol.{" "}
            <a
              href="https://docs.dograh.com/integrations/mcp"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 underline"
            >
              Learn more <ExternalLink className="h-3 w-3" />
            </a>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MCPSection />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Telemetry</CardTitle>
          <CardDescription>
            Configure Langfuse tracing for your voice agent calls.{" "}
            <a
              href="https://docs.dograh.com/configurations/tracing"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-0.5 underline"
            >
              Learn more <ExternalLink className="h-3 w-3" />
            </a>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TelemetrySection />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Call events</CardTitle>
          <CardDescription>Configure where your organization sends call diagnostics.</CardDescription>
        </CardHeader>
        <CardContent>
          <CallEventsSection />
        </CardContent>
      </Card>
    </PageLayout>
  );
}
