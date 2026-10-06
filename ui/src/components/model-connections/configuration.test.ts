import { describe, expect, it } from "vitest";

import { cleanConfiguration, cleanSettings, fieldForModel, selectionForConnection } from "./configuration";
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

    it("removes both numeric maximum declarations for custom endpoints", () => {
        const result = fieldForModel({ anyOf: [{ type: "number", maximum: 2 }, { type: "null" }], custom_endpoint: { field: "base_url", default_hostname: "api.openai.com", maximum: 2, description: "Custom range" } }, { base_url: "https://models.example.com/v1" });
        expect(result.maximum).toBeUndefined();
        expect(result.anyOf?.[0].maximum).toBeUndefined();
    });
});
