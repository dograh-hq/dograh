import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { confirmDograhEmailConnect, startDograhEmailConnect, verifyDograhEmailConnect } from "@/client/sdk.gen";

import { EmailDograhAccountConnect } from "./EmailDograhAccountConnect";

vi.mock("@/client/sdk.gen", () => ({ startDograhEmailConnect: vi.fn(), verifyDograhEmailConnect: vi.fn(), confirmDograhEmailConnect: vi.fn() }));
const started = { device_code: "device", expires_in: 600, interval: 3 };
const teams = [{ id: "team-a", name: "Personal" }, { id: "team-b", name: "Company" }];
const connected = vi.fn();
const props = { initialEmail: "alice@example.com", ready: true, onConnected: connected };

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(startDograhEmailConnect).mockResolvedValue({ data: started } as never);
    vi.mocked(verifyDograhEmailConnect).mockResolvedValue({ data: { teams, selected_team: teams[1] } } as never);
    vi.mocked(confirmDograhEmailConnect).mockResolvedValue({ data: { status: "connected", team_name: "Company" } } as never);
});

async function send() {
    fireEvent.click(screen.getByRole("button", { name: "Send verification code" }));
    await screen.findByLabelText("Verification code");
}

async function verify() {
    fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
}

describe("inline email connection", () => {
    it("prefills email and automatically connects the Stack-selected team without opening Cloud", async () => {
        const open = vi.spyOn(window, "open").mockReturnValue(null);
        render(<EmailDograhAccountConnect {...props} />);
        expect((screen.getByLabelText("Email address") as HTMLInputElement).value).toBe("alice@example.com");
        fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "billing@example.com" } });
        await send();
        expect(startDograhEmailConnect).toHaveBeenCalledWith(expect.objectContaining({ body: { email: "billing@example.com", install_url: window.location.origin } }));
        await verify();
        await screen.findByRole("status");
        expect(screen.queryByRole("combobox")).toBeNull();
        expect(screen.queryByLabelText("Account to connect")).toBeNull();
        expect(confirmDograhEmailConnect).toHaveBeenCalledWith(expect.objectContaining({ body: { device_code: "device", team_id: "team-b" } }));
        expect(connected).toHaveBeenCalledOnce();
        expect(open).not.toHaveBeenCalled();
        open.mockRestore();
    });

    it("shows rejected codes and allows retrying email verification", async () => {
        vi.mocked(verifyDograhEmailConnect).mockResolvedValueOnce({ error: { detail: "The code could not be verified. Please try again." } } as never);
        render(<EmailDograhAccountConnect {...props} />);
        await send();
        fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "000000" } });
        fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
        expect((await screen.findByRole("alert")).textContent).toContain("The code could not be verified");
        expect(screen.queryByRole("button", { name: /browser sign-in/i })).toBeNull();
        await verify();
        await screen.findByRole("status");
        expect(confirmDograhEmailConnect).toHaveBeenCalledOnce();
    });

    it("cancels an in-flight verification and ignores its late response", async () => {
        let resolve!: (value: never) => void;
        vi.mocked(verifyDograhEmailConnect).mockReturnValue(new Promise<never>(done => { resolve = done; }));
        render(<EmailDograhAccountConnect {...props} />);
        await send();
        fireEvent.change(screen.getByLabelText("Verification code"), { target: { value: "123456" } });
        fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
        fireEvent.click(screen.getByRole("button", { name: "Change email" }));
        await act(async () => { resolve({ data: { teams, selected_team: teams[1] } } as never); });
        expect(screen.getByLabelText("Email address")).toBeDefined();
        expect(screen.queryByLabelText("Account to connect")).toBeNull();
        expect(confirmDograhEmailConnect).not.toHaveBeenCalled();
        expect(vi.mocked(verifyDograhEmailConnect).mock.calls[0][0]?.signal?.aborted).toBe(true);
    });

    it("can retry confirmation after a lost response without restarting verification", async () => {
        vi.mocked(confirmDograhEmailConnect).mockRejectedValueOnce(new Error("Network unavailable"));
        render(<EmailDograhAccountConnect {...props} />);
        await send();
        await verify();
        await screen.findByRole("alert");
        fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
        await waitFor(() => expect(connected).toHaveBeenCalledOnce());
        expect(startDograhEmailConnect).toHaveBeenCalledOnce();
        expect(verifyDograhEmailConnect).toHaveBeenCalledOnce();
        expect(confirmDograhEmailConnect).toHaveBeenCalledTimes(2);
        expect(confirmDograhEmailConnect).toHaveBeenLastCalledWith(expect.objectContaining({ body: { device_code: "device", team_id: "team-b" } }));
    });

    it("ignores a late connection response after cancellation", async () => {
        let resolve!: (value: never) => void;
        vi.mocked(confirmDograhEmailConnect).mockReturnValue(new Promise<never>(done => { resolve = done; }));
        render(<EmailDograhAccountConnect {...props} />);
        await send();
        await verify();
        await waitFor(() => expect(confirmDograhEmailConnect).toHaveBeenCalledOnce());
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        await act(async () => { resolve({ data: { status: "connected", team_name: "Company" } } as never); });
        expect(screen.getByLabelText("Email address")).toBeDefined();
        expect(connected).not.toHaveBeenCalled();
        expect(vi.mocked(confirmDograhEmailConnect).mock.calls[0][0]?.signal?.aborted).toBe(true);
    });

    it("waits for local authentication and throttles resend", async () => {
        const view = render(<EmailDograhAccountConnect {...props} ready={false} />);
        expect((screen.getByRole("button", { name: "Send verification code" }) as HTMLButtonElement).disabled).toBe(true);
        view.rerender(<EmailDograhAccountConnect {...props} />);
        await send();
        expect((screen.getByRole("button", { name: /Resend code/ }) as HTMLButtonElement).disabled).toBe(true);
    });
});
