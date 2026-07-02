"use client";

import { AlertTriangle, Menu, RefreshCw } from "lucide-react";
import { Link, usePathname } from "@/i18n/routing";
import { useTranslations } from "next-intl";
import posthog from "posthog-js";
import React, { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { SidebarInset, SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { PostHogEvent } from "@/constants/posthog-events";
import { useAppConfig } from "@/context/AppConfigContext";
import { LeadFormsProvider } from "@/context/LeadFormsContext";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { AppSidebar } from "./AppSidebar";
import { GitHubStarBadge } from "./GitHubStarBadge";

function AppHeader() {
  const { toggleSidebar } = useSidebar();
  const t = useTranslations("appLayout");

  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-border/60 bg-background/70 px-4 py-2 backdrop-blur-md supports-[backdrop-filter]:bg-background/55">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label={t("openMenu")} className="md:hidden">
          <Menu className="h-5 w-5" />
        </Button>
        <Link href="/" className="text-lg font-bold md:hidden">{t("mobileBrand")}</Link>
      </div>
      <div className="flex items-center gap-3">
        <LanguageSwitcher />
        <GitHubStarBadge source="app_header" />
      </div>
    </header>
  );
}

function BackendStatusBanner() {
  const { config, loading, refresh } = useAppConfig();
  const t = useTranslations("appLayout");

  if (!config || config.backendStatus === "reachable") {
    return null;
  }

  const backendUrl = config.backendUrl && config.backendUrl !== "unknown"
    ? config.backendUrl
    : "the configured backend";
  const message = config.backendMessage || `Backend is not reachable at ${backendUrl}.`;

  return (
    <div
      role="alert"
      className="border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-950 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">{t("backendFailed")}</p>
            <p className="break-words text-sm">{message}</p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refresh()}
          disabled={loading}
          className="h-8 shrink-0 border-amber-400 bg-transparent text-amber-950 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-100 dark:hover:bg-amber-900/40"
        >
          <RefreshCw className="h-4 w-4" />
          {t("retry")}
        </Button>
      </div>
    </div>
  );
}

interface AppLayoutProps {
  children: ReactNode;
  headerActions?: ReactNode;
  stickyTabs?: ReactNode;
}

const AppLayout: React.FC<AppLayoutProps> = ({
  children,
  headerActions,
  stickyTabs,
}) => {
  const pathname = usePathname();

  // Check if current route should have sidebar
  // Hide sidebar for root (/), /handler routes (Stack Auth routes), and /auth routes
  const shouldShowSidebar = pathname !== "/" && !pathname.startsWith("/handler") && !pathname.startsWith("/auth");

  // Only match the exact editor page /workflow/<id>, not sub-routes like /workflow/<id>/runs
  const isWorkflowEditor = /^\/workflow\/\d+$/.test(pathname);

  // Always render SidebarProvider to keep the component tree shape consistent
  // across route changes (avoids React hooks ordering violations during navigation).
  return (
    <SidebarProvider defaultOpen>
      {shouldShowSidebar ? (
        <LeadFormsProvider>
          <div className="flex min-h-screen w-full">
            <AppSidebar />
            <SidebarInset className="flex-1">
              <BackendStatusBanner />
              {!isWorkflowEditor && <AppHeader />}
              {/* Optional header area for specific pages */}
              {headerActions && (
                <header className="sticky top-0 z-50 w-full border-b border-border/60 bg-background/70 backdrop-blur-md supports-[backdrop-filter]:bg-background/55">
                  <div className="container mx-auto px-4 py-4">
                    <div className="flex items-center justify-center">
                      {headerActions}
                    </div>
                  </div>
                </header>
              )}

              {/* Optional sticky tabs */}
              {stickyTabs && (
                <div className="sticky top-0 z-40 bg-[#2a2e39] border-b border-gray-700">
                  <div className="container mx-auto px-4">
                    <div className="flex items-center justify-center py-2">
                      {stickyTabs}
                    </div>
                  </div>
                </div>
              )}

              {/* Main content area */}
              <main className="app-surface flex-1">
                {children}
              </main>
            </SidebarInset>
          </div>
        </LeadFormsProvider>
      ) : (
        <div className="app-surface w-full flex-1">
          <BackendStatusBanner />
          {children}
        </div>
      )}
    </SidebarProvider>
  );
};

export default AppLayout;
