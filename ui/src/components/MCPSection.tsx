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

  const [endpointCopied, setEndpointCopied] = useState(false);

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
          Connect an MCP-compatible AI assistant to this URL over Streamable
          HTTP. Requires an API key in the X-API-Key header.{" "}
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

      <p className="text-xs text-muted-foreground">
        For step-by-step setup with Claude Code, Claude Desktop, Cursor, and
        other clients, see the{" "}
        <Link
          href="https://docs.dograh.com/integrations/mcp"
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline hover:no-underline"
        >
          MCP integration guide
        </Link>
        .
      </p>
    </div>
  );
}
