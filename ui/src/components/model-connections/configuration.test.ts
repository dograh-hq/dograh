import { describe, expect, it } from "vitest";

import { cleanConfiguration, cleanSettings, compatibleConnections, configurationForMode, configurationMode, fieldForModel, selectionForConnection } from "./configuration";
import type { ConfigurationSpec, ModelConnectionCatalog, ProviderConnection } from "./types";

const connection = (uuid: string, provider: string): ProviderConnection => ({ uuid, provider, name: uuid, connection_settings: {}, configured_credentials: ["api_key"], revision: 1, is_active: true });
const connections = [connection("openai-primary", "openai"), connection("openai-secondary", "openai"), connection("google", "google")];
const catalog: ModelConnectionCatalog = { services: { llm: {
    openai: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
        model: { default: "gpt-4.1" },
        temperature: { default: 0.1, model_constraints: [{ pattern: "^gpt-5", supported: false }] },
    } } },
    google: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: { model: { default: "gemini-3.5-flash" }, temperature: { default: 0.7 } } } },
} } };
const base: ConfigurationSpec = { version: 3, mode: "pipeline", llm: { provider_connection_uuid: "openai-primary", settings: { model: "gpt-4.1", temperature: 0.2 } }, stt: { provider_connection_uuid: "stt", settings: { model: "transcriber" } }, tts: { provider_connection_uuid: "tts", settings: { voice: "Alice" } }, embeddings: { provider_connection_uuid: "embedding", settings: {} } };

describe("model connection editor settings", () => {
    it.each(["openai", "google"])("reuses a %s account with role-specific model defaults", provider => {
        const account = connection("shared-account", provider);
        const realtimeModel = provider === "openai" ? "gpt-realtime-2" : "gemini-3.8-live";
        const shared: ModelConnectionCatalog = { services: {
            ...catalog.services,
            realtime: { [provider]: { ...catalog.services.llm![provider], settings_schema: { properties: {
                model: { default: realtimeModel },
            } } } },
        } };
        const cascade = configurationForMode("cascade", base, shared, [account]);
        const realtime = configurationForMode("realtime", cascade, shared, [account]);
        expect(realtime.llm).toEqual(cascade.llm);
        expect(realtime.realtime).toEqual({ provider_connection_uuid: account.uuid, settings: { model: realtimeModel } });
        expect(realtime.llm.settings.model).toBe(catalog.services.llm![provider].settings_schema.properties!.model.default);
        expect(cleanConfiguration(realtime, shared, [account]).realtime).toEqual(realtime.realtime);
    });

    it("preserves mixed managed and external providers on a rename-only save", () => {
        const available = [...connections, connection("managed", "dograh")];
        const mixed: ConfigurationSpec = { ...base, llm: { provider_connection_uuid: "managed", settings: {} }, embeddings: null };
        expect(configurationMode(mixed, available)).toBe("cascade");
        const cleaned = cleanConfiguration(mixed, catalog, available);
        expect(cleaned.llm.provider_connection_uuid).toBe("managed");
        expect(cleaned.stt?.provider_connection_uuid).toBe(base.stt?.provider_connection_uuid);
        expect(cleaned.tts?.provider_connection_uuid).toBe(base.tts?.provider_connection_uuid);
        expect(cleaned.embeddings).toBeNull();
    });

    it("filters connections by the selected service's required credentials", () => {
        const google = { ...connections[2], configured_credentials: [] };
        const required = { services: { llm: { google: { ...catalog.services.llm!.google, credential_required: ["api_key"] } } } };
        expect(compatibleConnections(required, [google], "llm")).toEqual([]);
        expect(compatibleConnections(required, [{ ...google, configured_credentials: ["api_key"] }], "llm")).toHaveLength(1);
    });

    it("skips invalid model constraint patterns", () => {
        expect(fieldForModel({ model_constraints: [{ pattern: "[", maximum: 1 }, { pattern: "^gpt", maximum: 2 }] }, { model: "gpt-4.1" }).maximum).toBe(2);
    });
    it("preserves parameters for a connection change within the same provider", () => {
        expect(selectionForConnection(catalog, "llm", connections[1], base.llm, connections)).toEqual({ provider_connection_uuid: "openai-secondary", settings: base.llm.settings });
    });

    it("starts a different provider with its own defaults", () => {
        expect(selectionForConnection(catalog, "llm", connections[2], base.llm, connections)).toEqual({ provider_connection_uuid: "google", settings: { model: "gemini-3.5-flash", temperature: 0.7 } });
    });

    it("removes hidden, computed, and credential fields without dropping valid settings", () => {
        const schema = { properties: { ...catalog.services.llm!.openai.settings_schema.properties, output: { readOnly: true } } };
        expect(cleanSettings({ temperature: 0.8, output: "computed", api_key: "secret" }, schema, { model: "gpt-5-mini" })).toEqual({});
        expect(cleanSettings({ temperature: 0 }, schema, { model: "gpt-4.1" })).toEqual({ temperature: 0 });
    });

    it("does not submit inactive pipeline selections in realtime mode", () => {
        const result = cleanConfiguration({ ...base, mode: "realtime", realtime: { provider_connection_uuid: "realtime", settings: {} } }, catalog, connections);
        expect(result).not.toHaveProperty("stt");
        expect(result).not.toHaveProperty("tts");
        expect(result).toHaveProperty("llm");
    });

    it("preserves fallback rules between Cascade and Realtime and clears them for Dograh", () => {
        const available = [...connections, connection("dograh", "dograh")];
        const configured: ConfigurationSpec = { ...base, llm_fallback: { version: 1, rules: [{ condition: { type: "error" }, target: { provider_connection_uuid: "google", settings: {} } }] } };
        const realtime = configurationForMode("realtime", configured, catalog, available);
        expect(cleanConfiguration(realtime, catalog, available).llm_fallback).toEqual(configured.llm_fallback);
        const cascade = configurationForMode("cascade", realtime, catalog, available);
        expect(cleanConfiguration(cascade, catalog, available).llm_fallback).toEqual(configured.llm_fallback);
        const dograh = configurationForMode("dograh", cascade, catalog, available);
        expect(dograh).not.toHaveProperty("llm_fallback");
        expect(cleanConfiguration({ ...dograh, llm_fallback: configured.llm_fallback }, catalog, available)).not.toHaveProperty("llm_fallback");
        expect(configurationForMode("cascade", dograh, catalog, available).llm_fallback).toBeUndefined();
    });

    it("removes both numeric maximum declarations for custom endpoints", () => {
        const result = fieldForModel({ anyOf: [{ type: "number", maximum: 2 }, { type: "null" }], custom_endpoint: { field: "base_url", default_hostname: "api.openai.com", maximum: 2, description: "Custom range" } }, { base_url: "https://models.example.com/v1" });
        expect(result.maximum).toBeUndefined();
        expect(result.anyOf?.[0].maximum).toBeUndefined();
    });
});
