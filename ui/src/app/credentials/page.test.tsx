import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createCredentialApiV1CredentialsPost, deleteCredentialApiV1CredentialsCredentialUuidDelete, listCredentialsApiV1CredentialsGet, updateCredentialApiV1CredentialsCredentialUuidPut } from "@/client";
import type { CredentialResponse } from "@/client/types.gen";

import CredentialsPage from "./page";

const { auth } = vi.hoisted(() => ({
    auth: { user: { id: "user" }, loading: false, getAccessToken: vi.fn(async () => "token"), redirectToLogin: vi.fn() },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client", () => ({
    listCredentialsApiV1CredentialsGet: vi.fn(),
    createCredentialApiV1CredentialsPost: vi.fn(),
    updateCredentialApiV1CredentialsCredentialUuidPut: vi.fn(),
    deleteCredentialApiV1CredentialsCredentialUuidDelete: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const credential: CredentialResponse = {
    uuid: "crm", name: "CRM", description: "Customer records", credential_type: "api_key",
    created_at: "2026-10-06T12:00:00Z", updated_at: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    auth.loading = false;
    vi.mocked(listCredentialsApiV1CredentialsGet).mockResolvedValue({ data: [credential] } as never);
    vi.mocked(deleteCredentialApiV1CredentialsCredentialUuidDelete).mockResolvedValue({ data: { status: "deleted" } } as never);
});

describe("credentials page", () => {
    it("waits for auth before loading organization credentials", async () => {
        auth.loading = true;
        const { rerender } = render(<CredentialsPage />);
        expect(listCredentialsApiV1CredentialsGet).not.toHaveBeenCalled();
        auth.loading = false;
        rerender(<CredentialsPage />);
        expect(await screen.findByRole("heading", { name: "CRM" })).toBeDefined();
        expect(listCredentialsApiV1CredentialsGet).toHaveBeenCalledWith({ headers: { Authorization: "Bearer token" } });
    });

    it("searches names, descriptions, and readable credential types", async () => {
        render(<CredentialsPage />);
        await screen.findByRole("heading", { name: "CRM" });
        for (const query of ["crm", "customer", "API Key"]) {
            fireEvent.change(screen.getByLabelText("Search credentials"), { target: { value: query } });
            expect(screen.getByRole("heading", { name: "CRM" })).toBeDefined();
        }
        fireEvent.change(screen.getByLabelText("Search credentials"), { target: { value: "missing" } });
        expect(screen.getByText("No credentials match your search")).toBeDefined();
        fireEvent.click(screen.getByRole("button", { name: "Clear Search" }));
        expect(screen.getByRole("heading", { name: "CRM" })).toBeDefined();
    });

    it("reports a load failure and retries instead of showing an empty list", async () => {
        vi.mocked(listCredentialsApiV1CredentialsGet).mockResolvedValueOnce({ error: { detail: "Unable to load credentials" } } as never);
        render(<CredentialsPage />);
        expect((await screen.findByRole("alert")).textContent).toContain("Unable to load credentials");
        expect(screen.queryByText("No credentials yet")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(await screen.findByRole("heading", { name: "CRM" })).toBeDefined();
    });

    it("creates a credential from the empty state and adds it to the list", async () => {
        vi.mocked(listCredentialsApiV1CredentialsGet).mockResolvedValue({ data: [] } as never);
        vi.mocked(createCredentialApiV1CredentialsPost).mockResolvedValue({ data: { ...credential, credential_type: "bearer_token" } } as never);
        render(<CredentialsPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Add Your First Credential" }));
        fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "CRM" } });
        fireEvent.change(screen.getByLabelText("Token"), { target: { value: "secret-token" } });
        fireEvent.click(screen.getByRole("button", { name: "Create" }));
        expect(await screen.findByRole("heading", { name: "CRM" })).toBeDefined();
        expect(createCredentialApiV1CredentialsPost).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" },
            body: { name: "CRM", description: "", credential_type: "bearer_token", credential_data: { token: "secret-token" } },
        });
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(screen.queryByText("secret-token")).toBeNull();
    });

    it("edits metadata without replacing stored authentication", async () => {
        vi.mocked(updateCredentialApiV1CredentialsCredentialUuidPut).mockResolvedValue({ data: { ...credential, name: "Sales CRM", description: "" } } as never);
        render(<CredentialsPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit CRM" }));
        expect(screen.queryByLabelText("API Key")).toBeNull();
        fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Sales CRM" } });
        fireEvent.change(screen.getByLabelText("Description"), { target: { value: "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
        expect(await screen.findByRole("heading", { name: "Sales CRM" })).toBeDefined();
        expect(updateCredentialApiV1CredentialsCredentialUuidPut).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" }, path: { credential_uuid: "crm" },
            body: { name: "Sales CRM", description: "" },
        });
    });

    it("requires complete replacement details and renders API validation errors", async () => {
        vi.mocked(updateCredentialApiV1CredentialsCredentialUuidPut).mockResolvedValue({ error: { detail: [{ msg: "Invalid API key" }] } } as never);
        render(<CredentialsPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Edit CRM" }));
        fireEvent.click(screen.getByLabelText("Replace authentication details"));
        fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "replacement-key" } });
        expect((screen.getByRole("button", { name: "Save Changes" }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.change(screen.getByLabelText("Header Name"), { target: { value: "X-API-Key" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
        expect((await screen.findByRole("alert")).textContent).toBe("Invalid API key");
        expect(updateCredentialApiV1CredentialsCredentialUuidPut).toHaveBeenCalledWith(expect.objectContaining({
            body: { name: "CRM", description: "Customer records", credential_type: "api_key", credential_data: { header_name: "X-API-Key", api_key: "replacement-key" } },
        }));
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        fireEvent.click(screen.getByRole("button", { name: "Edit CRM" }));
        fireEvent.click(screen.getByLabelText("Replace authentication details"));
        expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe("");
        expect(screen.queryByRole("alert")).toBeNull();
    });

    it("requires delete confirmation and preserves the credential when deletion fails", async () => {
        vi.mocked(deleteCredentialApiV1CredentialsCredentialUuidDelete).mockResolvedValueOnce({ error: { detail: "Deletion failed" } } as never);
        render(<CredentialsPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Delete CRM" }));
        expect(deleteCredentialApiV1CredentialsCredentialUuidDelete).not.toHaveBeenCalled();
        fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
        expect((await screen.findByRole("alert")).textContent).toBe("Deletion failed");
        expect(screen.getByRole("heading", { name: "CRM", hidden: true })).toBeDefined();
        fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
        expect(screen.getByText("No credentials yet")).toBeDefined();
        expect(deleteCredentialApiV1CredentialsCredentialUuidDelete).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" }, path: { credential_uuid: "crm" },
        });
    });
});
