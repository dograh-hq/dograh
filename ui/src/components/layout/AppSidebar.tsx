"use client";

import type { Team } from "@stackframe/stack";
import {
  AudioLines,
  Brain,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Database,
  FileText,
  Home,
  Key,
  LogOut,
  Megaphone,
  Phone,
  Settings,
  TrendingUp,
  Workflow,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React, { useRef } from "react";

import ThemeToggle from "@/components/ThemeSwitcher";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAppConfig } from "@/context/AppConfigContext";
import type { LocalUser } from "@/lib/auth";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// Lazy load SelectedTeamSwitcher - we'll pass selectedTeam from our context
const StackTeamSwitcher = React.lazy(() =>
  import("@stackframe/stack").then((mod) => ({
    default: mod.SelectedTeamSwitcher,
  }))
);

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, isMobile, setOpenMobile } = useSidebar();
  const { provider, getSelectedTeam, logout, user } = useAuth();
  const { config } = useAppConfig();

  // On mobile the sidebar renders as a full-width sheet overlay, so treat it
  // as always "expanded" regardless of the desktop collapsed/expanded state.
  const effectiveState = isMobile ? "expanded" : state;

  // Get selected team for Stack auth (cast to Team type from Stack)
  // Stabilize the reference so SelectedTeamSwitcher only sees a change when the team ID changes,
  // preventing unnecessary PATCH calls to Stack Auth on every route navigation.
  const selectedTeamRef = useRef<Team | null>(null);
  const rawSelectedTeam = provider === "stack" && getSelectedTeam ? getSelectedTeam() as Team | null : null;
  if (rawSelectedTeam?.id !== selectedTeamRef.current?.id) {
    selectedTeamRef.current = rawSelectedTeam;
  }
  const selectedTeam = selectedTeamRef.current;

  // Version info from app config context
  const versionInfo = config ? { ui: config.uiVersion, api: config.apiVersion } : null;

  const isActive = (path: string) => {
    return pathname.startsWith(path);
  };


  // Organize navigation into sections
  const overviewSection = [
    {
      title: "Overview",
      url: "/overview",
      icon: Home,
    },
  ];

  const buildSection = [
        {
          title: "Voice Agents",
          url: "/workflow",
          icon: Workflow,
        },
        {
          title: "Campaigns",
          url: "/campaigns",
          icon: Megaphone,
        },
        // {
        //   title: "Automation",
        //   url: "/automation",
        //   icon: Zap,
        // },
        {
          title: "Models",
          url: "/model-configurations",
          icon: Brain,
        },
        {
          title: "Telephony",
          url: "/telephony-configurations",
          icon: Phone,
        },
        {
          title: "Tools",
          url: "/tools",
          icon: Wrench,
        },
        {
          title: "Files",
          url: "/files",
          icon: Database,
        },
        {
          title: "Recordings",
          url: "/recordings",
          icon: AudioLines,
        },
        // {
        //   title: "Integrations",
        //   url: "/integrations",
        //   icon: Plug,
        // },
        {
          title: "Developers",
          url: "/api-keys",
          icon: Key,
        },
      ];

  const observeSection = [
    {
      title: "Usage",
      url: "/usage",
      icon: TrendingUp,
    },
    {
      title: "Reports",
      url: "/reports",
      icon: FileText,
    },
    // {
    //   title: "LoopTalk",
    //   url: "/looptalk",
    //   icon: MessageSquare,
    // },
  ];

  const handleMobileNavClick = () => {
    if (isMobile) {
      setOpenMobile(false);
    }
  };

  const SidebarLink = ({ item }: { item: typeof overviewSection[0] }) => {
    const isItemActive = isActive(item.url);
    const Icon = item.icon;

    if (effectiveState === "collapsed") {
      return (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <SidebarMenuButton
                asChild
                className={cn(
                  "hover:bg-accent hover:text-accent-foreground",
                  isItemActive && "bg-accent text-accent-foreground"
                )}
              >
                <Link href={item.url} onClick={handleMobileNavClick}>
                  <Icon className="h-4 w-4" />
                  <span className="sr-only">{item.title}</span>
                </Link>
              </SidebarMenuButton>
            </TooltipTrigger>
            <TooltipContent side="right">
              <p>{item.title}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    }

    return (
      <SidebarMenuButton
        asChild
        className={cn(
          "hover:bg-accent hover:text-accent-foreground",
          isItemActive && "bg-accent text-accent-foreground"
        )}
      >
        <Link href={item.url} onClick={handleMobileNavClick}>
          <Icon className="h-4 w-4" />
          <span>{item.title}</span>
        </Link>
      </SidebarMenuButton>
    );
  };

  return (
    <Sidebar collapsible="icon" className="border-r">
      <SidebarHeader className="border-b px-2 py-3">
        <div className="flex items-center justify-between">
          {/* Logo - only show when expanded */}
          {effectiveState === "expanded" && (
            <Link
              href="/"
              className="flex items-center gap-2 px-2 text-xl font-bold"
            >
              Dograh
              {versionInfo && (
                <span className="text-xs font-normal text-muted-foreground">
                  v{versionInfo.ui}
                </span>
              )}
            </Link>
          )}
          {/* Toggle button - center it when collapsed */}
          <SidebarTrigger className={cn(
            "hover:bg-accent",
            effectiveState === "collapsed" && "mx-auto"
          )}>
            {effectiveState === "expanded" ? (
              <ChevronLeft className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </SidebarTrigger>
        </div>

        {/* Team Switcher for Stack Auth - at the top */}
        {provider === "stack" && effectiveState === "expanded" && (
          <div className="mt-3">
            <React.Suspense
              fallback={
                <div className="h-9 w-full animate-pulse bg-muted rounded" />
              }
            >
              <StackTeamSwitcher
                selectedTeam={selectedTeam || undefined}
                onChange={() => {
                  router.refresh();
                }}
              />
            </React.Suspense>
          </div>
        )}

      </SidebarHeader>

      <SidebarContent className={cn(
        effectiveState === "collapsed" && "px-0"
      )}>
        {/* Overview Section */}
        <SidebarGroup className="mt-2">
          <SidebarMenu>
            {overviewSection.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarLink item={item} />
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>

        {/* BUILD Section */}
        {buildSection.length > 0 && (
          <SidebarGroup className="mt-6">
            {effectiveState === "expanded" && (
              <SidebarGroupLabel className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                BUILD
              </SidebarGroupLabel>
            )}
            <SidebarMenu>
              {buildSection.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarLink item={item} />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        )}

        {/* OBSERVE Section */}
        <SidebarGroup className="mt-6">
          {effectiveState === "expanded" && (
            <SidebarGroupLabel className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              OBSERVE
            </SidebarGroupLabel>
          )}
          <SidebarMenu>
            {observeSection.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarLink item={item} />
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className={cn(
        "border-t p-4",
        effectiveState === "collapsed" && "p-2"
      )}>
        {/* Bottom Actions */}
        <div className="space-y-2">
          {/* User Button - for local/OSS mode */}
          {provider !== "stack" && (
            <div className={cn(
              "flex",
              effectiveState === "collapsed" ? "justify-center" : "justify-start"
            )}>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="rounded-full h-8 w-8 cursor-pointer">
                    <span className="text-xs font-medium">
                      {(user?.displayName || (user as LocalUser | undefined)?.email || "")
                        .split(/[\s@]/)
                        .filter(Boolean)
                        .slice(0, 2)
                        .map((s: string) => s[0]?.toUpperCase())
                        .join("")
                        || "U"}
                    </span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="top" align="start" className="w-56">
                  <DropdownMenuLabel className="font-normal">
                    <div className="flex flex-col space-y-1">
                      {(user as LocalUser | undefined)?.email && (
                        <p className="text-xs text-muted-foreground">{(user as LocalUser).email}</p>
                      )}
                    </div>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
                    <Settings className="mr-2 h-4 w-4" />
                    Platform Settings
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => logout()} className="cursor-pointer">
                    <LogOut className="mr-2 h-4 w-4" />
                    Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}

          {/* User Button - for Stack auth */}
          {provider === "stack" && (
            <div className={cn(
              "flex",
              effectiveState === "collapsed" ? "justify-center" : "justify-start"
            )}>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="rounded-full h-8 w-8 cursor-pointer">
                    <span className="text-xs font-medium">
                      {(user?.displayName || (user as { primaryEmail?: string })?.primaryEmail || "")
                        .split(/[\s@]/)
                        .filter(Boolean)
                        .slice(0, 2)
                        .map((s: string) => s[0]?.toUpperCase())
                        .join("")
                        || "U"}
                    </span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="top" align="start" className="w-56">
                  <DropdownMenuLabel className="font-normal">
                    <div className="flex flex-col space-y-1">
                      {user?.displayName && (
                        <p className="text-sm font-medium">{user.displayName}</p>
                      )}
                      {(user as { primaryEmail?: string })?.primaryEmail && (
                        <p className="text-xs text-muted-foreground">{(user as { primaryEmail?: string }).primaryEmail}</p>
                      )}
                    </div>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => router.push("/handler/account-settings")} className="cursor-pointer">
                    <Settings className="mr-2 h-4 w-4" />
                    Account settings
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
                    <Settings className="mr-2 h-4 w-4" />
                    Platform Settings
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => router.push("/usage")} className="cursor-pointer">
                    <CircleDollarSign className="mr-2 h-4 w-4" />
                    Usage
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => logout()} className="cursor-pointer">
                    <LogOut className="mr-2 h-4 w-4" />
                    Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )}

          {/* Theme Toggle - at the very bottom */}
          <div className={cn(
            "mt-2 pt-2 border-t",
            effectiveState === "collapsed" ? "flex justify-center" : ""
          )}>
            {effectiveState === "collapsed" ? (
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div>
                      <ThemeToggle
                        showLabel={false}
                        className="hover:bg-accent hover:text-accent-foreground"
                      />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    <p>Toggle theme</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : (
              <ThemeToggle
                showLabel={true}
                className="hover:bg-accent hover:text-accent-foreground"
              />
            )}
          </div>

        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
