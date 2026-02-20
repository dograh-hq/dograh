import { isNextRouterError } from "next/dist/client/components/is-next-router-error";
import { redirect } from "next/navigation";

import { getWorkflowCountApiV1WorkflowCountGet } from "@/client/sdk.gen";
import { getServerAccessToken,getServerAuthProvider, getServerUser } from "@/lib/auth/server";
import logger from '@/lib/logger';
import { getRedirectUrl } from "@/lib/utils";

export const dynamic = 'force-dynamic';

export default async function AfterSignInPage() {
    logger.debug('[AfterSignInPage] Starting after-sign-in page');
    const authProvider = await getServerAuthProvider();
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
            const countResponse = await getWorkflowCountApiV1WorkflowCountGet({
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            logger.debug('[AfterSignInPage] Found workflows:', {
                total: countResponse.data?.total,
                active: countResponse.data?.active
            });

            if (countResponse.data && countResponse.data.active > 0) {
                logger.debug('[AfterSignInPage] Redirecting to /workflow - user has workflows');
                redirect('/workflow');
            } else {
                logger.debug('[AfterSignInPage] Redirecting to /workflow/create - no workflows found');
                redirect('/workflow/create');
            }
        }
    } catch (error) {
        if (isNextRouterError(error)) {
            throw error;
        }
        logger.error('[AfterSignInPage] Error checking workflows:', error);
    }

    // Default fallback
    logger.debug('[AfterSignInPage] Final fallback redirect to /workflow/create');
    redirect('/workflow/create');
}
