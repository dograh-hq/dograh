import { redirect } from "next/navigation";

import { getWorkflowsApiV1WorkflowFetchGet } from "@/client/sdk.gen";
import { getServerAccessToken,getServerAuthProvider, getServerUser } from "@/lib/auth/server";
import logger from '@/lib/logger';
import { getRedirectUrl } from "@/lib/utils";

export const dynamic = 'force-dynamic';

export default async function AfterSignInPage() {
    logger.debug('[AfterSignInPage] Starting after-sign-in page');
    const authProvider = getServerAuthProvider();
    logger.debug('[AfterSignInPage] Auth provider:', authProvider);
    logger.debug('[AfterSignInPage] Getting server user...');
    const user = await getServerUser();
    logger.debug('[AfterSignInPage] Got user:', { hasUser: !!user, userId: user?.id });

    if (authProvider === 'stack' && user && 'getAuthJson' in user) {
        logger.debug('[AfterSignInPage] Stack user detected, getting auth token...');
        const token = await user.getAuthJson();
        logger.debug('[AfterSignInPage] Got token:', { hasToken: !!token?.accessToken });
        const permissions = 'listPermissions' in user && 'selectedTeam' in user
            ? await user.listPermissions(user.selectedTeam!) ?? []
            : [];
        logger.debug('[AfterSignInPage] Got permissions:', { count: permissions.length });
        const redirectUrl = await getRedirectUrl(token?.accessToken ?? "", permissions);
        logger.debug('[AfterSignInPage] Redirecting to:', redirectUrl);
        redirect(redirectUrl);
    }

    // For local provider or if user is not available, check for existing workflows
    logger.debug('[AfterSignInPage] Checking for existing workflows before fallback');

    try {
        const accessToken = await getServerAccessToken();
        if (accessToken) {
            const workflowsResponse = await getWorkflowsApiV1WorkflowFetchGet({
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            const workflows = workflowsResponse.data ? (Array.isArray(workflowsResponse.data) ? workflowsResponse.data : [workflowsResponse.data]) : [];
            const activeWorkflows = workflows.filter(w => w.status === 'active');

            logger.debug('[AfterSignInPage] Found workflows:', {
                total: workflows.length,
                active: activeWorkflows.length
            });

            if (activeWorkflows.length > 0) {
                logger.debug('[AfterSignInPage] Redirecting to /workflow - user has workflows');
                redirect('/workflow');
            } else {
                logger.debug('[AfterSignInPage] Redirecting to /workflow/create - no workflows found');
                redirect('/workflow/create');
            }
        }
    } catch (error) {
        logger.error('[AfterSignInPage] Error checking workflows:', error);
    }

    // Default fallback
    logger.debug('[AfterSignInPage] Final fallback redirect to /workflow/create');
    redirect('/workflow/create');
}
