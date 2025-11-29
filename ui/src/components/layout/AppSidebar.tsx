"use client";

import type { Team } from "@stackframe/stack";
import {
  Brain,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  FileText,
  HelpCircle,
  Home,
  Key,
  Megaphone,
  MessageSquare,
  Phone,
  Star,
  TrendingUp,
  Workflow,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React from "react";

import ThemeToggle from "@/components/ThemeSwitcher";
import { Button } from "@/components/ui/button";
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
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

// Conditionally load Stack components only when using Stack auth
const StackUserButton = React.lazy(() =>
  import("@stackframe/stack").then((mod) => ({ default: mod.UserButton }))
);

// Lazy load SelectedTeamSwitcher - we'll pass selectedTeam from our context
const StackTeamSwitcher = React.lazy(() =>
  import("@stackframe/stack").then((mod) => ({
    default: mod.SelectedTeamSwitcher,
  }))
);

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state } = useSidebar();
  const { provider, getSelectedTeam } = useAuth();

  // Get selected team for Stack auth (cast to Team type from Stack)
  const selectedTeam = provider === "stack" && getSelectedTeam ? getSelectedTeam() as Team | null : null;

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
        {
          title: "Automation",
          url: "/automation",
          icon: Zap,
        },
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
    {
      title: "LoopTalk",
      url: "/looptalk",
      icon: MessageSquare,
    },
  ];

  const SidebarLink = ({ item }: { item: typeof overviewSection[0] }) => {
    const isItemActive = isActive(item.url);
    const Icon = item.icon;

    if (state === "collapsed") {
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
                <Link href={item.url}>
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
        <Link href={item.url}>
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
          {state === "expanded" && (
            <Link
              href="/"
              className="flex items-center gap-2 px-2 text-xl font-bold"
            >
              Dograh
            </Link>
          )}
          {/* Toggle button - center it when collapsed */}
          <SidebarTrigger className={cn(
            "hover:bg-accent",
            state === "collapsed" && "mx-auto"
          )}>
            {state === "expanded" ? (
              <ChevronLeft className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </SidebarTrigger>
        </div>

        {/* Team Switcher for Stack Auth - at the top */}
        {provider === "stack" && state === "expanded" && (
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

        {/* Star us on GitHub for OSS mode - at the top */}
        {provider !== "stack" && (
          <div className="mt-3 px-2">
            {state === "collapsed" ? (
              <TooltipProvider delayDuration={0}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="w-full hover:bg-accent hover:text-accent-foreground"
                      asChild
                    >
                      <a
                        href="https://github.com/dograh-hq/dograh"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
                        <span className="sr-only">Star us on GitHub</span>
                      </a>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    <p>Star us on GitHub</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : (
              <Button
                variant="ghost"
                className="w-full justify-start hover:bg-accent hover:text-accent-foreground"
                asChild
              >
                <a
                  href="https://github.com/dograh-hq/dograh"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
                  <span className="ml-2">Star us on GitHub</span>
                </a>
              </Button>
            )}
          </div>
        )}
      </SidebarHeader>

      <SidebarContent className={cn(
        state === "collapsed" && "px-0"
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
            {state === "expanded" && (
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
          {state === "expanded" && (
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
        state === "collapsed" && "p-2"
      )}>
        {/* Bottom Actions */}
        <div className="space-y-2">
          {/* Get Help - for OSS mode */}
          {provider !== "stack" && (
            <>
              {state === "collapsed" ? (
                <TooltipProvider delayDuration={0}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="w-full hover:bg-accent hover:text-accent-foreground"
                        asChild
                      >
                        <a
                          href="https://github.com/dograh-hq/dograh/issues/new/choose"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <HelpCircle className="h-4 w-4" />
                          <span className="sr-only">Get Help</span>
                        </a>
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="right">
                      <p>Get Help</p>
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : (
                <Button
                  variant="ghost"
                  className="w-full justify-start hover:bg-accent hover:text-accent-foreground"
                  asChild
                >
                  <a
                    href="https://github.com/dograh-hq/dograh/issues/new/choose"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <HelpCircle className="h-4 w-4" />
                    <span className="ml-2">Get Help</span>
                  </a>
                </Button>
              )}
            </>
          )}

          {/* User Button for Stack Auth - at the bottom */}
          {provider === "stack" && (
            <React.Suspense
              fallback={
                <div className={cn(
                  "animate-pulse bg-muted rounded",
                  state === "collapsed" ? "h-8 w-8" : "h-[34px] w-[34px]"
                )} />
              }
            >
              <div className={cn(
                "flex",
                state === "collapsed" ? "justify-center" : "justify-start"
              )}>
                <StackUserButton
                  extraItems={[
                    {
                      text: "Usage",
                      icon: <CircleDollarSign strokeWidth={2} size={16} />,
                      onClick: () => router.push("/usage"),
                    },
                  ]}
                />
              </div>
            </React.Suspense>
          )}

          {/* Theme Toggle - at the very bottom */}
          <div className={cn(
            "mt-2 pt-2 border-t",
            state === "collapsed" ? "flex justify-center" : ""
          )}>
            {state === "collapsed" ? (
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
