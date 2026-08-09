import { describe, expect, it } from "vitest";

import {
    extractUrlHostnameParameters,
    extractUrlPathParameters,
    validateUrl,
} from "./url-input";

describe("validateUrl", () => {
    it("accepts template parameters in the URL path", () => {
        expect(validateUrl("https://test.com/v1/{{initial_context.path}}")).toEqual({
            valid: true,
        });
    });

    it("accepts template parameters in the hostname", () => {
        expect(validateUrl("https://{{initial_context.path}}/test/v1")).toEqual({
            valid: true,
        });
    });

    it("rejects malformed template parameters", () => {
        expect(validateUrl("https://{{initial_context.path}/test/v1")).toEqual({
            valid: false,
            error: "Invalid URL template format",
        });
    });

    it("retains the protocol-specific error for URLs without an HTTP protocol", () => {
        expect(validateUrl("test.com/v1")).toEqual({
            valid: false,
            error: "Invalid URL format. Must start with http:// or https://",
        });
    });
});

describe("extractUrlHostnameParameters", () => {
    it("detects initial-context parameters in the hostname", () => {
        expect(
            extractUrlHostnameParameters("https://{{initial_context.host}}/test/v1")
        ).toEqual(["initial_context.host"]);
    });

    it("supports parameters embedded in a hostname", () => {
        expect(extractUrlHostnameParameters("https://{{tenant}}.example.com/v1")).toEqual([
            "tenant",
        ]);
    });
});

describe("extractUrlPathParameters", () => {
    it("detects initial-context parameters in the path", () => {
        expect(
            extractUrlPathParameters("https://test.com/v1/{{initial_context.path}}")
        ).toEqual(["initial_context.path"]);
    });

    it("deduplicates parameters and strips fallback filters", () => {
        expect(
            extractUrlPathParameters(
                "https://test.com/{{user_id}}/orders/{{user_id | anonymous}}"
            )
        ).toEqual(["user_id"]);
    });

    it("does not treat hostname or query templates as path parameters", () => {
        expect(
            extractUrlPathParameters(
                "https://{{initial_context.host}}/v1?filter={{initial_context.filter}}"
            )
        ).toEqual([]);
    });
});
