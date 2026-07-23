import { afterEach, describe, expect, it, vi } from "vitest";

import { createUuid } from "./uuid";

describe("createUuid", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("uses crypto.randomUUID when it is available", () => {
        const randomUUID = vi.fn(() => "27f3a42d-0eb3-43b1-a539-d4d09dd9065e");
        vi.stubGlobal("crypto", { randomUUID });

        expect(createUuid()).toBe("27f3a42d-0eb3-43b1-a539-d4d09dd9065e");
        expect(randomUUID).toHaveBeenCalledOnce();
    });

    it("creates a version 4 UUID when crypto.randomUUID is unavailable", () => {
        const getRandomValues = vi.fn((bytes: Uint8Array) => {
            bytes.fill(0);
            return bytes;
        });
        vi.stubGlobal("crypto", { getRandomValues });

        expect(createUuid()).toBe("00000000-0000-4000-8000-000000000000");
        expect(getRandomValues).toHaveBeenCalledOnce();
    });
});
