import type { ModelConfigurationOverride } from "@/client/types.gen";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

import { configurationMode } from "./configuration";
import type { ConfigurationSpec, NamedModelConfiguration, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { ROLES } from "./types";

/**
 * A workflow's model settings live entirely in its definition, as
 * `model_configuration_override`. Either the agent follows a configuration
 * (`model_configuration_uuid`, or the organization default when the override
 * is empty) or it carries custom settings: a complete spec stored as the
 * override patch, which the runtime resolver layers over the default.
 */
export type ModelPatch = Omit<ModelConfigurationOverride, "model_configuration_uuid">;

const PATCH_KEYS = ["mode", "llm", "llm_fallback", "stt", "tts", "realtime", "embeddings"] as const;

export function splitOverride(override: ModelConfigurationOverride | null | undefined): { uuid: string | null; patch: ModelPatch } {
    const source = (override ?? {}) as Record<string, unknown>;
    const patch: Record<string, unknown> = {};
    for (const key of PATCH_KEYS) if (source[key] !== undefined) patch[key] = source[key];
    return { uuid: (source.model_configuration_uuid as string | null | undefined) || null, patch: patch as ModelPatch };
}

export function hasPatch(patch: ModelPatch): boolean {
    return Object.keys(patch).length > 0;
}

export type WorkflowModelBinding =
    /** Nothing resolves: no organization default and no configuration chosen. */
    | { kind: "none" }
    /** Inline settings saved before named configurations existed. */
    | { kind: "legacy" }
    /** Bound to a configuration that was archived or removed. */
    | { kind: "unavailable"; uuid: string }
    | { kind: "existing"; base: NamedModelConfiguration; isDefault: boolean }
    /** `baseUuid` is the configuration an older partial patch was layered on, if any. */
    | { kind: "custom"; spec: ConfigurationSpec; baseUuid: string | null };

export function describeWorkflowModel(
    workflowConfigurations: { model_configuration_override?: ModelConfigurationOverride | null; model_overrides?: unknown; model_configuration_v2_override?: unknown },
    { configurations, defaultUuid, connections }: { configurations: NamedModelConfiguration[]; defaultUuid: string | null; connections: ProviderConnection[] },
): WorkflowModelBinding {
    const override = workflowConfigurations.model_configuration_override;
    if (override == null && (workflowConfigurations.model_overrides || workflowConfigurations.model_configuration_v2_override)) return { kind: "legacy" };
    const { uuid, patch } = splitOverride(override);
    const active = configurations.filter(item => item.is_active);
    const base = active.find(item => item.uuid === (uuid ?? defaultUuid));
    if (hasPatch(patch)) return { kind: "custom", spec: base ? applyModelPatch(base.configuration, patch, connections) : specFromPatch(patch), baseUuid: uuid };
    if (uuid) return base ? { kind: "existing", base, isDefault: false } : { kind: "unavailable", uuid };
    return base ? { kind: "existing", base, isDefault: true } : { kind: "none" };
}

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
type EditableSpec = Omit<ConfigurationSpec, ServiceRole> & Partial<Record<ServiceRole, ServiceSelection | null>>;

export function activeRoles(mode: ConfigurationSpec["mode"]): ServiceRole[] {
    return mode === "realtime" ? ["llm", "realtime", "embeddings"] : ["llm", "stt", "tts", "embeddings"];
}

/**
 * Layer a patch over a base the way the server's resolver does: mode and the
 * fallback policy replace, each service keeps the base settings when the
 * account stays with the same provider and starts clean otherwise, then the
 * patch settings apply on top. Only embeddings may be removed (null).
 */
export function applyModelPatch(base: ConfigurationSpec, patch: ModelPatch, connections: ProviderConnection[]): ConfigurationSpec {
    const result = clone(base) as unknown as EditableSpec;
    const provider = (uuid?: string | null) => connections.find(item => item.uuid === uuid)?.provider;
    if (patch.mode) result.mode = patch.mode;
    if (patch.llm_fallback !== undefined) result.llm_fallback = (patch.llm_fallback ?? null) as ConfigurationSpec["llm_fallback"];
    for (const role of ROLES) {
        const selection = patch[role];
        if (selection === undefined) continue;
        if (selection === null) { result[role] = null; continue; }
        const previous = result[role] ?? null;
        const oldUuid = previous?.provider_connection_uuid;
        const newUuid = selection.provider_connection_uuid ?? oldUuid ?? "";
        let settings: Record<string, unknown> = { ...(previous?.settings ?? {}) };
        if (newUuid && newUuid !== oldUuid && (!oldUuid || provider(oldUuid) !== provider(newUuid))) settings = {};
        result[role] = { provider_connection_uuid: newUuid, settings: { ...settings, ...(selection.settings ?? {}) } };
    }
    return result as unknown as ConfigurationSpec;
}

/** A spec built from a patch alone, for a custom override with no base to layer it on. */
export function specFromPatch(patch: ModelPatch): ConfigurationSpec {
    const mode = patch.mode ?? "pipeline";
    const selection = (role: ServiceRole): ServiceSelection | null => {
        const value = patch[role];
        return value ? { provider_connection_uuid: value.provider_connection_uuid ?? "", settings: { ...(value.settings ?? {}) } } : null;
    };
    const spec: EditableSpec = { version: 3, mode, llm: selection("llm") ?? { provider_connection_uuid: "", settings: {} }, embeddings: selection("embeddings") };
    for (const role of activeRoles(mode)) if (role !== "llm" && role !== "embeddings") spec[role] = selection(role);
    // An empty policy only disables inherited fallbacks; it is not a policy to show.
    if (patch.llm_fallback?.rules?.length) spec.llm_fallback = patch.llm_fallback as ConfigurationSpec["llm_fallback"];
    return spec as unknown as ConfigurationSpec;
}

/** Every service the mode needs has an account chosen. */
export function isComplete(spec: ConfigurationSpec): boolean {
    return activeRoles(spec.mode).every(role => role === "embeddings" || Boolean(spec[role]?.provider_connection_uuid));
}

/**
 * The override that stores custom settings: the whole spec, so the agent's
 * settings never depend on what any shared configuration says. Setting the
 * mode tells the resolver to take the named services as given, and the
 * explicit empty fallback policy stops the organization default's fallbacks
 * from being inherited (they could target the account chosen as primary, or
 * be rejected in Dograh mode).
 */
export function customOverride(spec: ConfigurationSpec): ModelConfigurationOverride {
    const override: Record<string, unknown> = { mode: spec.mode };
    for (const role of activeRoles(spec.mode)) {
        const selection = spec[role];
        if (role === "embeddings") { override.embeddings = selection ? { provider_connection_uuid: selection.provider_connection_uuid, settings: { ...selection.settings } } : null; continue; }
        if (selection) override[role] = { provider_connection_uuid: selection.provider_connection_uuid, settings: { ...selection.settings } };
    }
    override.llm_fallback = spec.llm_fallback?.rules.length ? spec.llm_fallback : { rules: [] };
    return override as ModelConfigurationOverride;
}

/** The override that follows a configuration; empty means the organization default. */
export function followOverride(uuid: string | null): ModelConfigurationOverride {
    return uuid ? { model_configuration_uuid: uuid } : {};
}

/** Workflow configurations with the override replaced and retired inline keys dropped. */
export function withOverride(workflowConfigurations: WorkflowConfigurations, override: ModelConfigurationOverride): WorkflowConfigurations {
    const next: WorkflowConfigurations = { ...workflowConfigurations, model_configuration_override: override };
    delete next.model_overrides;
    delete next.model_configuration_v2_override;
    return next;
}

/** Chip-sized summary: the brain model and the voice the agent speaks with. */
export function summarizeConfiguration(spec: ConfigurationSpec | null, connections: ProviderConnection[]): string {
    if (!spec) return "Not configured";
    const name = (uuid?: string) => connections.find(item => item.uuid === uuid)?.name;
    const model = (role: "llm" | "realtime") => String(spec[role]?.settings?.model || name(spec[role]?.provider_connection_uuid) || "provider default");
    const voice = (role: "tts" | "realtime") => String(spec[role]?.settings?.voice || "default voice");
    if (spec.mode === "realtime") return `${model("realtime")} / ${voice("realtime")}`;
    if (configurationMode(spec, connections) === "dograh") return `Dograh / ${voice("tts")}`;
    return `${model("llm")} / ${voice("tts")}`;
}
