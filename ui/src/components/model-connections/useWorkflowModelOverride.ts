"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { WorkflowConfigurations } from "@/types/workflow-configurations";

import { cleanConfiguration, emptyConfiguration } from "./configuration";
import type { ConfigurationSpec } from "./types";
import { useModelConnections } from "./useModelConnections";
import { customOverride, describeWorkflowModel, followOverride, isComplete, summarizeConfiguration, withOverride } from "./workflowModelOverride";

const SAVE_DELAY_MS = 400;

/**
 * Edit a workflow's model settings: follow a preset configuration, or build
 * custom settings from scratch.
 *
 * Custom settings start as an unsaved draft and save to the workflow draft a
 * moment after the first change, once every service has an account, so the
 * next test call uses them. Saves run one at a time, in order. Publishing
 * carries the override into the published version unchanged.
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
    // An edit the saved configurations do not reflect yet.
    const [pending, setPending] = useState<ConfigurationSpec | null>(null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const latest = useRef<{ configurations: WorkflowConfigurations; workflowName: string; spec: ConfigurationSpec | null }>({
        configurations: workflowConfigurations, workflowName, spec: null,
    });
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const queue = useRef<Promise<unknown>>(Promise.resolve());
    const mounted = useRef(true);
    // The preset the agent followed before custom settings were started, so
    // leaving them returns there.
    const origin = useRef<string | null>(null);
    latest.current.configurations = workflowConfigurations;
    latest.current.workflowName = workflowName;

    const view: "existing" | "custom" = customizing || binding.kind === "custom" ? "custom" : "existing";
    const saved = binding.kind === "custom" ? binding.spec : null;
    // Custom settings start from scratch: the first compatible account for
    // each service. Nothing is saved until the user changes something.
    const seed = useMemo(() => (catalog ? emptyConfiguration(catalog, connections.connections) : null), [catalog, connections.connections]);
    const configuration = view === "custom" ? pending ?? saved ?? seed : null;

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
        const ok = await persist(withOverride(latest.current.configurations, customOverride(cleanConfiguration(spec, catalog, connections.connections))));
        // A failed edit stays waiting: it is shown with the error, counts as
        // unsaved, and is retried on the next change or on leaving.
        if (!ok && latest.current.spec === null) latest.current.spec = spec;
    }, [catalog, connections.connections, persist]);
    const flushRef = useRef(flush);
    flushRef.current = flush;

    const clearWaiting = useCallback(() => {
        latest.current.spec = null;
        if (timer.current) { clearTimeout(timer.current); timer.current = null; }
        setPending(null);
    }, []);

    const edit = useCallback((next: ConfigurationSpec) => {
        setPending(next);
        latest.current.spec = next;
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => { void flushRef.current(); }, SAVE_DELAY_MS);
    }, []);

    /** Send a waiting edit now, for retrying after a failed save. */
    const retry = useCallback(() => { void flushRef.current(); }, []);

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
        origin.current = binding.kind === "existing" && !binding.isDefault ? binding.base.uuid : null;
        setCustomizing(true);
    }, [binding]);

    /**
     * Back to the preset tab. Saved custom settings are dropped in favour of
     * the preset they started from; a draft that was never saved is just
     * discarded, so the agent's settings are untouched.
     */
    const leaveCustom = useCallback(async () => {
        clearWaiting();
        setCustomizing(false);
        setError(null);
        if (binding.kind !== "custom") return;
        await persist(withOverride(latest.current.configurations, followOverride(origin.current ?? binding.baseUuid)));
    }, [binding, clearWaiting, persist]);

    /** Follow a preset (null for the organization default), dropping custom settings. */
    const useExisting = useCallback(async (uuid: string | null) => {
        clearWaiting();
        setCustomizing(false);
        const followed = binding.kind === "existing" ? (binding.isDefault ? null : binding.base.uuid) : undefined;
        if (followed === uuid) return;
        await persist(withOverride(latest.current.configurations, followOverride(uuid)));
    }, [binding, clearWaiting, persist]);

    const incomplete = configuration !== null && !isComplete(configuration);
    const shown = view === "custom" ? (incomplete ? null : configuration) : binding.kind === "existing" ? binding.base.configuration : null;
    return {
        binding,
        view,
        configuration,
        incomplete,
        /** A change has been made to the custom settings since they were last saved. */
        edited: pending !== null,
        /** An edit the autosave cannot finish by itself: a service still needs an account, or the last save failed. */
        dirty: pending !== null && (incomplete || error !== null),
        summary: summarizeConfiguration(shown, connections.connections),
        shared: configurations.filter(item => item.is_active),
        edit,
        retry,
        startCustom,
        leaveCustom,
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
