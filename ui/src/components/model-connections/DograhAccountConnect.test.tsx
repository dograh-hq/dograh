import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { startDograhEmailConnect } from "@/client/sdk.gen";

import { DograhAccountConnectFlow } from "./DograhAccountConnect";

const auth = vi.hoisted(() => ({
    loading: false,
    user: { id: "local-user", email: "alice@example.com", organizationId: undefined as string | undefined },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client/sdk.gen", () => ({ startDograhEmailConnect: vi.fn(), verifyDograhEmailConnect: vi.fn(), confirmDograhEmailConnect: vi.fn() }));

beforeEach(() => {
    vi.clearAllMocks();
    auth.loading = false;
    auth.user.organizationId = undefined;
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

        auth.user.organizationId = "org-1";
        view.rerender(<DograhAccountConnectFlow onConnected={vi.fn()} />);
        fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
        await screen.findByLabelText("Verification code");
        expect(startDograhEmailConnect).toHaveBeenCalledOnce();
    });
});
