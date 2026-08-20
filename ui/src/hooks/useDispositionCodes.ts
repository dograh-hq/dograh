import { useEffect, useState } from "react";

import { getDispositionCodesApiV1OrganizationsDispositionCodesGet } from "@/client/sdk.gen";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/**
 * Disposition codes available to the current organization, served by the
 * backend rather than hardcoded in the frontend.
 *
 * The catalog is the union of the platform's built-in dispositions (derived
 * from the enums that write `gathered_context.mapped_call_disposition`) and
 * any custom mapped codes the org's runs have produced. Hardcoding it here
 * meant the list silently fell behind every new disposition the backend
 * learned to write.
 */
export function useDispositionCodes(): {
    codes: string[];
    endTaskReasonCodes: string[];
    isLoading: boolean;
} {
    const { user, loading: authLoading } = useAuth();
    const [codes, setCodes] = useState<string[]>([]);
    const [endTaskReasonCodes, setEndTaskReasonCodes] = useState<string[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        if (authLoading || !user) return;

        let active = true;
        setIsLoading(true);

        const loadDispositionCodes = async () => {
            try {
                const response = await getDispositionCodesApiV1OrganizationsDispositionCodesGet();
                if (response.error) {
                    throw new Error(detailFromError(response.error, "Failed to load disposition codes"));
                }
                if (active) {
                    setCodes(response.data?.codes ?? []);
                    setEndTaskReasonCodes(response.data?.end_task_reason_codes ?? []);
                }
            } catch (error) {
                console.error("Failed to fetch disposition codes:", error);
            } finally {
                if (active) setIsLoading(false);
            }
        };

        void loadDispositionCodes();

        return () => {
            active = false;
        };
    }, [authLoading, user]);

    return { codes, endTaskReasonCodes, isLoading };
}
