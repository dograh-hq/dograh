import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmbeddedVoiceTester } from "./EmbeddedVoiceTester";

const { useWebSocketRTCMock } = vi.hoisted(() => ({
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

type BackendStatus = "reachable" | "unreachable";

// Base shape of everything EmbeddedVoiceTester destructures off the hook.
// start/refreshAppConfig are passed in per-test so each test can use its own
// mock instances (needed to genuinely exercise identity-dependent effect
// re-runs rather than relying on React's dependency-array bailout).
function baseHookReturn(opts: {
    start: () => void;
    refreshAppConfig: () => void;
    appConfigLoading: boolean;
    backendStatus?: BackendStatus;
    isStarting?: boolean;
    connectionStatus?: string;
    isCompleted?: boolean;
}) {
    return {
        audioRef: { current: null },
        connectionActive: false,
        permissionError: null,
        isCompleted: opts.isCompleted ?? false,
        apiKeyModalOpen: false,
        setApiKeyModalOpen: vi.fn(),
        apiKeyError: null,
        apiKeyErrorCode: null,
        workflowConfigError: null,
        workflowConfigModalOpen: false,
        setWorkflowConfigModalOpen: vi.fn(),
        connectionStatus: opts.connectionStatus ?? "idle",
        start: opts.start,
        stop: vi.fn(),
        isStarting: opts.isStarting ?? false,
        feedbackMessages: [],
        appConfig: opts.backendStatus ? { backendStatus: opts.backendStatus } : null,
        appConfigLoading: opts.appConfigLoading,
        refreshAppConfig: opts.refreshAppConfig,
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
        const start = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig: vi.fn(), appConfigLoading: true })
        );

        render(<EmbeddedVoiceTester {...props} />);

        expect(start).not.toHaveBeenCalled();
    });

    it("calls start() once appConfig finishes loading with a reachable backend", () => {
        const start = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig: vi.fn(), appConfigLoading: true })
        );

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);
        expect(start).not.toHaveBeenCalled();

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig: vi.fn(),
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        expect(start).toHaveBeenCalledTimes(1);
    });

    it("calls start() immediately when appConfig was already loaded and reachable on mount", () => {
        const start = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig: vi.fn(),
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );

        render(<EmbeddedVoiceTester {...props} />);

        expect(start).toHaveBeenCalledTimes(1);
    });

    it("never starts, and never calls start with a stale config, once loading finishes with an unreachable backend even after retrying", () => {
        // Regression test for the exact failure mode a review bot flagged:
        // /api/config/version can resolve (loading -> false) with HTTP 200
        // while backendStatus is 'unreachable' (the server-side healthcheck
        // it performs failed/timed out) — forceTurnRelay silently defaults to
        // false in that response. Starting on "loading finished" alone would
        // create a connection missing the relay-only restriction whenever
        // this happens on a deployment that actually needs it.
        const start = vi.fn();
        const refreshAppConfig = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig, appConfigLoading: true })
        );

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "unreachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        // One retry attempted, not started.
        expect(refreshAppConfig).toHaveBeenCalledTimes(1);
        expect(start).not.toHaveBeenCalled();

        // Backend still unreachable after the retry resolves — must not
        // retry again or start; a genuinely down backend must not spin
        // forever, and must never fall through to starting unprotected.
        rerender(<EmbeddedVoiceTester {...props} />);
        expect(refreshAppConfig).toHaveBeenCalledTimes(1);
        expect(start).not.toHaveBeenCalled();
    });

    it("starts once the retried config comes back reachable", () => {
        const start = vi.fn();
        const refreshAppConfig = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig, appConfigLoading: true })
        );

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "unreachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);
        expect(start).not.toHaveBeenCalled();

        // The retry succeeds this time.
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        expect(refreshAppConfig).toHaveBeenCalledTimes(1);
        expect(start).toHaveBeenCalledTimes(1);
    });

    it("never calls start a second time even when the effect re-runs on an unrelated dependency change", () => {
        // The guard under test is autoStartedRef, not React's dependency-array
        // bailout — using a *new* start identity on the extra render forces
        // the effect to actually re-execute (the real hook recreates start on
        // every render), so this only passes if the ref guard itself, not
        // React skipping an unchanged effect, is what prevents a second call.
        const firstStart = vi.fn();
        const secondStart = vi.fn();
        const refreshAppConfig = vi.fn();

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start: firstStart,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );
        const { rerender } = render(<EmbeddedVoiceTester {...props} />);
        expect(firstStart).toHaveBeenCalledTimes(1);

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start: secondStart,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        expect(firstStart).toHaveBeenCalledTimes(1);
        expect(secondStart).not.toHaveBeenCalled();
    });

    it("offers a working manual retry instead of staying permanently stuck once unreachable", () => {
        // Regression test for a review bot's reproduction: after the one
        // bounded auto-retry, the tester must not be left with no way to
        // ever start (a dead "Starting Test..." spinner with no functional
        // click handler) if the backend is still unreachable.
        const start = vi.fn();
        const refreshAppConfig = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig, appConfigLoading: true })
        );

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);

        // isStarting: true (with connectionStatus staying non-"failed") makes
        // the disabled-state check below meaningful: the component's disabled
        // expression is `isStarting && connectionStatus !== "failed" &&
        // !configUnreachable`, and with isStarting false (as every other test
        // in this file uses) that whole expression is already false
        // regardless of configUnreachable — the assertion would pass even if
        // configUnreachable did nothing at all. Only with isStarting true does
        // "not disabled" actually prove configUnreachable is short-circuiting
        // it, as a review bot correctly pointed out.
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "unreachable",
                isStarting: true,
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);
        expect(refreshAppConfig).toHaveBeenCalledTimes(1);

        // configRetriedRef flips inside the effect, which commits *after*
        // this render's own render-phase read of it — one more render (with
        // unchanged props/mocks) is needed to observe it, mirroring the real
        // app: refreshAppConfig there is the actual context function, whose
        // own state updates naturally trigger exactly this kind of follow-up
        // render on their own.
        rerender(<EmbeddedVoiceTester {...props} />);

        // Still just the one automatic retry; the button must now be a real,
        // enabled retry action — meaningful specifically because isStarting
        // is true here (see comment above).
        expect(refreshAppConfig).toHaveBeenCalledTimes(1);
        const retryButton = screen.getByRole("button", { name: /retry connection/i }) as HTMLButtonElement;
        expect(retryButton.disabled).toBe(false);

        fireEvent.click(retryButton);
        expect(refreshAppConfig).toHaveBeenCalledTimes(2);
        expect(start).not.toHaveBeenCalled();

        // The manual retry succeeds — this must lead to a real start(), not
        // just another no-op retry loop.
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "reachable",
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        expect(start).toHaveBeenCalledTimes(1);
    });

    it("issues exactly one extra fetch per manual retry click, not two, when it also comes back unreachable", () => {
        // Regression test for a review bot's own reproduction: the manual
        // retry handler used to reset configRetriedRef to false before
        // calling refreshAppConfig(). That made the auto-start effect think
        // no retry had happened yet, so when the manual refresh resolved
        // still unreachable, the effect fired its *own* extra automatic
        // refresh on top of the manual one — one click producing two fetches
        // (three total including the very first automatic retry) instead of
        // one (two total).
        const start = vi.fn();
        const refreshAppConfig = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({ start, refreshAppConfig, appConfigLoading: true })
        );

        const { rerender } = render(<EmbeddedVoiceTester {...props} />);

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "unreachable",
                isStarting: true,
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />); // effect fires the first automatic retry
        rerender(<EmbeddedVoiceTester {...props} />); // configRetriedRef's flip becomes observable
        expect(refreshAppConfig).toHaveBeenCalledTimes(1);

        const retryButton = screen.getByRole("button", { name: /retry connection/i });
        fireEvent.click(retryButton);
        expect(refreshAppConfig).toHaveBeenCalledTimes(2);

        // Simulate the manual refresh's async work actually resolving — the
        // mock doesn't auto-update the way the real refreshAppConfig
        // (AppConfigContext.loadConfig) would, so this has to be done
        // explicitly: loading flips true then false again, still
        // unreachable. Without an actual appConfigLoading change here the
        // effect never re-runs at all and this test would pass unconditionally
        // regardless of whether the bug exists (confirmed by running this
        // test against the buggy handleConfigRetry — it must fail there).
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: true,
                backendStatus: "unreachable",
                isStarting: true,
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);

        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig,
                appConfigLoading: false,
                backendStatus: "unreachable",
                isStarting: true,
            })
        );
        rerender(<EmbeddedVoiceTester {...props} />);
        // One more render for configRetriedRef's own flip (if any) to become
        // observable, same as earlier in this file.
        rerender(<EmbeddedVoiceTester {...props} />);

        // Exactly the one manual fetch — no extra automatic retry fired on
        // top of it.
        expect(refreshAppConfig).toHaveBeenCalledTimes(2);
        expect(start).not.toHaveBeenCalled();
    });
});

describe("EmbeddedVoiceTester spent run", () => {
    // A run the backend refuses as already completed is surfaced by the hook as
    // a completed call, not a failed one, so the footer must lead to a new run
    // rather than a retry that would be refused again.
    it("offers a new test and releases the run instead of retrying", () => {
        const onReset = vi.fn();
        const start = vi.fn();
        useWebSocketRTCMock.mockReturnValue(
            baseHookReturn({
                start,
                refreshAppConfig: vi.fn(),
                appConfigLoading: false,
                backendStatus: "reachable",
                isCompleted: true,
            })
        );

        render(
            <EmbeddedVoiceTester
                workflowId={1}
                workflowRunId={630140}
                accessToken="token"
                onReset={onReset}
            />
        );

        expect(screen.queryByRole("button", { name: /retry call/i })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: /start another test/i }));

        expect(onReset).toHaveBeenCalledTimes(1);
        expect(start).toHaveBeenCalledTimes(1); // the initial auto-start only
    });
});
