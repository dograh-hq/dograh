import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ContextDestinationRouteRow } from "../../config";
import { TransferCallToolConfig } from "./TransferCallToolConfig";

const noop = vi.fn();

function ContextMappingHarness() {
    const [routes, setRoutes] = useState<ContextDestinationRouteRow[]>([
        {
            id: "existing-route",
            context_value: "support",
            destination: "PJSIP/support",
        },
    ]);

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
            externalPbxRoutingEnabled
            contextMappingPath="department"
            onContextMappingPathChange={noop}
            contextDestinationRoutes={routes}
            onContextDestinationRoutesChange={setRoutes}
            fallbackDestination=""
            onFallbackDestinationChange={noop}
        />
    );
}

describe("TransferCallToolConfig context mappings", () => {
    it("preserves the focused row when an earlier mapping is removed", () => {
        render(<ContextMappingHarness />);

        fireEvent.click(screen.getByRole("button", { name: "Add mapping" }));

        const addedDestination = screen.getByLabelText("PBX destination 2");
        addedDestination.focus();
        expect(document.activeElement).toBe(addedDestination);

        fireEvent.click(screen.getByRole("button", { name: "Remove mapping 1" }));

        expect(screen.getByLabelText("PBX destination 1")).toBe(addedDestination);
        expect(document.activeElement).toBe(addedDestination);
    });
});
