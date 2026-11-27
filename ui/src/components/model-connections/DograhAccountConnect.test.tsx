import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { startDograhEmailConnect } from "@/client/sdk.gen";
import type { UserResponse } from "@/client/types.gen";

import { DograhAccountConnectFlow } from "./DograhAccountConnect";

const auth = vi.hoisted(() => ({
    loading: false,
    user: { id: 232, email: "alice@example.com", organization_id: 239 } satisfies UserResponse,
}));
const organization = vi.hoisted(() => ({ loading: false, orgContext: null as { organization_id: number } | null }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => organization }));
vi.mock("@/client/sdk.gen", () => ({ startDograhEmailConnect: vi.fn(), verifyDograhEmailConnect: vi.fn(), confirmDograhEmailConnect: vi.fn() }));

beforeEach(() => {
    vi.clearAllMocks();
    auth.loading = false;
    organization.loading = false;
    organization.orgContext = null;
    vi.mocked(startDograhEmailConnect).mockResolvedValue({ data: { device_code: "device", expires_in: 600 } } as never);
});

describe("local connection readiness", () => {
    it("waits for a selected organization before allowing a verification email", async () => {
        const view = render(<DograhAccountConnectFlow onConnected={vi.fn()} />);
        const send = screen.getByRole("button", { name: "Send verification code" }) as HTMLButtonElement;
        expect(send.disabled).toBe(true);
        expect(screen.getByText("Select an organization to connect Dograh models.")).toBeDefined();
        fireEvent.click(send);
        expect(startDograhEmailConnect).not.toHaveBeenCalled();

        organization.orgContext = { organization_id: auth.user.organization_id };
        view.rerender(<DograhAccountConnectFlow onConnected={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
        await screen.findByLabelText("Verification code");
        expect(startDograhEmailConnect).toHaveBeenCalledOnce();
    });

    it("waits for organization context to finish loading with the OSS login response", async () => {
        organization.orgContext = { organization_id: auth.user.organization_id };
        organization.loading = true;
        const view = render(<DograhAccountConnectFlow onConnected={vi.fn()} />);
        expect((screen.getByRole("button", { name: "Send verification code" }) as HTMLButtonElement).disabled).toBe(true);
        organization.loading = false;
        view.rerender(<DograhAccountConnectFlow onConnected={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
        await screen.findByLabelText("Verification code");
        expect(startDograhEmailConnect).toHaveBeenCalledWith(expect.objectContaining({ body: { email: "alice@example.com", install_url: window.location.origin } }));
    });
});
