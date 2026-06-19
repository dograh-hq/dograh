"use client";

import { AlertTriangle, Menu, RefreshCw } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import posthog from "posthog-js";
import React, { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { SidebarInset, SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { PostHogEvent } from "@/constants/posthog-events";
import { useAppConfig } from "@/context/AppConfigContext";
import { LeadFormsProvider } from "@/context/LeadFormsContext";

import { AppSidebar } from "./AppSidebar";
import { GitHubStarBadge } from "./GitHubStarBadge";

function AppHeader() {
  const { toggleSidebar } = useSidebar();

  return (
    <header className="sticky top-0 z-50 flex items-center justify-between border-b border-border/60 bg-background/70 px-4 py-2 backdrop-blur-md supports-[backdrop-filter]:bg-background/55">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={toggleSidebar} aria-label="Open menu" className="md:hidden">
          <Menu className="h-5 w-5" />
        </Button>
        <Link href="/" className="text-lg font-bold md:hidden">Dograh</Link>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <a
            href="https://join.slack.com/t/dograh-community/shared_invite/zt-3zjb5vwvl-j7hRz3_F1SOn5cH~jm5f5g"
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => posthog.capture(PostHogEvent.SLACK_COMMUNITY_CLICKED, { source: "app_header" })}
            className="flex items-center gap-2"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
              <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zm10.122 2.521a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zm-1.268 0a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zm-2.523 10.122a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zm0-1.268a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" />
            </svg>
            <span className="hidden sm:inline">Join Slack</span>
          </a>
        </Button>
        <GitHubStarBadge source="app_header" />
      </div>
    </header>
  );
}

function BackendStatusBanner() {
  const { config, loading, refresh } = useAppConfig();

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
            <p className="text-sm font-semibold">Backend connection failed</p>
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
          Retry
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
