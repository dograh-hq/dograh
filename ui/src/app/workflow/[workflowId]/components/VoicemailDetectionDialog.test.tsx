import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { VoicemailDetectionDialog } from "./VoicemailDetectionDialog";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: null, loading: false }) }));
vi.mock("@/components/LLMConfigSelector", () => ({ LLMConfigSelector: () => null }));

it("loads old settings into the new form and removes obsolete detector configuration on save", () => {
    const onSave = vi.fn();
    const oldConfig = {
        enabled: true, use_workflow_llm: false, provider: "openai", model: "gpt-4.1", api_key: "test-key",
        long_speech_timeout: 8, supervisor_mode: "legacy", system_prompt: "Old detector prompt",
        listening_window_ms: 1200, human_utterance_max_ms: 1700,
        voicemail_action: "leave_message" as const,
        voicemail_message: { text: "Please call back." }, screening_message: { recording_pk: 9 },
    };
    render(<VoicemailDetectionDialog open onOpenChange={vi.fn()} onSave={onSave}
        workflowConfigurations={resolveWorkflowConfigurations({ voicemail_detection: oldConfig })} />);
    expect(screen.queryByText(/System Prompt|Speech Cutoff|Existing detection/i)).toBeNull();
    fireEvent.change(screen.getByLabelText("Voicemail message"), { target: { value: "Call us tomorrow." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const saved = onSave.mock.calls[0][0].voicemail_detection;
    expect(saved).toMatchObject({
        enabled: true, use_workflow_llm: false, provider: "openai", model: "gpt-4.1", api_key: "test-key",
        listening_window_ms: 1200, human_utterance_max_ms: 1700,
        voicemail_action: "leave_message",
        voicemail_message: { text: "Call us tomorrow." }, screening_message: { recording_pk: 9 },
    });
    expect(saved).not.toHaveProperty("system_prompt");
    expect(saved).not.toHaveProperty("long_speech_timeout");
    expect(saved).not.toHaveProperty("supervisor_mode");
});

it("requires a message for playback and saves disconnect independently of message content", () => {
    const onSave = vi.fn();
    render(<VoicemailDetectionDialog open onOpenChange={vi.fn()} onSave={onSave}
        workflowConfigurations={resolveWorkflowConfigurations({
            voicemail_detection: { enabled: true, use_workflow_llm: true },
        })} />);
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(screen.getByRole("radio", { name: "Leave a message" }));
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Voicemail message"), { target: { value: "   " } });
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Voicemail message"), { target: { value: "Call back." } });
    expect(save.disabled).toBe(false);
    fireEvent.click(screen.getByRole("radio", { name: "Disconnect the call" }));
    fireEvent.click(save);
    expect(onSave.mock.calls[0][0].voicemail_detection).toMatchObject({
        voicemail_action: "hangup", voicemail_message: { text: "Call back." },
    });
});
