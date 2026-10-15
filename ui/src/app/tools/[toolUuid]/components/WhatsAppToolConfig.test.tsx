import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet } from "@/client/sdk.gen";
import type { WhatsAppTemplateConfig } from "@/client/types.gen";

import { WhatsAppToolConfig } from "./WhatsAppToolConfig";

vi.mock("@/client/sdk.gen", () => ({
    listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet: vi.fn(),
}));

const mockGetAccessToken = vi.fn().mockResolvedValue("test-token");
vi.mock("@/lib/auth", () => ({
    useAuth: () => ({
        getAccessToken: mockGetAccessToken,
        user: { id: "user-1", email: "test@example.com" },
        loading: false,
    }),
}));

const mockTemplates = [
    {
        id: "123456789",
        name: "booking_confirmation",
        language: "en_US",
        category: "UTILITY",
        status: "APPROVED",
        body_text: "Hi {{1}}, your booking for {{2}} is confirmed!",
        variables: [
            { index: 1, name: "customer_name", example: "John" },
            { index: 2, name: "booking_date", example: "Tomorrow 3pm" },
        ],
    },
];

const mockPhoneNumbers = [
    {
        phone_number_id: "phone_1",
        display_phone_number: "+1 555-0199",
        verified_name: "Dograh Support",
    },
];

function TestHarness() {
    const [name, setName] = useState("Send Booking Confirmation");
    const [description, setDescription] = useState("Send confirmation via WhatsApp");
    const [executionMode, setExecutionMode] = useState<"async" | "sync">("async");
    const [fallbackPhone, setFallbackPhone] = useState("+15559876543");
    const [templates, setTemplates] = useState<WhatsAppTemplateConfig[]>([
        {
            id: "1",
            name: "Booking Confirmation",
            description: "Send when appointment is booked",
            template_name: "booking_confirmation",
            template_id: "123456789",
            language_code: "en_US",
            from_phone_number_id: "phone_1",
            variable_mappings: [
                { index: 1, name: "customer_name", source: "context", value: "{{initial_context.name}}", fallback_value: "Valued Customer" },
                { index: 2, name: "booking_date", source: "llm", value: "Confirmed appointment time", fallback_value: "Upcoming" },
            ],
        },
    ]);

    return (
        <WhatsAppToolConfig
            name={name}
            onNameChange={setName}
            description={description}
            onDescriptionChange={setDescription}
            executionMode={executionMode}
            onExecutionModeChange={setExecutionMode}
            fallbackRecipientPhone={fallbackPhone}
            onFallbackRecipientPhoneChange={setFallbackPhone}
            templates={templates}
            onTemplatesChange={setTemplates}
        />
    );
}

describe("WhatsAppToolConfig", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).mockResolvedValue({
            data: {
                templates: mockTemplates,
                phone_numbers: mockPhoneNumbers,
                waba_id: "test-waba-id",
                config_id: 1,
            },
        } as unknown as Awaited<ReturnType<typeof listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet>>);
    });

    it("renders tool basic info and execution mode settings", async () => {
        render(<TestHarness />);

        const nameInput = screen.getByLabelText("Tool Name") as HTMLInputElement;
        expect(nameInput.value).toBe("Send Booking Confirmation");

        const descInput = screen.getByLabelText("Description for AI Agent") as HTMLTextAreaElement;
        expect(descInput.value).toBe("Send confirmation via WhatsApp");

        expect(screen.getByText("Async (Zero Dead Air)")).toBeTruthy();
        expect(screen.getByText("Sync (Wait for Meta Confirmation)")).toBeTruthy();
        expect(screen.getByText(/Automatic Recipient Routing:/i)).toBeTruthy();

        await waitFor(() => {
            expect(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).toHaveBeenCalled();
        });
    });

    it("renders configured template card in view mode and toggles inline edit mode", async () => {
        render(<TestHarness />);

        await waitFor(() => {
            expect(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).toHaveBeenCalled();
        });

        // Template #1 badge and LLM Choice indicator
        expect(screen.getByText("#1")).toBeTruthy();
        expect(screen.getByText('LLM Choice: "1"')).toBeTruthy();

        // Template preview should be visible
        expect(screen.getByText("Hi {{1}}, your booking for {{2}} is confirmed!")).toBeTruthy();

        // Variable mappings summary badges on card
        expect(screen.getByText(/customer_name/)).toBeTruthy();
        expect(screen.getByText(/booking_date/)).toBeTruthy();

        // Click Edit to enter inline edit mode
        const editButton = screen.getByRole("button", { name: /Edit template/i });
        fireEvent.click(editButton);

        // Card should now be in inline edit mode
        expect(screen.getByText("Editing Template #1")).toBeTruthy();
        expect(screen.getByRole("button", { name: /Done/i })).toBeTruthy();
        expect(screen.getByRole("button", { name: /Cancel/i })).toBeTruthy();
        expect((screen.getByDisplayValue("{{initial_context.name}}") as HTMLInputElement).value).toBe("{{initial_context.name}}");
        expect((screen.getByDisplayValue("Confirmed appointment time") as HTMLInputElement).value).toBe("Confirmed appointment time");

        // Cancel reverts to view mode
        const cancelButton = screen.getByRole("button", { name: /Cancel/i });
        fireEvent.click(cancelButton);

        expect(screen.queryByText("Editing Template #1")).toBeNull();
        expect(screen.getByText("Booking Confirmation")).toBeTruthy();
    });

    it("opens inline add form when Add Template is clicked and can cancel", async () => {
        render(<TestHarness />);

        await waitFor(() => {
            expect(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).toHaveBeenCalled();
        });

        const addButton = screen.getByRole("button", { name: /Add Template/i });
        fireEvent.click(addButton);

        // New inline card should open
        expect(screen.getByText("New Template #2")).toBeTruthy();
        expect(screen.getByRole("button", { name: /Done/i })).toBeTruthy();

        // Clicking cancel dismisses the new template inline card
        const cancelButton = screen.getByRole("button", { name: /Cancel/i });
        fireEvent.click(cancelButton);

        expect(screen.queryByText("New Template #2")).toBeNull();
    });

    it("deletes a template when trash button is clicked", async () => {
        render(<TestHarness />);

        await waitFor(() => {
            expect(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).toHaveBeenCalled();
        });

        const deleteButton = screen.getByRole("button", { name: /Delete template/i });
        fireEvent.click(deleteButton);

        // Should show empty state
        expect(screen.getByText(/No templates configured yet/i)).toBeTruthy();
    });

    it("selects execution mode when clicking anywhere on the card container box", async () => {
        render(<TestHarness />);

        const syncCard = screen.getByText("Sync (Wait for Meta Confirmation)").closest('[role="button"]')!;
        expect(syncCard).toBeTruthy();

        // Clicking the container box (not just the radio circle) toggles to sync
        fireEvent.click(syncCard);
        expect(screen.getByLabelText("Sync (Wait for Meta Confirmation)").getAttribute("data-state")).toBe("checked");

        const asyncCard = screen.getByText("Async (Zero Dead Air)").closest('[role="button"]')!;
        fireEvent.click(asyncCard);
        expect(screen.getByLabelText("Async (Zero Dead Air)").getAttribute("data-state")).toBe("checked");
    });

    it("renders compact variable table with column headers in edit mode", async () => {
        render(<TestHarness />);

        await waitFor(() => {
            expect(listWhatsappTemplatesApiV1OrganizationsWhatsappTemplatesGet).toHaveBeenCalled();
        });

        // Click Edit to enter inline edit mode
        const editButton = screen.getByRole("button", { name: /Edit template/i });
        fireEvent.click(editButton);

        // Verify table and column headers are rendered
        expect(screen.getByRole("table")).toBeTruthy();
        expect(screen.getByText("Placeholder")).toBeTruthy();
        expect(screen.getByText("Value / Path / Prompt")).toBeTruthy();
        expect(screen.getByText("Fallback Value (Optional)")).toBeTruthy();
        expect((screen.getByDisplayValue("Valued Customer") as HTMLInputElement).value).toBe("Valued Customer");
    });

    it("renders fallback recipient phone input in Delivery Settings", async () => {
        render(<TestHarness />);

        const fallbackInput = screen.getByLabelText("Fallback / Test Recipient Phone Number") as HTMLInputElement;
        expect(fallbackInput).toBeTruthy();
        expect(fallbackInput.value).toBe("+15559876543");
    });
});
