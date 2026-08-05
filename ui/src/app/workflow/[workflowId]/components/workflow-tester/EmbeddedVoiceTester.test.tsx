import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmbeddedVoiceTester } from "./EmbeddedVoiceTester";

const { startMock, useWebSocketRTCMock } = vi.hoisted(() => ({
    startMock: vi.fn(),
    useWebSocketRTCMock: vi.fn(),
}));

vi.mock("../../run/[runId]/hooks", () => ({
    useWebSocketRTC: useWebSocketRTCMock,
}));

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/components/workflow/conversation", () => ({
    RealtimeFeedback: () => null,
}));

vi.mock("../../run/[runId]/components", () => ({
    ApiKeyErrorDialog: () => null,
    ConnectionStatus: () => null,
    WorkflowConfigErrorDialog: () => null,
}));

// Base shape of everything EmbeddedVoiceTester destructures off the hook.
// Only `appConfigLoading` varies per test — this is the field the auto-start
// gate in EmbeddedVoiceTester depends on.
function baseHookReturn(appConfigLoading: boolean) {
    return {
        audioRef: { current: null },
        connectionActive: false,
        permissionError: null,
        isCompleted: false,
        apiKeyModalOpen: false,
        setApiKeyModalOpen: vi.fn(),
        apiKeyError: null,
        apiKeyErrorCode: null,
        workflowConfigError: null,
        workflowConfigModalOpen: false,
        setWorkflowConfigModalOpen: vi.fn(),
        connectionStatus: "idle",
        start: startMock,
        stop: vi.fn(),
        isStarting: false,
        feedbackMessages: [],
        appConfigLoading,
    };
}

describe("EmbeddedVoiceTester auto-start", () => {
    const props = {
        workflowId: 1,
        workflowRunId: 1,
        accessToken: "token",
        onReset: vi.fn(),
    };

    it("does not call start() while appConfig is still loading", () => {
        startMock.mockClear();
        useWebSocketRTCMock.mockReturnValue(baseHookReturn(true));

        render(<EmbeddedVoiceTester {...props} />);

        // createPeerConnection reads appConfig?.forceTurnRelay synchronously —
        // calling start() before appConfig has loaded would silently create a
        // connection missing the relay-only restriction, with no way to
        // recreate it once appConfig resolves (see PR description / commit
        // message for the full failure mode this reproduces).
        expect(startMock).not.toHaveBeenCalled();
    });

    it("calls start() exactly once, only after appConfig finishes loading", () => {
        startMock.mockClear();
        useWebSocketRTCMock.mockReturnValue(baseHookReturn(true));

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);
        expect(startMock).not.toHaveBeenCalled();

        // appConfig resolves — re-render with appConfigLoading now false, as
        // React would after the async /api/config/version fetch completes.
        useWebSocketRTCMock.mockReturnValue(baseHookReturn(false));
        rerender(<EmbeddedVoiceTester {...props} />);

        expect(startMock).toHaveBeenCalledTimes(1);

        // A further re-render (e.g. any other state change) must not
        // trigger a second start() — autoStartedRef still guards that.
        rerender(<EmbeddedVoiceTester {...props} />);
        expect(startMock).toHaveBeenCalledTimes(1);
    });

    it("calls start() immediately when appConfig was already loaded on mount", () => {
        startMock.mockClear();
        useWebSocketRTCMock.mockReturnValue(baseHookReturn(false));

        render(<EmbeddedVoiceTester {...props} />);

        expect(startMock).toHaveBeenCalledTimes(1);
    });
});
