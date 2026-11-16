import { describe, expect, it } from "vitest";

import { getCaretCoordinates } from "./caretPosition";

describe("getCaretCoordinates", () => {
    it("returns default values in non-browser or null element environment", () => {
        // @ts-expect-error testing null element
        const coords = getCaretCoordinates(null, 0);
        expect(coords).toEqual({ top: 0, left: 0, lineHeight: 20 });
    });

    it("calculates coordinates in DOM environment without throwing", () => {
        const textarea = document.createElement("textarea");
        textarea.value = "Hello {% world\nSecond line";
        document.body.appendChild(textarea);

        const coords = getCaretCoordinates(textarea, 8);
        expect(coords).toHaveProperty("top");
        expect(coords).toHaveProperty("left");
        expect(coords).toHaveProperty("lineHeight");
        expect(typeof coords.top).toBe("number");
        expect(typeof coords.left).toBe("number");
        expect(typeof coords.lineHeight).toBe("number");

        document.body.removeChild(textarea);
    });
});
