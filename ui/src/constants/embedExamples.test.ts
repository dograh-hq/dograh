import { afterEach, describe, expect, it, vi } from "vitest";

import { HEADLESS_CHAT_EXAMPLE } from "./embedExamples";

type HeadlessWidget = {
    onChatStateChange: (callback: (state: string) => void) => void;
    onMessage: (callback: (text: string, turn: unknown) => void) => void;
    startChat: () => void;
    sendMessage: (text: string) => Promise<unknown[]>;
};

type WidgetWindow = Window & { DograhWidget?: HeadlessWidget };

describe("headless chat embed example", () => {
    afterEach(() => {
        delete (window as WidgetWindow).DograhWidget;
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
        document.body.innerHTML = "";
    });

    it("waits for the async widget script before registering callbacks", () => {
        document.body.innerHTML = `
            <script id="dograh-widget"></script>
            <button id="open-chat"></button>
            <input id="chat-input" />
            <button id="send-btn"></button>
        `;
        vi.stubGlobal("appendAgentBubble", vi.fn());
        vi.stubGlobal("appendVisitorBubble", vi.fn());

        const onChatStateChange = vi.fn();
        const onMessage = vi.fn();
        const widget: HeadlessWidget = {
            onChatStateChange,
            onMessage,
            startChat: vi.fn(),
            sendMessage: vi.fn(async () => []),
        };

        window.eval(HEADLESS_CHAT_EXAMPLE);

        expect(onChatStateChange).not.toHaveBeenCalled();
        expect(onMessage).not.toHaveBeenCalled();

        (window as WidgetWindow).DograhWidget = widget;
        document.getElementById("dograh-widget")?.dispatchEvent(new Event("load"));

        expect(onChatStateChange).toHaveBeenCalledOnce();
        expect(onMessage).toHaveBeenCalledOnce();
    });
});
