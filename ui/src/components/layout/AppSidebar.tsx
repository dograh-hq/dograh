"use client";

import {
  AlertTriangle,
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Database,
  FileText,
  LogOut,
  type LucideIcon,
  Megaphone,
  Phone,
  Settings,
  TrendingUp,
  Workflow,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import React from "react";

import { BrandLogo } from "@/components/BrandLogo";
import { SidebarTeamSwitcher } from "@/components/layout/SidebarTeamSwitcher";
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
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTelephonyConfigWarnings } from "@/context/TelephonyConfigWarningsContext";
import type { LocalUser } from "@/lib/auth";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type SidebarNavItem = {
  title: string;
  url: string;
  icon: LucideIcon;
  showsTelephonyWarning?: boolean;
};

type SidebarNavSection = {
  items: SidebarNavItem[];
};

const TELEPHONY_WARNING_COPY = "Action required";

const NAV_SECTIONS: SidebarNavSection[] = [
  {
    items: [
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
        title: "Telephony",
        url: "/telephony-configurations",
        icon: Phone,
        showsTelephonyWarning: true,
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
    ],
  },
  {
    items: [
      {
        title: "Agent Runs",
        url: "/usage",
        icon: TrendingUp,
      },
      {
        title: "Reports",
        url: "/reports",
        icon: FileText,
      }
    ],
  },
];

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { state, isMobile, setOpenMobile } = useSidebar();
  const { provider, logout, user } = useAuth();
  const {
    telnyxMissingWebhookPublicKeyCount,
    vonageMissingSignatureSecretCount,
  } = useTelephonyConfigWarnings();
  const hasTelephonyWarning =
    telnyxMissingWebhookPublicKeyCount > 0 ||
    vonageMissingSignatureSecretCount > 0;
  const isCollapsed = !isMobile && state === "collapsed";

  const isActive = (path: string) => pathname.startsWith(path);

  const handleMobileNavClick = () => {
    if (isMobile) {
      setOpenMobile(false);
    }
  };

  const SidebarLink = ({ item }: { item: SidebarNavItem }) => {
    const isItemActive = isActive(item.url);
    const Icon = item.icon;
    const showWarningDot = item.showsTelephonyWarning && hasTelephonyWarning;
    const tooltip = {
      children: (
        <div className="notranslate" translate="no">
          <p>{item.title}</p>
          {showWarningDot && (
            <p className="text-amber-600 dark:text-amber-400">{TELEPHONY_WARNING_COPY}</p>
          )}
        </div>
      ),
    };
    const warningIndicator = (
      <AlertTriangle
        aria-label="Action required on a telephony configuration"
        className={cn(
          "text-amber-500",
          isCollapsed ? "absolute -right-0.5 -top-0.5 h-3 w-3" : "ml-auto h-3.5 w-3.5"
        )}
      />
    );

    return (
      <SidebarMenuButton
        asChild
        tooltip={tooltip}
        className={cn(
          "transition-colors text-muted-foreground hover:text-foreground text-[17px]",
          isItemActive && "font-medium text-foreground"
        )}
      >
        <Link
          href={item.url}
          onClick={handleMobileNavClick}
          className={cn("relative", isCollapsed && "justify-center")}
          translate="no"
        >
          <Icon
            className="h-5 w-5 shrink-0"
            strokeWidth={1.5}
          />
          <span
            className={cn("notranslate min-w-0 flex-1 truncate", isCollapsed && "sr-only")}
            translate="no"
          >
            {item.title}
          </span>
          {showWarningDot && (
            isCollapsed ? (
              warningIndicator
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  {warningIndicator}
                </TooltipTrigger>
                <TooltipContent side="right">
                  <p>{TELEPHONY_WARNING_COPY}</p>
                </TooltipContent>
              </Tooltip>
            )
          )}
        </Link>
      </SidebarMenuButton>
    );
  };

  // Footer identity trigger: avatar initials only (no name), in a subtle
  // bordered circle. Same treatment expanded and collapsed.
  const displayIdentity =
    user?.displayName ||
    (user as { primaryEmail?: string } | undefined)?.primaryEmail ||
    (user as LocalUser | undefined)?.email ||
    "";
  const userInitials =
    displayIdentity
      .split(/[\s@]/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s: string) => s[0]?.toUpperCase())
      .join("") || "U";

  const userChipTrigger = (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8 shrink-0 cursor-pointer rounded-full bg-foreground hover:bg-foreground/80"
    >
      <span className="text-sm font-medium text-background">{userInitials}</span>
    </Button>
  );

  return (
    <Sidebar collapsible="icon" variant="sidebar">
      <SidebarHeader className="px-3 pt-6 pb-10 border-b-0 notranslate" translate="no">
        <div className="flex items-center justify-between">
          <Link
            href="/"
            className={cn(
              "notranslate flex items-center gap-2.5",
              isCollapsed && "hidden"
            )}
            translate="no"
          >
            <BrandLogo mark className="h-8" />
            <span
              className="text-xl font-semibold tracking-tight text-foreground"
              style={{ fontFamily: "'Fraunces', serif" }}
            >
              Agent Studio
            </span>
          </Link>

          <SidebarTrigger className={cn("hover:bg-accent", isCollapsed && "mx-auto")}>
            {isCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </SidebarTrigger>
        </div>

        {provider === "stack" && (
          <div className={cn("mt-3 notranslate", isCollapsed && "hidden")} translate="no">
            <SidebarTeamSwitcher />
          </div>
        )}
      </SidebarHeader>

      <SidebarContent className={cn("notranslate", isCollapsed && "px-0")} translate="no">
        {NAV_SECTIONS.map((section, index) => (
          <SidebarGroup
            key={index}
            className={index === 0 ? "pt-0" : "pt-6"}
          >
            <SidebarMenu>
              {section.items.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarLink item={item} />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarFooter
        className={cn("px-3 py-3 notranslate", isCollapsed && "px-2")}
        translate="no"
      >
        <div className={cn("flex items-center justify-between", isCollapsed && "flex-col gap-2")}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              {userChipTrigger}
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="start" className="w-56">
              <DropdownMenuLabel className="font-normal">
                <div className="flex flex-col space-y-1">
                  {provider === "stack" && user?.displayName && (
                    <p className="text-sm font-medium">{user.displayName}</p>
                  )}
                  {provider === "stack" && (user as { primaryEmail?: string })?.primaryEmail && (
                    <p className="text-xs text-muted-foreground">{(user as { primaryEmail?: string }).primaryEmail}</p>
                  )}
                  {provider !== "stack" && (user as LocalUser | undefined)?.email && (
                    <p className="text-xs text-muted-foreground">{(user as LocalUser).email}</p>
                  )}
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {provider === "stack" && (
                <DropdownMenuItem onClick={() => router.push("/handler/account-settings")} className="cursor-pointer">
                  <Settings className="mr-2 h-4 w-4" />
                  Account settings
                </DropdownMenuItem>
              )}
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

          <Tooltip>
            <TooltipTrigger asChild>
              <div className="notranslate" translate="no">
                <ThemeToggle
                  showLabel={false}
                  variant="outline"
                  className="h-8 w-8 rounded-full border-border/80 text-foreground"
                />
              </div>
            </TooltipTrigger>
            <TooltipContent side={isCollapsed ? "right" : "top"}>
              <p>Toggle theme</p>
            </TooltipContent>
          </Tooltip>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}
