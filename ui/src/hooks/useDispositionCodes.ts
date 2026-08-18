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
 *
 * Cached at module scope: several run-listing screens mount this hook and the
 * catalog only changes when a run records a code we haven't seen before.
 */
let cachedCodes: string[] | null = null;
let inFlight: Promise<string[]> | null = null;

const loadDispositionCodes = (): Promise<string[]> => {
    if (cachedCodes) return Promise.resolve(cachedCodes);
    if (inFlight) return inFlight;

    inFlight = (async () => {
        const response = await getDispositionCodesApiV1OrganizationsDispositionCodesGet();
        if (response.error) {
            throw new Error(detailFromError(response.error, "Failed to load disposition codes"));
        }
        cachedCodes = response.data?.codes ?? [];
        return cachedCodes;
    })();

    // A failed fetch must not poison the cache — the next mount should retry.
    inFlight.catch(() => { inFlight = null; });

    return inFlight;
};

export function useDispositionCodes(): { codes: string[]; isLoading: boolean } {
    const { isAuthenticated } = useAuth();
    const [codes, setCodes] = useState<string[]>(() => cachedCodes ?? []);
    const [isLoading, setIsLoading] = useState(() => !cachedCodes);

    useEffect(() => {
        if (!isAuthenticated || cachedCodes) return;

        let active = true;
        setIsLoading(true);

        loadDispositionCodes()
            .then(loaded => {
                if (active) setCodes(loaded);
            })
            .catch(error => {
                console.error("Failed to fetch disposition codes:", error);
            })
            .finally(() => {
                if (active) setIsLoading(false);
            });

        return () => {
            active = false;
        };
    }, [isAuthenticated]);

    return { codes, isLoading };
}
