import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

import { getAuthUserApiV1UserAuthUserGet } from "@/client/sdk.gen";
import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";
import { impersonateApiV1SuperuserImpersonatePost } from "@/client/sdk.gen";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function getRandomId() {
  return Math.floor(Math.random() * 10_000);
}

export function debounce<T extends (...args: unknown[]) => unknown>(func: T, wait: number): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null;

  return function (...args: Parameters<T>) {
    if (timeout) {
      clearTimeout(timeout);
    }

    timeout = setTimeout(() => {
      func(...args);
    }, wait);
  };
}

export async function getRedirectUrl(token: string, permissions: { id: string }[] = []) {
  console.log('[getRedirectUrl] Called with:', {
    hasToken: !!token,
    tokenLength: token?.length,
    permissionsCount: permissions.length,
    permissions: permissions.map(p => p.id)
  });
  try {
    console.log('[getRedirectUrl] Calling getAuthUserApiV1UserAuthUserGet...');
    const authUser = await getAuthUserApiV1UserAuthUserGet({
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    console.log('[getRedirectUrl] Auth user response:', {
      hasData: !!authUser.data,
      isSuperuser: authUser.data?.is_superuser,
      userId: authUser.data?.id
    });
    if (authUser.data?.is_superuser) {
      console.log('[getRedirectUrl] User is superuser, redirecting to /superadmin');
      return "/superadmin";
    }

    const hasAdminPermission = permissions.some(p => p.id === 'admin');
    console.log('[getRedirectUrl] Admin permission check:', { hasAdminPermission });

  // If the user doesn't have admin permissions, redirect them to
  // usage page
  if (!hasAdminPermission) {
    console.log('[getRedirectUrl] No admin permission, redirecting to /usage');
    return "/usage";
  }

  // Check if user has any workflows
  try {
    console.log('[getRedirectUrl] Checking for existing workflows...');
    const workflowsResponse = await getWorkflowsApiV1WorkflowFetchGet({
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    const workflows = workflowsResponse.data ? (Array.isArray(workflowsResponse.data) ? workflowsResponse.data : [workflowsResponse.data]) : [];
    const activeWorkflows = workflows.filter(w => w.status === 'active');

    console.log('[getRedirectUrl] Found workflows:', {
      total: workflows.length,
      active: activeWorkflows.length
    });

    if (activeWorkflows.length > 0) {
      console.log('[getRedirectUrl] User has workflows, redirecting to /workflow');
      return "/workflow";
    } else {
      console.log('[getRedirectUrl] No workflows found, redirecting to /create-workflow');
      return "/create-workflow";
    }
  } catch (error) {
    console.error('[getRedirectUrl] Error checking workflows:', error);
    // If we can't check workflows, default to create-workflow
    console.log('[getRedirectUrl] Defaulting to /create-workflow due to error');
    return "/create-workflow";
  }
  } catch (error) {
    console.error("[getRedirectUrl] Failed to fetch auth user:", error);
    // Re-throw the error so the caller can handle it
    throw error;
  }
}

/**
 * Check if the application is running in OSS (Open Source Software) mode
 */
export function isOSSMode(): boolean {
  return process.env.NEXT_PUBLIC_DEPLOYMENT_MODE === 'oss';
}

/**
 * --------------------------------------------------------------------------
 * Cookie helpers
 * --------------------------------------------------------------------------
 */

export function setStackRefreshCookie(refreshToken: string) {
  const expiryDate = new Date();
  expiryDate.setFullYear(expiryDate.getFullYear() + 1);

  const isDograhDomain = window.location.hostname.endsWith('.dograh.com');
  const cookieDomainPart = isDograhDomain ? '; domain=.dograh.com' : '';

  document.cookie =
    `stack-refresh-${process.env.NEXT_PUBLIC_STACK_PROJECT_ID}=${refreshToken}; ` +
    `expires=${expiryDate.toUTCString()}; path=/` +
    `${cookieDomainPart}; secure; samesite=lax`;
}

/**
 * Centralised impersonation logic to avoid code duplication between pages.
 *
 * It performs the super-admin impersonate request, sets the cross-sub-domain
 * refresh cookie and optionally redirects the browser to the supplied path.
 */
export async function impersonateAsSuperadmin(params: {
  accessToken: string;
  userId?: number;
  providerUserId?: string;
  redirectPath?: string;
  /**
   * If true the browser opens the impersonated session in a **new tab**
   * (via `window.open`). Defaults to `false` which navigates in the current tab.
   */
  openInNewTab?: boolean;
}): Promise<void> {
  const { accessToken, userId, providerUserId, redirectPath, openInNewTab = false } = params;

  // Build request body depending on which identifier we have.
  const body: Record<string, unknown> = {};
  if (userId !== undefined) {
    body.user_id = userId;
  }
  if (providerUserId !== undefined) {
    body.provider_user_id = providerUserId;
  }

  if (Object.keys(body).length === 0) {
    throw new Error('Either userId or providerUserId must be provided');
  }

  const resp = await impersonateApiV1SuperuserImpersonatePost({
    body,
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
  });

  const refreshToken = resp.data?.refresh_token;
  if (!refreshToken) {
    throw new Error('No refresh token returned from impersonate');
  }

  // ---------------------------------------------------------------------------------
  // Instead of setting the cookie here (which would also affect the superadmin
  // sub-domain), redirect the browser to the dedicated impersonation helper route
  // (served from the target sub-domain, e.g. app.dograh.com). The route will set the
  // cookie for the *current* sub-domain only and then forward the user to the final
  // destination.
  // ---------------------------------------------------------------------------------

  // Determine the base URL that should handle the impersonation cookie. If we are on
  // superadmin.dograh.com we want to switch to app.dograh.com. For any other domain
  // (e.g. localhost, staging, or already on the app) we just keep the same origin.
  const appBaseUrl = window.location.origin.includes('superadmin.')
    ? window.location.origin.replace('superadmin.', 'app.')
    : window.location.origin;

  const finalRedirect = redirectPath ?? '/workflow';

  // Build the redirect URL to the helper route, passing along the refresh token and
  // the final destination.
  const impersonateUrl = `${appBaseUrl}/impersonate?refresh_token=${encodeURIComponent(
    refreshToken,
  )}&redirect_path=${encodeURIComponent(finalRedirect)}`;

  if (openInNewTab) {
    window.open(impersonateUrl, '_blank');
  } else {
    window.location.href = impersonateUrl;
  }
}

