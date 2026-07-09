"use client";

import type { CurrentUser, Team } from "@stackframe/stack";
import React, { useState } from "react";
import { toast } from "sonner";

import SpinLoader from "@/components/SpinLoader";
import { useAuth } from "@/lib/auth";
import { reloadApp } from "@/lib/browserReload";
import logger from "@/lib/logger";

// Lazy load Stack's SelectedTeamSwitcher, but own the selected-team write here.
// The stock component re-applies the selectedTeam prop asynchronously, which
// can pin the sidebar to the previous team while the user switch is in flight.
const StackTeamSwitcher = React.lazy(() =>
  import("@stackframe/stack").then((mod) => ({
    default: mod.SelectedTeamSwitcher,
  }))
);

export function SidebarTeamSwitcher() {
  const { provider, user } = useAuth();

  // The !user guard is load-bearing (Sentry JAVASCRIPT-NEXTJS-2Z): Stack's
  // TeamSwitcher calls user?.useTeams() — a hook behind optional chaining — so
  // if useUser() flips to null mid-session (token expiry, sign-out from
  // another tab) its re-render throws React #300 "Rendered fewer hooks than
  // expected". Unmounting it here re-renders the ancestor first, removing the
  // switcher before it can re-render with a null user.
  if (provider !== "stack" || !user) {
    return null;
  }

  return <SidebarTeamSwitcherContent user={user as CurrentUser} />;
}

function SidebarTeamSwitcherContent({ user }: { user: CurrentUser }) {
  const [isSwitching, setIsSwitching] = useState(false);

  const handleChange = async (team: Team | null) => {
    setIsSwitching(true);

    try {
      await user.setSelectedTeam(team);
      reloadApp();
    } catch (error) {
      logger.error("Failed to switch Stack team", error);
      toast.error("Could not switch teams. Please try again.");
      setIsSwitching(false);
    }
  };

  return (
    <div className="relative">
      <React.Suspense
        fallback={<div className="h-9 w-full animate-pulse rounded bg-muted" />}
      >
        <StackTeamSwitcher
          selectedTeam={user.selectedTeam || undefined}
          noUpdateSelectedTeam
          onChange={(team) => {
            void handleChange(team);
          }}
          triggerClassName="w-full"
        />
      </React.Suspense>
      {isSwitching && (
        <div
          className="fixed inset-0 z-[100] flex min-h-screen items-center justify-center bg-background/90 backdrop-blur-sm"
          role="status"
          aria-live="polite"
        >
          <SpinLoader label="Switching teams..." />
        </div>
      )}
    </div>
  );
}
