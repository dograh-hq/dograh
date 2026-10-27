import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SuperadminPage from "./page";

const mocks = vi.hoisted(() => ({
    getAccessToken: vi.fn(),
    impersonate: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
    useAuth: () => ({ user: { id: "admin" }, getAccessToken: mocks.getAccessToken }),
}));

vi.mock("@/client/sdk.gen", () => ({
    impersonateApiV1SuperuserImpersonatePost: mocks.impersonate,
}));

describe("superadmin deployment selection", () => {
    let tab: { opener: unknown; location: { href: string }; close: ReturnType<typeof vi.fn> };

    beforeEach(() => {
        vi.clearAllMocks();
        vi.stubEnv("NEXT_PUBLIC_APP_URL", "https://app.dograh.com");
        mocks.getAccessToken.mockResolvedValue("admin-access-token");
        mocks.impersonate.mockResolvedValue({ data: { refresh_token: "user-refresh-token" } });
        tab = { opener: window, location: { href: "" }, close: vi.fn() };
        vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
        Element.prototype.scrollIntoView = vi.fn();
    });

    afterEach(() => {
        vi.restoreAllMocks();
        vi.unstubAllEnvs();
    });

    describe.each([
        { deployment: "Europe (EU) — eu.dograh.com", origin: "https://eu.dograh.com" },
        { deployment: "India (IN) — in.dograh.com", origin: "https://in.dograh.com" },
    ])("$deployment", ({ deployment, origin }) => {
        it.each([
            { label: "Provider User ID", value: "provider-user", button: "Impersonate by Provider ID", body: { provider_user_id: "provider-user" } },
            { label: "Email Address", value: "user@example.com", button: "Impersonate by Email", body: { email: "user@example.com" } },
        ])("opens the selected deployment when impersonating by $label", async ({ label, value, button, body }) => {
            render(<SuperadminPage />);
            fireEvent.keyDown(screen.getByRole("combobox", { name: "Deployment" }), { key: "Enter" });
            fireEvent.click(await screen.findByRole("option", { name: deployment }));
            fireEvent.change(screen.getByLabelText(label), { target: { value } });
            fireEvent.click(screen.getByRole("button", { name: button }));

            await waitFor(() => expect(tab.location.href).not.toBe(""));
            const destination = new URL(tab.location.href);
            expect(destination.origin).toBe(origin);
            expect(destination.pathname).toBe("/impersonate");
            expect(destination.searchParams.get("refresh_token")).toBe("user-refresh-token");
            expect(destination.searchParams.get("redirect_path")).toBe("/workflow");
            expect(destination.searchParams.has("access_token")).toBe(false);
            expect(tab.opener).toBeNull();
            expect(window.open).toHaveBeenCalledWith("", "_blank");
            expect(mocks.impersonate).toHaveBeenCalledWith({
                body,
                headers: { Authorization: "Bearer admin-access-token" },
            });
        });
    });

    it.each(["https://app.dograh.com", "https://staging.dograh.com", ""])(
        "preserves the default destination with NEXT_PUBLIC_APP_URL=%s",
        async (appUrl) => {
            vi.stubEnv("NEXT_PUBLIC_APP_URL", appUrl);
            render(<SuperadminPage />);
            fireEvent.change(screen.getByLabelText("Provider User ID"), { target: { value: "provider-user" } });
            fireEvent.click(screen.getByRole("button", { name: "Impersonate by Provider ID" }));

            await waitFor(() => expect(tab.location.href).not.toBe(""));
            expect(new URL(tab.location.href).origin).toBe(appUrl || window.location.origin);
        },
    );
});
