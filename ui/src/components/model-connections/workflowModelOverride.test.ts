import { describe, expect, it } from "vitest";

import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import type { ConfigurationSpec, NamedModelConfiguration, ProviderConnection } from "./types";
import { applyModelPatch, customOverride, describeWorkflowModel, followOverride, isComplete, specFromPatch, splitOverride, summarizeConfiguration, withOverride } from "./workflowModelOverride";

const connection = (uuid: string, name: string, provider: string): ProviderConnection =>
    ({ uuid, name, provider, connection_settings: {}, configured_credentials: ["api_key"], is_active: true, revision: 1 });
const connections = [connection("openai-a", "OpenAI prod", "openai"), connection("openai-b", "OpenAI backup", "openai"), connection("eleven", "ElevenLabs", "elevenlabs"), connection("dograh", "Dograh", "dograh")];
const cascade: ConfigurationSpec = {
    version: 3, mode: "pipeline",
    llm: { provider_connection_uuid: "openai-a", settings: { model: "gpt-4.1", temperature: 0.2 } },
    stt: { provider_connection_uuid: "openai-a", settings: { model: "transcribe" } },
    tts: { provider_connection_uuid: "openai-a", settings: { voice: "Sophie" } },
    embeddings: { provider_connection_uuid: "openai-a", settings: { model: "embed-3" } },
};
const named = (uuid: string, name: string, extra: Partial<NamedModelConfiguration> = {}): NamedModelConfiguration =>
    ({ uuid, name, is_active: true, revision: 1, configuration: cascade, ...extra });
const configurations = [named("prod", "Production voice"), named("cheap", "Fast and cheap"), named("gone", "Archived", { is_active: false })];
const context = { configurations, defaultUuid: "prod", connections };

describe("describeWorkflowModel", () => {
    it("follows the organization default or a chosen configuration", () => {
        expect(describeWorkflowModel(resolveWorkflowConfigurations(), context)).toMatchObject({ kind: "existing", isDefault: true, base: { uuid: "prod" } });
        expect(describeWorkflowModel(resolveWorkflowConfigurations({ model_configuration_override: {} }), context)).toMatchObject({ kind: "existing", isDefault: true });
        expect(describeWorkflowModel(resolveWorkflowConfigurations({ model_configuration_override: { model_configuration_uuid: "cheap" } }), context)).toMatchObject({ kind: "existing", isDefault: false, base: { uuid: "cheap" } });
    });

    it("treats any patch as custom settings, layered over the base when there is one", () => {
        const patched = describeWorkflowModel(resolveWorkflowConfigurations({ model_configuration_override: { model_configuration_uuid: "cheap", tts: { settings: { voice: "Rachel" } } } }), context);
        expect(patched.kind).toBe("custom");
        if (patched.kind === "custom") {
            expect(patched.spec.tts).toEqual({ provider_connection_uuid: "openai-a", settings: { voice: "Rachel" } });
            expect(patched.spec.llm).toEqual(cascade.llm);
        }
        const standalone = describeWorkflowModel(resolveWorkflowConfigurations({ model_configuration_override: customOverride(cascade) }), { ...context, defaultUuid: null });
        expect(standalone.kind).toBe("custom");
        if (standalone.kind === "custom") expect(standalone.spec).toEqual(cascade);
    });

    it("reports unavailable, missing and legacy bindings", () => {
        expect(describeWorkflowModel(resolveWorkflowConfigurations({ model_configuration_override: { model_configuration_uuid: "gone" } }), context)).toEqual({ kind: "unavailable", uuid: "gone" });
        expect(describeWorkflowModel(resolveWorkflowConfigurations(), { ...context, defaultUuid: null })).toEqual({ kind: "none" });
        expect(describeWorkflowModel(resolveWorkflowConfigurations({ model_overrides: { llm: { temperature: 0.4 } } }), context)).toEqual({ kind: "legacy" });
        // A present override, even empty, supersedes retired inline keys.
        expect(describeWorkflowModel(resolveWorkflowConfigurations({ model_overrides: { llm: { temperature: 0.4 } }, model_configuration_override: {} }), context)).toMatchObject({ kind: "existing", isDefault: true });
    });
});

describe("applyModelPatch", () => {
    it("merges settings for the same account", () => {
        const result = applyModelPatch(cascade, { llm: { settings: { model: "gpt-4.1-mini" } } }, connections);
        expect(result.llm).toEqual({ provider_connection_uuid: "openai-a", settings: { model: "gpt-4.1-mini", temperature: 0.2 } });
        expect(cascade.llm.settings.model).toBe("gpt-4.1");
    });

    it("keeps settings across accounts of one provider and resets them across providers", () => {
        expect(applyModelPatch(cascade, { tts: { provider_connection_uuid: "openai-b" } }, connections).tts).toEqual({ provider_connection_uuid: "openai-b", settings: { voice: "Sophie" } });
        expect(applyModelPatch(cascade, { llm: { provider_connection_uuid: "dograh" } }, connections).llm).toEqual({ provider_connection_uuid: "dograh", settings: {} });
        expect(applyModelPatch(cascade, { tts: { provider_connection_uuid: "eleven", settings: { voice: "Rachel" } } }, connections).tts).toEqual({ provider_connection_uuid: "eleven", settings: { voice: "Rachel" } });
    });

    it("switches mode, adds services, and removes embeddings", () => {
        const result = applyModelPatch(cascade, { mode: "realtime", realtime: { provider_connection_uuid: "openai-a", settings: { model: "gpt-realtime" } }, embeddings: null }, connections);
        expect(result.mode).toBe("realtime");
        expect(result.realtime).toEqual({ provider_connection_uuid: "openai-a", settings: { model: "gpt-realtime" } });
        expect(result.embeddings).toBeNull();
        expect(result).not.toHaveProperty("stt");
        expect(result).not.toHaveProperty("tts");
    });

    it("takes the services a mode-setting patch names as given, like the server", () => {
        const patch = { mode: "pipeline" as const, llm: { provider_connection_uuid: "openai-a", settings: { model: "gpt-4.1-mini" } }, tts: { settings: { voice: "Rachel" } } };
        const result = applyModelPatch(cascade, patch, connections);
        expect(result.llm).toEqual({ provider_connection_uuid: "openai-a", settings: { model: "gpt-4.1-mini" } });
        expect(result.tts).toEqual({ provider_connection_uuid: "openai-a", settings: { voice: "Rachel" } });
        expect(result.stt).toEqual(cascade.stt);
    });
});

describe("custom overrides", () => {
    it("stores the whole spec for the active mode, disables inherited fallbacks, and reads back", () => {
        const override = customOverride(cascade);
        expect(override).toEqual({ mode: "pipeline", llm: cascade.llm, stt: cascade.stt, tts: cascade.tts, embeddings: cascade.embeddings, llm_fallback: { rules: [] } });
        expect(specFromPatch(splitOverride(override).patch)).toEqual(cascade);
        const realtime: ConfigurationSpec = { ...cascade, mode: "realtime", realtime: { provider_connection_uuid: "openai-a", settings: { model: "gpt-realtime" } }, embeddings: null };
        expect(customOverride(realtime)).toEqual({ mode: "realtime", llm: cascade.llm, realtime: realtime.realtime, embeddings: null, llm_fallback: { rules: [] } });
        expect(customOverride(realtime)).not.toHaveProperty("stt");
        const withPolicy = { ...cascade, llm_fallback: { version: 1 as const, rules: [{ condition: { type: "error" as const }, target: { provider_connection_uuid: "openai-b", settings: {} } }] } };
        expect(customOverride(withPolicy).llm_fallback).toEqual(withPolicy.llm_fallback);
        expect(specFromPatch(splitOverride(customOverride(withPolicy)).patch).llm_fallback).toEqual(withPolicy.llm_fallback);
    });

    it("knows when every service has an account", () => {
        expect(isComplete(cascade)).toBe(true);
        expect(isComplete({ ...cascade, embeddings: null })).toBe(true);
        expect(isComplete({ ...cascade, tts: { provider_connection_uuid: "", settings: {} } })).toBe(false);
        expect(isComplete(specFromPatch({ mode: "realtime", llm: { provider_connection_uuid: "openai-a" } }))).toBe(false);
    });

    it("builds follow overrides and applies them without touching other settings", () => {
        expect(followOverride(null)).toEqual({});
        expect(followOverride("cheap")).toEqual({ model_configuration_uuid: "cheap" });
        const base = resolveWorkflowConfigurations({ dictionary: "net metering", model_overrides: { llm: { temperature: 0.4 } }, model_configuration_v2_override: { llm: {} } });
        const next = withOverride(base, followOverride("cheap"));
        expect(next.model_configuration_override).toEqual({ model_configuration_uuid: "cheap" });
        expect(next.dictionary).toBe("net metering");
        expect(next).not.toHaveProperty("model_overrides");
        expect(next).not.toHaveProperty("model_configuration_v2_override");
        expect(base).toHaveProperty("model_overrides");
    });

    it("summarizes brain model and voice per mode", () => {
        expect(summarizeConfiguration(cascade, connections)).toBe("gpt-4.1 / Sophie");
        expect(summarizeConfiguration({ ...cascade, mode: "realtime", realtime: { provider_connection_uuid: "openai-a", settings: { model: "gpt-realtime", voice: "marin" } } }, connections)).toBe("gpt-realtime / marin");
        const managed: ConfigurationSpec = { version: 3, mode: "pipeline", llm: { provider_connection_uuid: "dograh", settings: {} }, stt: { provider_connection_uuid: "dograh", settings: {} }, tts: { provider_connection_uuid: "dograh", settings: { voice: "af_heart" } } };
        expect(summarizeConfiguration(managed, connections)).toBe("Dograh / af_heart");
        expect(summarizeConfiguration({ ...cascade, llm: { provider_connection_uuid: "openai-a", settings: {} }, tts: { provider_connection_uuid: "openai-a", settings: {} } }, connections)).toBe("OpenAI prod / default voice");
        expect(summarizeConfiguration(null, connections)).toBe("Not configured");
    });
});
