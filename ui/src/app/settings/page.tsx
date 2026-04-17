"use client";

import { ExternalLink } from "lucide-react";

import { MCPSection } from "@/components/MCPSection";
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
      </div>
    </div>
  );
}
