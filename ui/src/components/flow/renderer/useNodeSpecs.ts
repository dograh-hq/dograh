"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { listNodeTypesApiV1NodeTypesGet } from "@/client/sdk.gen";
import type { NodeSpec, NodeTypesResponse } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

interface State {
    specs: NodeSpec[];
    specVersion: string | null;
    loading: boolean;
    error: string | null;
}

let _cache: NodeTypesResponse | null = null;

/**
 * Fetches the node-spec catalog once per session and caches it in module
 * scope. Subsequent calls return the cached value synchronously.
 *
 * Spec changes require a backend restart and a page refresh — adding a new
 * node type while a session is active won't surface until reload.
 */
export function useNodeSpecs(): State & {
    bySpecName: Map<string, NodeSpec>;
} {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [state, setState] = useState<State>(() => ({
        specs: _cache?.node_types ?? [],
        specVersion: _cache?.spec_version ?? null,
        loading: !_cache,
        error: null,
    }));

    useEffect(() => {
        if (authLoading || !user || hasFetched.current || _cache) return;
        hasFetched.current = true;

        listNodeTypesApiV1NodeTypesGet({ throwOnError: true })
            .then(({ data }) => {
                _cache = data ?? null;
                setState({
                    specs: data?.node_types ?? [],
                    specVersion: data?.spec_version ?? null,
                    loading: false,
                    error: null,
                });
            })
            .catch((err: unknown) => {
                const message = err instanceof Error ? err.message : String(err);
                setState((s) => ({ ...s, loading: false, error: message }));
            });
    }, [authLoading, user]);

    const bySpecName = useMemo(() => {
        return new Map(state.specs.map((s) => [s.name, s]));
    }, [state.specs]);

    return { ...state, bySpecName };
}
