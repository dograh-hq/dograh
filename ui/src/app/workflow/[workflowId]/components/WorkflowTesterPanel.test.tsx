import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { createRunMock, markActionCompletedMock } = vi.hoisted(() => ({
    createRunMock: vi.fn(),
    markActionCompletedMock: vi.fn(),
}));

vi.mock("@/client/sdk.gen", () => ({
    createWorkflowRunApiV1WorkflowWorkflowIdRunsPost: createRunMock,
}));

vi.mock("@/lib/auth", () => ({
    useAuth: () => ({
        isAuthenticated: true,
        loading: false,
        getAccessToken: async () => "token",
    }),
}));

vi.mock("@/context/OnboardingContext", () => ({
    useOnboarding: () => ({ markActionCompleted: markActionCompletedMock }),
}));

vi.mock("posthog-js", () => ({ default: { capture: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

vi.mock("@/components/onboarding/OnboardingTooltip", () => ({
    OnboardingTooltip: () => null,
}));

// Stand-in for the real tester: it only needs to show which run it was handed.
vi.mock("./workflow-tester/EmbeddedVoiceTester", () => ({
    EmbeddedVoiceTester: ({ workflowRunId }: { workflowRunId: number }) => (
        <div data-testid="voice-tester">run:{workflowRunId}</div>
    ),
}));

vi.mock("./workflow-tester/ManualTextChatPanel", () => ({
    ManualTextChatPanel: () => <div data-testid="chat-panel" />,
}));

vi.mock("./workflow-tester/AiSimulatorPlaceholder", () => ({
    AiSimulatorPlaceholder: () => null,
}));

import { WorkflowTesterPanel } from "./WorkflowTesterPanel";

function renderPanel() {
    return render(
        <WorkflowTesterPanel
            workflowId={10124}
            disabled={false}
            disabledReason={null}
        />,
    );
}

async function startVoiceRun() {
    const runTest = await screen.findByRole("button", { name: /run test/i });
    fireEvent.click(runTest);
    const tester = await screen.findByTestId("voice-tester");
    return tester.textContent;
}

// Radix activates a tab on mousedown, not click.
const switchTo = (name: RegExp) =>
    fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0 });

describe("WorkflowTesterPanel voice run lifecycle", () => {
    beforeEach(() => {
        let nextRunId = 630140;
        createRunMock.mockReset();
        createRunMock.mockImplementation(async () => ({
            data: { id: nextRunId++ },
            error: undefined,
        }));
    });

    it("hands the tester the run it just created", async () => {
        renderPanel();

        expect(await startVoiceRun()).toBe("run:630140");
    });

    it("releases the run id when the audio tab is left", async () => {
        // Leaving the tab unmounts the tester, which ends the call and completes
        // the run server-side. Re-offering a completed run is refused, so the
        // panel must come back to the empty state rather than reuse the id.
        renderPanel();
        await startVoiceRun();

        switchTo(/test chat/i);
        await waitFor(() => expect(screen.getByTestId("chat-panel")).toBeTruthy());

        switchTo(/test audio/i);

        await waitFor(() =>
            expect(screen.queryByTestId("voice-tester")).toBeNull(),
        );
        expect(screen.getByRole("button", { name: /run test/i })).toBeTruthy();
    });

    it("mints a fresh run on the next test rather than reusing the old one", async () => {
        renderPanel();
        await startVoiceRun();

        switchTo(/test chat/i);
        switchTo(/test audio/i);
        await waitFor(() =>
            expect(screen.queryByTestId("voice-tester")).toBeNull(),
        );

        expect(await startVoiceRun()).toBe("run:630141");
        expect(createRunMock).toHaveBeenCalledTimes(2);
    });
});
