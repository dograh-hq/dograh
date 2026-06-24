"use client";

import { ExternalLink } from "lucide-react";

import { CreditsSection } from "@/components/CreditsSection";
import { CrmSection } from "@/components/CrmSection";
import { MCPSection } from "@/components/MCPSection";
import { TelemetrySection } from "@/components/TelemetrySection";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { WhatsAppSection } from "@/components/WhatsAppSection";
import { INTEGRATION_DOCUMENTATION_URLS } from "@/constants/documentation";
import { BRAND } from "@/lib/brand";

export default function SettingsPage() {
  return (
    <div className="flex justify-center py-12 px-4">
      <div className="w-full max-w-2xl space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Platform Settings</h1>
          <p className="text-muted-foreground">
            Manage your platform configuration and integrations.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Credits &amp; Billing</CardTitle>
            <CardDescription>
              Your remaining call minutes. Top up anytime with Razorpay.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CreditsSection />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>MCP Server</CardTitle>
            <CardDescription>
              Let AI agents access your {BRAND.name} workspace and documentation via
              the Model Context Protocol.{" "}
              <a
                href={INTEGRATION_DOCUMENTATION_URLS.mcp}
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
                href={INTEGRATION_DOCUMENTATION_URLS.tracing}
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
            <CardTitle>WhatsApp Follow-up</CardTitle>
            <CardDescription>
              Automatically send an approved WhatsApp template (with an optional
              document) to the lead after each call. Connect your own provider
              account and API key.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WhatsAppSection />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Connect your CRM</CardTitle>
            <CardDescription>
              Automatically push every call to your CRM — upsert the contact and log
              the outcome, recording, transcript and sentiment. Connect your own CRM
              account and API token.
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
