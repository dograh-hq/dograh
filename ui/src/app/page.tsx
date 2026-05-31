import { isNextRouterError } from "next/dist/client/components/is-next-router-error";
import { redirect } from "next/navigation";

import { getWorkflowCountApiV1WorkflowCountGet } from "@/client/sdk.gen";
import { getServerAccessToken } from "@/lib/auth/server";

export const dynamic = 'force-dynamic';

export default async function Home() {
  try {
    const accessToken = await getServerAccessToken();
    const countResponse = await getWorkflowCountApiV1WorkflowCountGet({
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (countResponse.data && countResponse.data.active > 0) {
      redirect('/workflow');
    } else {
      redirect('/workflow/create');
    }
  } catch (error) {
    if (isNextRouterError(error)) {
      throw error;
    }
    redirect('/workflow/create');
  }
}
