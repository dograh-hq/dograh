"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { WorkflowConfigurations } from "@/types/workflow-configurations";

import { cleanConfiguration, emptyConfiguration } from "./configuration";
import type { ConfigurationSpec } from "./types";
import { useModelConnections } from "./useModelConnections";
import { customOverride, describeWorkflowModel, followOverride, isComplete, summarizeConfiguration, withOverride } from "./workflowModelOverride";

const SAVE_DELAY_MS = 400;

/**
 * Edit a workflow's model settings: follow an existing configuration, or
 * build custom settings from scratch.
 *
 * Custom edits save to the workflow draft a moment after they stop, once
 * every service has an account, so the next test call uses them. Saves run
 * one at a time, in order. Publishing carries the override into the published
 * version unchanged.
 */
export function useWorkflowModelOverride({ workflowName, workflowConfigurations, onSave }: {
    workflowName: string;
    workflowConfigurations: WorkflowConfigurations;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const connections = useModelConnections();
    const { catalog, configurations, defaultUuid } = connections;
    const binding = useMemo(
        () => describeWorkflowModel(workflowConfigurations, { configurations, defaultUuid, connections: connections.connections }),
        [workflowConfigurations, configurations, defaultUuid, connections.connections],
    );
    const [customizing, setCustomizing] = useState(false);
    const [pending, setPending] = useState<ConfigurationSpec | null>(null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const latest = useRef<{ configurations: WorkflowConfigurations; workflowName: string; spec: ConfigurationSpec | null }>({
        configurations: workflowConfigurations, workflowName, spec: null,
    });
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const queue = useRef<Promise<unknown>>(Promise.resolve());
    const mounted = useRef(true);
    latest.current.configurations = workflowConfigurations;
    latest.current.workflowName = workflowName;

    const view: "existing" | "custom" = customizing || binding.kind === "custom" ? "custom" : "existing";
    const saved = binding.kind === "custom" ? binding.spec : null;
    const configuration = view === "custom" ? pending ?? saved : null;

    // Saves are serialized so a later edit can never be overtaken by an
    // earlier request's response. Resolves to whether the save succeeded.
    const persist = useCallback((next: WorkflowConfigurations): Promise<boolean> => {
        const run = async () => {
            if (mounted.current) { setSaving(true); setError(null); }
            try {
                await onSave(next, latest.current.workflowName);
                return true;
            } catch (cause) {
                if (mounted.current) setError(cause instanceof Error ? cause.message : "Failed to save model settings");
                return false;
            } finally {
                if (mounted.current) setSaving(false);
            }
        };
        const result = queue.current.then(run, run);
        queue.current = result;
        return result;
    }, [onSave]);

    const flush = useCallback(async () => {
        if (timer.current) { clearTimeout(timer.current); timer.current = null; }
        const spec = latest.current.spec;
        // An incomplete spec waits for the user; it is never sent.
        if (!spec || !catalog || !isComplete(spec)) return;
        latest.current.spec = null;
        // On failure the edited settings stay on screen with the error, so the
        // user can correct them; the store keeps the last saved override.
        await persist(withOverride(latest.current.configurations, customOverride(cleanConfiguration(spec, catalog, connections.connections))));
    }, [catalog, connections.connections, persist]);
    const flushRef = useRef(flush);
    flushRef.current = flush;

    const edit = useCallback((next: ConfigurationSpec) => {
        setPending(next);
        latest.current.spec = next;
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => { void flushRef.current(); }, SAVE_DELAY_MS);
    }, []);

    // Leaving the editor must not lose an edit that is still waiting to save.
    useEffect(() => {
        const state = latest;
        const flushLatest = flushRef;
        mounted.current = true;
        return () => {
            mounted.current = false;
            if (state.current.spec) void flushLatest.current();
        };
    }, []);

    // The edited spec stays on screen until the saved configurations arrive,
    // unless another edit is already waiting to be saved.
    useEffect(() => { if (!latest.current.spec) setPending(null); }, [workflowConfigurations]);

    /** Switch to custom settings, starting from scratch. */
    const startCustom = useCallback(() => {
        if (!catalog) return;
        setCustomizing(true);
        edit(emptyConfiguration(catalog, connections.connections));
    }, [catalog, connections.connections, edit]);

    /** Follow a configuration (null for the organization default), dropping custom settings. */
    const useExisting = useCallback(async (uuid: string | null) => {
        latest.current.spec = null;
        if (timer.current) { clearTimeout(timer.current); timer.current = null; }
        setPending(null);
        setCustomizing(false);
        await persist(withOverride(latest.current.configurations, followOverride(uuid)));
    }, [persist]);

    const incomplete = configuration !== null && !isComplete(configuration);
    const shown = view === "custom" ? (incomplete ? null : configuration) : binding.kind === "existing" ? binding.base.configuration : null;
    return {
        binding,
        view,
        configuration,
        incomplete,
        summary: summarizeConfiguration(shown, connections.connections),
        shared: configurations.filter(item => item.is_active),
        dirty: view === "custom",
        edit,
        startCustom,
        useExisting,
        saving,
        error,
        catalog,
        connections: connections.connections,
        defaultUuid,
        loading: connections.loading,
        loadError: connections.error,
        reload: connections.reload,
    };
}

export type WorkflowModelOverride = ReturnType<typeof useWorkflowModelOverride>;
