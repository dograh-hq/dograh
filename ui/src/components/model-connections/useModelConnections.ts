"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getDefaultModelConfiguration, getModelConnectionCatalog, listNamedModelConfigurations, listProviderConnections } from "@/client/sdk.gen";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";

export function useModelConnections({ includeArchived = false }: { includeArchived?: boolean } = {}) {
    const { user, loading: authLoading } = useAuth();
    const { orgContext, loading: organizationLoading } = useOrgConfig();
    const [catalog, setCatalog] = useState<ModelConnectionCatalog | null>(null);
    const [connections, setConnections] = useState<ProviderConnection[]>([]);
    const [configurations, setConfigurations] = useState<NamedModelConfiguration[]>([]);
    const [defaultUuid, setDefaultUuid] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const request = useRef(0);
    const organizationId = orgContext?.organization_id;
    const userId = user?.id;

    const reload = useCallback(async () => {
        // Organization-context requests also finish signup provisioning. Wait
        // for them so parallel catalog reads cannot capture a half-built setup.
        if (authLoading || !userId || organizationLoading || organizationId == null) return;
        const currentRequest = ++request.current;
        setLoading(true);
        setError(null);
        try {
            const [catalogResult, connectionsResult, configurationsResult, defaultResult] = await Promise.all([
                getModelConnectionCatalog(),
                listProviderConnections({ query: { include_archived: includeArchived } }),
                listNamedModelConfigurations({ query: { include_archived: includeArchived } }),
                getDefaultModelConfiguration(),
            ]);
            if (currentRequest !== request.current) return;
            for (const response of [catalogResult, connectionsResult, configurationsResult, defaultResult]) {
                if (response.error) throw new Error(detailFromError(response.error, "Failed to load model configurations"));
            }
            if (!catalogResult.data || !connectionsResult.data || !configurationsResult.data || !defaultResult.data) throw new Error("Failed to load model configurations");
            setCatalog(catalogResult.data as unknown as ModelConnectionCatalog);
            setConnections(connectionsResult.data as ProviderConnection[]);
            setConfigurations(configurationsResult.data as NamedModelConfiguration[]);
            setDefaultUuid(defaultResult.data.model_configuration_uuid);
        } catch (cause) {
            if (currentRequest === request.current) setError(cause instanceof Error ? cause.message : "Failed to load model configurations");
        } finally {
            if (currentRequest === request.current) setLoading(false);
        }
    }, [authLoading, userId, organizationLoading, organizationId, includeArchived]);

    useEffect(() => {
        // Keep the current catalog visible when refreshConfig toggles loading.
        // Only discard it when the user, organization, or archive filter changes.
        setCatalog(null);
        setConnections([]);
        setConfigurations([]);
        setDefaultUuid(null);
        setLoading(true);
        setError(null);
    }, [userId, organizationId, includeArchived]);

    useEffect(() => {
        void reload();
        return () => { request.current += 1; };
    }, [reload]);

    return { catalog, connections, configurations, defaultUuid, loading, error, reload };
}
