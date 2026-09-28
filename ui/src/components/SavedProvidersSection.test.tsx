import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";

import { SavedProvidersSection } from "./SavedProvidersSection";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const remove = vi.fn();

vi.mock("@/client/sdk.gen", () => ({
    listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet: (...args: unknown[]) => list(...args),
    createModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesPost: (...args: unknown[]) => create(...args),
    updateModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNamePut: (...args: unknown[]) => update(...args),
    deleteModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNameDelete: (...args: unknown[]) => remove(...args),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "u" }, loading: false }) }));
vi.mock("@/components/ui/dialog", () => ({
    Dialog: ({ open, children }: { open: boolean; children: ReactNode }) => (open ? <div>{children}</div> : null),
    DialogContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    DialogHeader: ({ children }: { children: ReactNode }) => <>{children}</>,
    DialogFooter: ({ children }: { children: ReactNode }) => <>{children}</>,
    DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
    DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
}));
vi.mock("@/components/ui/select", () => ({
    Select: ({ children }: { children: ReactNode }) => <div>{children}</div>,
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectItem: () => null,
}));
// The real form is covered by ServiceConfigurationForm tests; here it only
// hands a config back so the section's own logic is what is under test.
vi.mock("@/components/ServiceConfigurationForm", () => ({
    ServiceConfigurationForm: ({ onlyService, onSave, submitLabel }: { onlyService: string; onSave: (config: Record<string, unknown>) => Promise<void>; submitLabel: string }) => (
        <button
            type="button"
            onClick={() =>
                onSave({ [onlyService]: { provider: "openai", api_key: ["sk-1"], model: "gpt-4.1-mini" } }).catch(() => undefined)
            }
        >
            {submitLabel}
        </button>
    ),
}));

const defaults = {
    dograh: {},
    byok: {
        pipeline: { llm: {}, tts: {}, stt: {}, embeddings: {}, default_providers: {} },
        realtime: { realtime: {}, llm: {}, embeddings: {}, default_providers: {} },
    },
} as unknown as ModelConfigurationDefaultsV2;

const existing = {
    name: "openai-prod",
    service: "llm" as const,
    config: { provider: "openai", api_key: "****1111", model: "gpt-4.1-mini" },
};

describe("SavedProvidersSection", () => {
    beforeEach(() => {
        list.mockReset().mockResolvedValue({ data: { profiles: [existing] } });
        create.mockReset().mockResolvedValue({ data: existing });
        update.mockReset().mockResolvedValue({ data: existing });
        remove.mockReset().mockResolvedValue({});
    });

    it("lists saved profiles with a provider / model summary", async () => {
        render(<SavedProvidersSection defaults={defaults} />);
        expect(await screen.findByText("openai-prod")).toBeTruthy();
        expect(screen.getByText("openai / gpt-4.1-mini")).toBeTruthy();
    });

    it("creates a profile with the chosen name and service", async () => {
        render(<SavedProvidersSection defaults={defaults} />);
        await screen.findByText("openai-prod");

        fireEvent.click(screen.getByRole("button", { name: /Add provider/ }));
        fireEvent.change(screen.getByLabelText("Name"), { target: { value: "openai-cheap" } });
        fireEvent.click(screen.getByRole("button", { name: "Save provider" }));

        await waitFor(() => expect(create).toHaveBeenCalledOnce());
        expect(create.mock.calls[0][0].body).toMatchObject({
            name: "openai-cheap",
            service: "llm",
            config: { provider: "openai", model: "gpt-4.1-mini" },
        });
        await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    });

    it("rejects an invalid name without calling the API", async () => {
        render(<SavedProvidersSection defaults={defaults} />);
        await screen.findByText("openai-prod");

        fireEvent.click(screen.getByRole("button", { name: /Add provider/ }));
        fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bad Name" } });
        fireEvent.click(screen.getByRole("button", { name: "Save provider" }));

        expect(await screen.findByText(/lowercase letters/)).toBeTruthy();
        expect(create).not.toHaveBeenCalled();
    });

    it("updates the named profile in place", async () => {
        render(<SavedProvidersSection defaults={defaults} />);
        await screen.findByText("openai-prod");

        fireEvent.click(screen.getByRole("button", { name: "Edit openai-prod" }));
        fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

        await waitFor(() => expect(update).toHaveBeenCalledOnce());
        expect(update.mock.calls[0][0].path).toEqual({ service: "llm", name: "openai-prod" });
        expect(update.mock.calls[0][0].body).toMatchObject({
            config: { provider: "openai", model: "gpt-4.1-mini" },
        });
    });

    it("deletes only after confirmation", async () => {
        render(<SavedProvidersSection defaults={defaults} />);
        await screen.findByText("openai-prod");

        fireEvent.click(screen.getByRole("button", { name: "Delete openai-prod" }));
        expect(remove).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Delete" }));

        await waitFor(() => expect(remove).toHaveBeenCalledOnce());
        expect(remove.mock.calls[0][0].path).toEqual({ service: "llm", name: "openai-prod" });
    });
});
