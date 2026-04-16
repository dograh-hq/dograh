"use client";

import { Check, Copy } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

export function MCPSection() {
  const backendUrl =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    (typeof window !== "undefined" ? window.location.origin : "");
  const endpoint = `${backendUrl}/api/v1/mcp/`;

  const clientConfig = JSON.stringify(
    {
      mcpServers: {
        dograh: {
          url: endpoint,
          headers: { "X-API-Key": "YOUR_API_KEY" },
        },
      },
    },
    null,
    2,
  );

  const claudeCliCommand = `claude mcp add --transport http dograh ${endpoint} \\
  --header "X-API-Key: YOUR_API_KEY"`;

  const [endpointCopied, setEndpointCopied] = useState(false);
  const [configCopied, setConfigCopied] = useState(false);
  const [cliCopied, setCliCopied] = useState(false);

  const handleCopy = async (
    value: string,
    setter: (v: boolean) => void,
  ) => {
    await navigator.clipboard.writeText(value);
    setter(true);
    setTimeout(() => setter(false), 2000);
  };

  return (
    <div className="grid gap-6">
      <div className="grid gap-2">
        <Label>MCP Endpoint</Label>
        <p className="text-xs text-muted-foreground">
          Connect an AI agent (Claude Desktop, Cursor, etc.) to this URL over
          Streamable HTTP. Requires an API key in the X-API-Key header.{" "}
          <Link
            href="/api-keys"
            target="_blank"
            className="text-primary underline hover:no-underline"
          >
            Get your API key
          </Link>
        </p>
        <div className="flex items-center gap-2">
          <code className="text-xs break-all bg-muted px-2 py-1 rounded flex-1">
            {endpoint}
          </code>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0"
            onClick={() => handleCopy(endpoint, setEndpointCopied)}
          >
            {endpointCopied ? (
              <Check className="h-4 w-4" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      <div className="grid gap-2">
        <Label>Claude Code CLI</Label>
        <p className="text-xs text-muted-foreground">
          Run this in your terminal to register Dograh as an MCP server with
          Claude Code.
        </p>
        <div className="relative">
          <pre className="text-xs bg-muted px-3 py-2 pr-12 rounded overflow-x-auto whitespace-pre-wrap">
            {claudeCliCommand}
          </pre>
          <Button
            variant="outline"
            size="icon"
            className="absolute top-2 right-2"
            onClick={() => handleCopy(claudeCliCommand, setCliCopied)}
          >
            {cliCopied ? (
              <Check className="h-4 w-4" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      <div className="grid gap-2">
        <Label>Client Configuration</Label>
        <p className="text-xs text-muted-foreground">
          Paste this into your MCP client&apos;s config file (e.g. Claude
          Desktop&apos;s{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
            claude_desktop_config.json
          </code>
          ) and replace{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
            YOUR_API_KEY
          </code>
          .
        </p>
        <div className="relative">
          <pre className="text-xs bg-muted px-3 py-2 pr-12 rounded overflow-x-auto whitespace-pre-wrap">
            {clientConfig}
          </pre>
          <Button
            variant="outline"
            size="icon"
            className="absolute top-2 right-2"
            onClick={() => handleCopy(clientConfig, setConfigCopied)}
          >
            {configCopied ? (
              <Check className="h-4 w-4" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
