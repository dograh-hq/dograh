"use client";

import { usePathname } from "next/navigation";
import React, { ReactNode } from "react";

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";

import { AppSidebar } from "./AppSidebar";

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
  // Hide sidebar for root (/) and /handler routes (Stack Auth routes)
  const shouldShowSidebar = pathname !== "/" && !pathname.startsWith("/handler");

  // Check if we're in workflow editor mode - collapse sidebar by default
  const isWorkflowEditor = /^\/workflow\/\d+/.test(pathname);

  // If no sidebar needed, just return children
  if (!shouldShowSidebar) {
    return <>{children}</>;
  }

  return (
    <SidebarProvider defaultOpen={!isWorkflowEditor}>
      <div className="flex h-screen w-full">
        <AppSidebar />
        <SidebarInset className="flex-1">
          {/* Optional header area for specific pages */}
          {headerActions && (
            <header className="sticky top-0 z-50 w-full border-b bg-background">
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
          <main className="flex-1 overflow-auto">
            {children}
          </main>
        </SidebarInset>
      </div>
    </SidebarProvider>
  );
};

export default AppLayout;
