import { describe, expect, it } from "vitest";

import { buildHttpToolTestSnapshot, generateSampleValue, type HttpToolTestSnapshotFields } from "./testPanelHelpers";

describe("generateSampleValue", () => {
    it("returns a sample string for type 'string'", () => {
        expect(generateSampleValue("string")).toBe("sample_text");
    });

    it("returns a sample number string for type 'number'", () => {
        expect(generateSampleValue("number")).toBe("5");
    });

    it("returns 'true' for type 'boolean'", () => {
        expect(generateSampleValue("boolean")).toBe("true");
    });

    it("returns an empty array literal for type 'array'", () => {
        expect(generateSampleValue("array")).toBe("[]");
    });

    it("returns an empty object literal for type 'object'", () => {
        expect(generateSampleValue("object")).toBe("{}");
    });
});

describe("buildHttpToolTestSnapshot", () => {
    const base: HttpToolTestSnapshotFields = {
        httpMethod: "GET",
        url: "https://api.example.com",
        credentialUuid: "",
        headers: [],
        parameters: [],
        presetParameters: [],
        timeoutMs: 5000,
    };

    it("produces identical output for identical fields", () => {
        expect(buildHttpToolTestSnapshot(base)).toBe(buildHttpToolTestSnapshot({ ...base }));
    });

    it("produces different output when the url changes", () => {
        const changed = { ...base, url: "https://api.example.com/v2" };
        expect(buildHttpToolTestSnapshot(base)).not.toBe(buildHttpToolTestSnapshot(changed));
    });

    it("produces different output when a parameter is added", () => {
        const changed = { ...base, parameters: [{ name: "q", type: "string" as const, description: "", required: true }] };
        expect(buildHttpToolTestSnapshot(base)).not.toBe(buildHttpToolTestSnapshot(changed));
    });
});
