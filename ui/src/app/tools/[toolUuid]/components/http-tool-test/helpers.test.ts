import { describe, expect, it } from "vitest";

import {
    buildHttpToolTestSnapshot,
    generateSampleValue,
    type HttpToolTestSnapshotFields,
    isUnsafeHttpMethod,
    parseTestParameterValues,
} from "./helpers";

describe("generateSampleValue", () => {
    it.each([
        ["string", "sample_text"],
        ["number", "5"],
        ["boolean", "true"],
        ["array", "[]"],
        ["object", "{}"],
    ] as const)("returns %s sample input", (type, expected) => {
        expect(generateSampleValue(type)).toBe(expected);
    });
});

describe("isUnsafeHttpMethod", () => {
    it("treats GET as safe", () => {
        expect(isUnsafeHttpMethod("GET")).toBe(false);
    });

    it.each(["POST", "PUT", "PATCH", "DELETE"] as const)("treats %s as unsafe", (method) => {
        expect(isUnsafeHttpMethod(method)).toBe(true);
    });
});

describe("parseTestParameterValues", () => {
    it("converts input strings to configured parameter types", () => {
        expect(
            parseTestParameterValues(
                [
                    { name: "query", type: "string", required: true },
                    { name: "limit", type: "number", required: true },
                    { name: "enabled", type: "boolean", required: true },
                    { name: "filters", type: "object", required: true },
                    { name: "tags", type: "array", required: true },
                ],
                {
                    query: "cart",
                    limit: "5",
                    enabled: "false",
                    filters: '{"status":"open"}',
                    tags: '["new"]',
                }
            )
        ).toEqual({
            query: "cart",
            limit: 5,
            enabled: false,
            filters: { status: "open" },
            tags: ["new"],
        });
    });

    it("reports the parameter containing invalid JSON", () => {
        expect(() =>
            parseTestParameterValues(
                [{ name: "filters", type: "object", required: true }],
                { filters: "{" }
            )
        ).toThrow("filters: invalid JSON");
    });

    it("rejects missing required values", () => {
        expect(() =>
            parseTestParameterValues(
                [{ name: "customer_id", type: "string", required: true }],
                { customer_id: "" }
            )
        ).toThrow("customer_id: value is required");
    });
});

describe("buildHttpToolTestSnapshot", () => {
    const base: HttpToolTestSnapshotFields = {
        name: "Search API",
        description: "Search for records",
        httpMethod: "GET",
        url: "https://api.example.com",
        credentialUuid: "",
        headers: [],
        parameters: [],
        presetParameters: [],
        timeoutMs: 5000,
        customMessage: "",
        customMessageType: "text",
        customMessageRecordingId: "",
    };

    it("produces identical output for identical fields", () => {
        expect(buildHttpToolTestSnapshot(base)).toBe(buildHttpToolTestSnapshot({ ...base }));
    });

    it("changes when a saved HTTP field changes", () => {
        expect(buildHttpToolTestSnapshot(base)).not.toBe(
            buildHttpToolTestSnapshot({ ...base, url: "https://api.example.com/v2" })
        );
        expect(buildHttpToolTestSnapshot(base)).not.toBe(
            buildHttpToolTestSnapshot({
                ...base,
                parameters: [{ name: "q", type: "string", description: "", required: true }],
            })
        );
        expect(buildHttpToolTestSnapshot(base)).not.toBe(
            buildHttpToolTestSnapshot({ ...base, name: "Updated Search API" })
        );
        expect(buildHttpToolTestSnapshot(base)).not.toBe(
            buildHttpToolTestSnapshot({ ...base, description: "Updated description" })
        );
        expect(buildHttpToolTestSnapshot(base)).not.toBe(
            buildHttpToolTestSnapshot({ ...base, customMessage: "Done" })
        );
    });
});
