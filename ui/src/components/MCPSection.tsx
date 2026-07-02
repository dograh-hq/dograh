"use client";

import { Check, Copy } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useAppConfig } from "@/context/AppConfigContext";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";

const MCP_PATH = "/api/v1/mcp/";

export function MCPSection() {
  const t = useTranslations("mcpSection");
  const { config } = useAppConfig();
  const backendUrl = resolveBrowserBackendUrl(config?.backendApiEndpoint);
  const tunnelUrl = config?.tunnelUrl ?? null;

  const endpoints = [
    ...(tunnelUrl
      ? [
          {
            key: "tunnel",
            label: t("publicUrl"),
            url: `${tunnelUrl}${MCP_PATH}`,
          },
        ]
      : []),
    { key: "backend", label: t("backendUrl"), url: `${backendUrl}${MCP_PATH}` },
  ];

  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const handleCopy = async (value: string, key: string) => {
    await navigator.clipboard.writeText(value);
    setCopiedKey(key);
    setTimeout(
      () => setCopiedKey((current) => (current === key ? null : current)),
      2000,
    );
  };

  return (
    <div className="grid gap-6">
      <div className="grid gap-2">
        <Label>{t("endpoint")}</Label>
        <p className="text-xs text-muted-foreground">
          {t("description")}{" "}
          <Link
            href="/api-keys"
            target="_blank"
            className="text-primary underline hover:no-underline"
          >
            {t("getApiKey")}
          </Link>
        </p>
        <div className="grid gap-3">
          {endpoints.map(({ key, label, url }) => (
            <div key={key} className="grid gap-1">
              {endpoints.length > 1 && (
                <span className="text-xs font-medium text-muted-foreground">
                  {label}
                </span>
              )}
              <div className="flex items-center gap-2">
                <code className="text-xs break-all bg-muted px-2 py-1 rounded flex-1">
                  {url}
                </code>
                <Button
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  onClick={() => handleCopy(url, key)}
                >
                  {copiedKey === key ? (
                    <Check className="h-4 w-4" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </div>
          ))}
        </div>
        {tunnelUrl && (
          <p className="text-xs text-muted-foreground">
            {t("tunnelHint")}
          </p>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        {t("guideText")}{" "}
        <Link
          href="https://docs.dograh.com/integrations/mcp"
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline hover:no-underline"
        >
          {t("guideLink")}
        </Link>
        .
      </p>
    </div>
  );
}
