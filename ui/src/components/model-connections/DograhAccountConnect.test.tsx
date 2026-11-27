import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pollDograhAccountConnect, startDograhAccountConnect } from "@/client/sdk.gen";

import { DograhAccountConnect, DograhAccountConnectFlow } from "./DograhAccountConnect";

vi.mock("@/client/sdk.gen", () => ({ startDograhAccountConnect: vi.fn(), pollDograhAccountConnect: vi.fn() }));

const started = {
    device_code: "device-secret",
    user_code: "BCDF-GHJK",
    verification_uri: "https://app.dograh.com/connect",
    verification_uri_complete: "https://app.dograh.com/connect?code=BCDF-GHJK",
    expires_in: 600,
    interval: 3,
};

beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.spyOn(window, "open").mockReturnValue(null);
    vi.mocked(startDograhAccountConnect).mockResolvedValue({ data: started } as never);
});

afterEach(() => vi.useRealTimers());

async function connect() {
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Connect Dograh account" })); });
}

describe("Connect Dograh account", () => {
    it("shows the code, polls until approved, then reports the connection", async () => {
        const onConnected = vi.fn();
        vi.mocked(pollDograhAccountConnect)
            .mockResolvedValueOnce({ data: { status: "pending" } } as never)
            .mockResolvedValueOnce({ data: { status: "connected", team_name: "Acme", made_default: true } } as never);
        render(<DograhAccountConnect onConnected={onConnected} />);

        await connect();
        expect(startDograhAccountConnect).toHaveBeenCalledWith({ body: { install_url: window.location.origin } });
        expect(screen.getByText("BCDF-GHJK")).toBeDefined();
        expect(screen.getByRole("link", { name: /Open approval page/ }).getAttribute("href")).toBe(started.verification_uri_complete);

        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        expect(onConnected).not.toHaveBeenCalled();
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });

        expect(pollDograhAccountConnect).toHaveBeenCalledWith(expect.objectContaining({ body: { device_code: "device-secret" } }));
        expect(screen.getByRole("status").textContent).toContain("Connected to Acme.");
        expect(onConnected).toHaveBeenCalledOnce();
    });

    it("reports a pending request and detaches the approval tab", async () => {
        const tab = { opener: window as unknown, location: { href: "" }, close: vi.fn() };
        vi.mocked(window.open).mockReturnValue(tab as never);
        vi.mocked(pollDograhAccountConnect).mockResolvedValue({ data: { status: "pending" } } as never);
        const onPendingChange = vi.fn();
        render(<DograhAccountConnectFlow onConnected={vi.fn()} onPendingChange={onPendingChange} />);
        await connect();
        expect(tab.opener).toBeNull();
        expect(tab.location.href).toBe(started.verification_uri_complete);
        expect(onPendingChange).toHaveBeenLastCalledWith(true);
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onPendingChange).toHaveBeenLastCalledWith(false);
    });

    it("offers a restart when the request is denied", async () => {
        vi.mocked(pollDograhAccountConnect).mockResolvedValue({ data: { status: "denied" } } as never);
        render(<DograhAccountConnect onConnected={vi.fn()} />);
        await connect();
        await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
        expect(screen.getByText("The request was denied on Dograh.")).toBeDefined();
        expect(screen.getByRole("button", { name: "Start again" })).toBeDefined();
    });

    it("stops polling when cancelled", async () => {
        vi.mocked(pollDograhAccountConnect).mockResolvedValue({ data: { status: "pending" } } as never);
        render(<DograhAccountConnect onConnected={vi.fn()} />);
        await connect();
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
        expect(pollDograhAccountConnect).not.toHaveBeenCalled();
    });

    it("expires even when the API keeps returning errors", async () => {
        vi.mocked(pollDograhAccountConnect).mockResolvedValue({ error: { detail: "Unavailable" }, response: { status: 503 } } as never);
        render(<DograhAccountConnect onConnected={vi.fn()} />);
        await connect();
        await act(async () => { await vi.advanceTimersByTimeAsync(started.expires_in * 1000); });
        expect(screen.getByText( "The code expired before it was approved.")).toBeDefined();
        const polls = vi.mocked(pollDograhAccountConnect).mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
        expect(pollDograhAccountConnect).toHaveBeenCalledTimes(polls);
    });

    it("expires and aborts a poll that never returns", async () => {
        vi.mocked(pollDograhAccountConnect).mockReturnValue(new Promise<never>(() => {}));
        render(<DograhAccountConnect onConnected={vi.fn()} />);
        await connect();
        await act(async () => { await vi.advanceTimersByTimeAsync(started.expires_in * 1000); });
        expect(screen.getByText("The code expired before it was approved.")).toBeDefined();
        expect(vi.mocked(pollDograhAccountConnect).mock.calls[0][0]?.signal?.aborted).toBe(true);
    });

    it.each([401, 403])("stops on HTTP %s instead of retrying authentication", async (status) => {
        vi.mocked(pollDograhAccountConnect).mockResolvedValue({ error: { detail: "Unauthorized" }, response: { status } } as never);
        render(<DograhAccountConnect onConnected={vi.fn()} />);
        await connect();
        await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
        expect(screen.getByText(/Sign in again, then retry/)).toBeDefined();
        expect(pollDograhAccountConnect).toHaveBeenCalledOnce();
    });
});
