import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Shared shell for a single self-serve integration/account page (Credits,
 * Phone Numbers, WhatsApp, CRM). Renders the page header + a titled card so
 * the existing Section components drop straight in.
 */
export function IntegrationPage({
  eyebrow,
  title,
  subtitle,
  cardTitle,
  cardDescription,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  cardTitle: string;
  cardDescription: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div>
          <p className="text-eyebrow text-primary">{eyebrow}</p>
          <h1 className="text-h1 mt-1">{title}</h1>
          {subtitle && (
            <p className="text-body mt-2 text-muted-foreground">{subtitle}</p>
          )}
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{cardTitle}</CardTitle>
            <CardDescription>{cardDescription}</CardDescription>
          </CardHeader>
          <CardContent>{children}</CardContent>
        </Card>
      </div>
    </div>
  );
}
