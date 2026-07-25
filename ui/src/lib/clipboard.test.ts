import { afterEach, describe, expect, it, vi } from "vitest";

import { copyTextToClipboard } from "./clipboard";

const originalExecCommand = document.execCommand;

describe("copyTextToClipboard", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();

        Object.defineProperty(document, "execCommand", {
            configurable: true,
            value: originalExecCommand,
        });
        document.querySelectorAll("textarea").forEach((element) => element.remove());
    });

    it("uses the Clipboard API when it is available", async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        const execCommand = vi.fn();
        vi.stubGlobal("navigator", { clipboard: { writeText } });
        Object.defineProperty(document, "execCommand", {
            configurable: true,
            value: execCommand,
        });

        await copyTextToClipboard("secret");

        expect(writeText).toHaveBeenCalledWith("secret");
        expect(execCommand).not.toHaveBeenCalled();
    });

    it("uses the DOM fallback when the Clipboard API is unavailable", async () => {
        const execCommand = vi.fn().mockReturnValue(true);
        vi.stubGlobal("navigator", {});
        Object.defineProperty(document, "execCommand", {
            configurable: true,
            value: execCommand,
        });

        await copyTextToClipboard("secret");

        expect(execCommand).toHaveBeenCalledWith("copy");
        expect(document.querySelector("textarea")).toBeNull();
    });

    it("uses the DOM fallback when Clipboard API access is denied", async () => {
        const writeText = vi.fn().mockRejectedValue(new Error("denied"));
        const execCommand = vi.fn().mockReturnValue(true);
        vi.stubGlobal("navigator", { clipboard: { writeText } });
        Object.defineProperty(document, "execCommand", {
            configurable: true,
            value: execCommand,
        });

        await copyTextToClipboard("secret");

        expect(writeText).toHaveBeenCalledWith("secret");
        expect(execCommand).toHaveBeenCalledWith("copy");
    });

    it("rejects and removes the temporary element when copying fails", async () => {
        const execCommand = vi.fn().mockReturnValue(false);
        vi.stubGlobal("navigator", {});
        Object.defineProperty(document, "execCommand", {
            configurable: true,
            value: execCommand,
        });

        await expect(copyTextToClipboard("secret")).rejects.toThrow(
            "Copy command was unsuccessful",
        );
        expect(document.querySelector("textarea")).toBeNull();
    });
});
