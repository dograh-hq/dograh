import { describe, expect, it } from "vitest";

import { generateSampleValue } from "./page";

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
