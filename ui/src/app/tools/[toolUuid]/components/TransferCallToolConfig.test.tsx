import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ContextDestinationRuleRow } from "../../config";
import { TransferCallToolConfig } from "./TransferCallToolConfig";

const noop = vi.fn();

function inputValue(label: string): string {
    return (screen.getByLabelText(label) as HTMLInputElement).value;
}

function ContextMappingHarness({ rules: initialRules }: { rules?: ContextDestinationRuleRow[] }) {
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
