"use client";

import type { Team } from "@stackframe/stack";
import {
  AlertTriangle,
  AudioLines,
  BarChart3,
  Brain,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Contact,
  CreditCard,
  Database,
  Home,
  Key,
  LogOut,
  type LucideIcon,
  Megaphone,
  MessageCircle,
  Phone,
  PhoneCall,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
  Users,
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useTelephonyConfigWarnings } from "@/context/TelephonyConfigWarningsContext";
import { useUserConfig } from "@/context/UserConfigContext";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import type { LocalUser } from "@/lib/auth";
import { useAuth } from "@/lib/auth";
import { BRAND } from "@/lib/brand";
import { cn } from "@/lib/utils";

type SidebarNavItem = {
  title: string;
  url: string;
  icon: LucideIcon;
  showsTelephonyWarning?: boolean;
  /** Only visible to org admins (model/provider/API-key/engine settings). */
  adminOnly?: boolean;
  /** Only visible to superusers (deployment owner; stricter than adminOnly). */
  superuserOnly?: boolean;
  /** Only visible if the org's plan includes this feature (superuser always). */
  requiresFeature?: "api" | "mcp";
};

type SidebarNavSection = {
  label?: string;
  items: SidebarNavItem[];
};

const TELEPHONY_WARNING_COPY = "Action required";

const NAV_SECTIONS: SidebarNavSection[] = [
  {
    items: [
      {
        title: "Home",
        url: "/home",
        icon: Home,
      },
      {
        title: "KYC",
        url: "/kyc",
        icon: ShieldCheck,
      },
    ],
  },
  {
    label: "BUILD",
    items: [
      {
        title: "Build with AI",
        url: "/agent-builder",
        icon: Sparkles,
      },
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
        title: "Models",
        url: "/model-configurations",
        icon: Brain,
        // Client-visible on purpose: orgs pick their own voice/language/model
        // (and paste their own provider keys) in the portal.
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
      {
        title: "Developers",
        url: "/api-keys",
        icon: Key,
        requiresFeature: "api",
      },
    ],
  },
  {
    label: "INTEGRATIONS",
    items: [
      {
        title: "WhatsApp",
        url: "/integrations/whatsapp",
        icon: MessageCircle,
      },
      {
        title: "CRM",
        url: "/integrations/crm",
        icon: Contact,
      },
      {
        title: "Phone Numbers",
        url: "/phone-numbers",
        icon: PhoneCall,
      },
      {
        title: "Credits & Billing",
        url: "/credits",
        icon: CreditCard,
      },
    ],
  },
  {
    label: "MANAGE",
    items: [
      {
        title: "Analytics",
        url: "/analytics",
        icon: BarChart3,
      },
    ],
  },
  {
    label: "ACCOUNT",
    items: [
      {
        title: "Settings",
        url: "/settings",
        icon: Settings,
      },
      {
        title: "Clients",
        url: "/clients",
        icon: Users,
        superuserOnly: true,
      },
    ],
  },
];

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
  const { isAdmin } = useIsAdmin();
  const { isSuperuser, planFeatures } = useUserConfig();
  const { openHireExpert } = useLeadForms();
  const { telnyxMissingWebhookPublicKeyCount } = useTelephonyConfigWarnings();
  const hasTelephonyWarning = telnyxMissingWebhookPublicKeyCount > 0;
  const isCollapsed = !isMobile && state === "collapsed";

  // Get selected team for Stack auth (cast to Team type from Stack)
  // Stabilize the reference so SelectedTeamSwitcher only sees a change when the team ID changes,
  // preventing unnecessary PATCH calls to Stack Auth on every route navigation.
  const selectedTeamRef = useRef<Team | null>(null);
  const rawSelectedTeam = provider === "stack" && getSelectedTeam ? getSelectedTeam() as Team | null : null;
  if (rawSelectedTeam?.id !== selectedTeamRef.current?.id) {
    selectedTeamRef.current = rawSelectedTeam;
  }
  const selectedTeam = selectedTeamRef.current;

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
          "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
          isItemActive && "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
        )}
      >
        <Link
          href={item.url}
          onClick={handleMobileNavClick}
          className={cn("relative", isCollapsed && "justify-center")}
          translate="no"
        >
          {isItemActive && !isCollapsed && (
            <span
              className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-cta"
              aria-hidden
            />
          )}
          <Icon
            className={cn(
              "h-4 w-4 shrink-0",
              isItemActive && "text-cta drop-shadow-[0_0_6px_rgba(240,170,70,0.8)]"
            )}
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
      className="h-7 w-7 shrink-0 cursor-pointer rounded-full border border-border/80 bg-muted/40 hover:bg-muted/60"
    >
      <span className="text-xs font-medium">{userInitials}</span>
    </Button>
  );

  // "Hire an Expert" CTA, rendered INSIDE the shared footer pill next to the
  // profile icon. Expanded: label pill filling the row. Collapsed: icon-only.
  const hireExpertButton = isCollapsed ? (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          size="icon"
          className="h-7 w-7 rounded-full"
          onClick={() => openHireExpert("sidebar")}
          aria-label="Hire an Expert"
        >
          <UserRound className="h-3.5 w-3.5" />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">
        <p>Hire an Expert</p>
      </TooltipContent>
    </Tooltip>
  ) : (
    <Button
      size="sm"
      className="h-7 gap-1.5 rounded-full px-3 text-xs"
      onClick={() => openHireExpert("sidebar")}
    >
      <UserRound className="h-3.5 w-3.5" />
      Hire an Expert
    </Button>
  );

  return (
    <Sidebar collapsible="icon" variant="floating" className="app-sidebar-dock py-4">
      <SidebarHeader className="px-2 py-3 notranslate" translate="no">
        <div className="flex items-center justify-between">
          <div className={cn("flex items-center gap-2", isCollapsed && "hidden")}>
            <Link
              href="/"
              className="notranslate flex items-center gap-2 px-1"
              translate="no"
            >
              {BRAND.logoUrl && (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img src={BRAND.logoUrl} alt={BRAND.name} className="h-6 w-auto" />
              )}
              {BRAND.name}
            </Link>
          </div>

          <SidebarTrigger className={cn("hover:bg-sidebar-accent", isCollapsed && "mx-auto")}>
            {isCollapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </SidebarTrigger>
        </div>

        {provider === "stack" && (
          <div className={cn("mt-3 notranslate", isCollapsed && "hidden")} translate="no">
            <React.Suspense
              fallback={
                <div className="h-9 w-full animate-pulse rounded bg-muted" />
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

      <SidebarContent className={cn("notranslate", isCollapsed && "px-0")} translate="no">
        {NAV_SECTIONS.map((section, index) => {
          const visibleItems = section.items.filter(
            (item) =>
              (!item.adminOnly || isAdmin) &&
              (!item.superuserOnly || isSuperuser) &&
              (!item.requiresFeature ||
                isSuperuser ||
                planFeatures[item.requiresFeature])
          );
          if (visibleItems.length === 0) {
            return null;
          }
          return (
            <SidebarGroup
              key={section.label ?? "main"}
              className={index === 0 ? "mt-2" : "mt-6"}
            >
              {section.label && (
                <SidebarGroupLabel
                  className={cn(
                    "notranslate text-xs font-semibold uppercase tracking-wider text-muted-foreground",
                    isCollapsed && "hidden"
                  )}
                  translate="no"
                >
                  {section.label}
                </SidebarGroupLabel>
              )}
              <SidebarMenu>
                {visibleItems.map((item) => (
                  <SidebarMenuItem key={item.title}>
                    <SidebarLink item={item} />
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroup>
          );
        })}
      </SidebarContent>

      <SidebarFooter
        className={cn("p-3 notranslate", isCollapsed && "p-2")}
        translate="no"
      >
        <div className="space-y-2">
          {provider !== "stack" && (
            <div
              className={cn(
                "flex items-center justify-between gap-1 rounded-full border border-border/60 bg-muted/30 p-1",
                isCollapsed && "flex-col"
              )}
            >
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  {userChipTrigger}
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
                  {isAdmin && (
                    <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
                      <Settings className="mr-2 h-4 w-4" />
                      Platform Settings
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => logout()} className="cursor-pointer">
                    <LogOut className="mr-2 h-4 w-4" />
                    Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              {hireExpertButton}
            </div>
          )}

          {provider === "stack" && (
            <div
              className={cn(
                "flex items-center justify-between gap-1 rounded-full border border-border/60 bg-muted/30 p-1",
                isCollapsed && "flex-col"
              )}
            >
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  {userChipTrigger}
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
                  {isAdmin && (
                    <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
                      <Settings className="mr-2 h-4 w-4" />
                      Platform Settings
                    </DropdownMenuItem>
                  )}
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
              {hireExpertButton}
            </div>
          )}

          <div className={cn("mt-2 border-t pt-2", isCollapsed && "flex justify-center")}>
            {isCollapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="notranslate" translate="no">
                    <ThemeToggle
                      showLabel={false}
                      className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                    />
                  </div>
                </TooltipTrigger>
                <TooltipContent side="right">
                  <p>Toggle theme</p>
                </TooltipContent>
              </Tooltip>
            ) : (
              <div className="notranslate" translate="no">
                <ThemeToggle
                  showLabel={true}
                  className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                />
              </div>
            )}
          </div>
        </div>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
