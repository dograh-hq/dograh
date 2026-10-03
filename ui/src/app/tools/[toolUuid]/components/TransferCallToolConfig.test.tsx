import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { type ContextDestinationRuleRow, DEFAULT_TRANSFER_INTRODUCTION_PROMPT } from "../../config";
import { TransferCallToolConfig } from "./TransferCallToolConfig";

const noop = vi.fn();

function inputValue(label: string): string {
    return (screen.getByLabelText(label) as HTMLInputElement).value;
}

function ContextMappingHarness({ rules: initialRules }: { rules?: ContextDestinationRuleRow[] }) {
    const [introductionEnabled, setIntroductionEnabled] = useState(false);
    const [introductionPrompt, setIntroductionPrompt] = useState(DEFAULT_TRANSFER_INTRODUCTION_PROMPT);
    const [rules, setRules] = useState<ContextDestinationRuleRow[]>(
        initialRules ?? [
            {
                id: "existing-rule",
                context_path: "department",
                routes: [
                    {
                        id: "existing-route",
                        context_value: "support",
                        destination: "PJSIP/support",
                    },
                ],
            },
        ],
    );

    return (
        <TransferCallToolConfig
            name="Transfer call"
            onNameChange={noop}
            description=""
            onDescriptionChange={noop}
            destinationSource="context_mapping"
            onDestinationSourceChange={noop}
            destination=""
            onDestinationChange={noop}
            messageType="none"
            onMessageTypeChange={noop}
            customMessage=""
            onCustomMessageChange={noop}
            audioRecordingId=""
            onAudioRecordingIdChange={noop}
            timeout={30}
            onTimeoutChange={noop}
            callDisposition=""
            onCallDispositionChange={noop}
            introductionEnabled={introductionEnabled}
            onIntroductionEnabledChange={setIntroductionEnabled}
            introductionPrompt={introductionPrompt}
            onIntroductionPromptChange={setIntroductionPrompt}
            resolverUrl=""
            onResolverUrlChange={noop}
            resolverCredentialUuid=""
            onResolverCredentialUuidChange={noop}
            resolverHeaders={[]}
            onResolverHeadersChange={noop}
            resolverTimeoutMs={3000}
            onResolverTimeoutMsChange={noop}
            resolverWaitMessage=""
            onResolverWaitMessageChange={noop}
            parameters={[]}
            onParametersChange={noop}
            presetParameters={[]}
            onPresetParametersChange={noop}
            contextDestinationRules={rules}
            onContextDestinationRulesChange={setRules}
            fallbackDestination=""
            onFallbackDestinationChange={noop}
        />
    );
}

describe("TransferCallToolConfig context mappings", () => {
    it("keeps introductions opt-in and preserves edited language instructions", () => {
        render(<ContextMappingHarness />);
        const toggle = screen.getByRole("switch", { name: "Play a transfer introduction to both parties" });
        expect(toggle.getAttribute("aria-checked")).toBe("false");
        expect(screen.queryByLabelText("Introduction instructions")).toBeNull();
        fireEvent.click(toggle);
        const prompt = screen.getByLabelText("Introduction instructions") as HTMLTextAreaElement;
        expect(prompt.value).toBe(DEFAULT_TRANSFER_INTRODUCTION_PROMPT);
        fireEvent.change(prompt, { target: { value: "Always use Brazilian Portuguese." } });
        fireEvent.click(toggle);
        fireEvent.click(toggle);
        expect((screen.getByLabelText("Introduction instructions") as HTMLTextAreaElement).value)
            .toBe("Always use Brazilian Portuguese.");
    });

    it("shows a bounded optional successful-transfer disposition", () => {
        render(<ContextMappingHarness />);

        const input = screen.getByLabelText(
            "Call Disposition After Successful Transfer"
        ) as HTMLInputElement;

        expect(input.value).toBe("");
        expect(input.maxLength).toBe(64);
        expect(screen.getByText(/recorded only when the transfer succeeds/i)).toBeTruthy();
    });

    it("makes context mapping generally available", () => {
        render(<ContextMappingHarness />);

        expect(screen.getByRole("tab", { name: "Context Mapping" })).toBeTruthy();
        expect(screen.getByText("Ordered Context Routing")).toBeTruthy();
    });

    it("preserves the focused row when an earlier mapping is removed", () => {
        render(<ContextMappingHarness />);

        fireEvent.click(screen.getByRole("button", { name: "Add mapping to rule 1" }));

        const addedDestination = screen.getByLabelText("Rule 1 transfer destination 2");
        addedDestination.focus();
        expect(document.activeElement).toBe(addedDestination);

        fireEvent.click(screen.getByRole("button", { name: "Remove rule 1 mapping 1" }));

        expect(screen.getByLabelText("Rule 1 transfer destination 1")).toBe(addedDestination);
        expect(document.activeElement).toBe(addedDestination);
    });

    it("adds an ordered rule with one empty mapping row", () => {
        render(<ContextMappingHarness />);

        fireEvent.click(screen.getByRole("button", { name: /Add routing rule/ }));

        expect(inputValue("Context field 2")).toBe("");
        expect(inputValue("Rule 2 context value 1")).toBe("");
    });

    it("reorders rules with the move controls", () => {
        render(
            <ContextMappingHarness
                rules={[
                    {
                        id: "first",
                        context_path: "qualified",
                        routes: [{ id: "r1", context_value: "yes", destination: "sales" }],
                    },
                    {
                        id: "second",
                        context_path: "state",
                        routes: [{ id: "r2", context_value: "tx", destination: "texas" }],
                    },
                ]}
            />,
        );

        expect(inputValue("Context field 1")).toBe("qualified");
        expect(screen.getByLabelText("Move rule 1 up")).toHaveProperty("disabled", true);

        fireEvent.click(screen.getByLabelText("Move rule 2 up"));

        expect(inputValue("Context field 1")).toBe("state");
        expect(inputValue("Context field 2")).toBe("qualified");
    });
});
