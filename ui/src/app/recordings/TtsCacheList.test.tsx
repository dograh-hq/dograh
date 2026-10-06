import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TtsCacheList from "./TtsCacheList";

const mocks = vi.hoisted(() => ({
    auth: { user: { id: "user" } as { id: string } | null, loading: false },
    list: vi.fn(), preview: vi.fn(), invalidate: vi.fn(), clear: vi.fn(),
    toastError: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/hooks/useOrganizationTimezone", () => ({ useOrganizationTimezone: () => "UTC" }));
vi.mock("@/client/sdk.gen", () => ({
    listTtsCacheApiV1TtsCacheGet: mocks.list,
    previewTtsCacheApiV1TtsCacheEntryIdAudioGet: mocks.preview,
    invalidateTtsCacheEntryApiV1TtsCacheEntryIdDelete: mocks.invalidate,
    clearTtsCacheApiV1TtsCacheDelete: mocks.clear,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: mocks.toastError } }));

const entry = {
    id: "a".repeat(64), text_preview: "Hello there", provider: "minimax", model: "speech",
    voice_id: "voice", duration_seconds: 2.5, hit_count: 17,
    created_at: "2026-09-20T12:00:00Z", last_used_at: "2026-09-20T13:00:00Z",
};

beforeEach(() => {
    vi.clearAllMocks();
    mocks.auth.user = { id: "user" };
    mocks.auth.loading = false;
    mocks.list.mockResolvedValue({ data: { entries: [entry], total: 1 } });
    mocks.preview.mockResolvedValue({ data: new Blob(["audio"], { type: "audio/wav" }) });
    mocks.invalidate.mockResolvedValue({ data: { removed: 1 } });
    mocks.clear.mockResolvedValue({ data: { removed: 1 } });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test-audio") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

describe("TtsCacheList", () => {
    it("waits for authentication before fetching", async () => {
        mocks.auth.loading = true;
        const { rerender } = render(<TtsCacheList />);
        await new Promise((resolve) => setTimeout(resolve, 300));
        expect(mocks.list).not.toHaveBeenCalled();
        mocks.auth.loading = false;
        rerender(<TtsCacheList />);
        expect(await screen.findByText("Hello there")).toBeTruthy();
        expect(screen.getByText("17 reuses")).toBeTruthy();
    });

    it("sends search, duration range and usage sorting to the generated client", async () => {
        render(<TtsCacheList />);
        await screen.findByText("Hello there");
        fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "most_used" } });
        fireEvent.change(screen.getByLabelText("Search cached speech"), { target: { value: "Hello" } });
        fireEvent.change(screen.getByLabelText("Min seconds"), { target: { value: "1" } });
        fireEvent.change(screen.getByLabelText("Max seconds"), { target: { value: "3" } });
        await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({
            query: expect.objectContaining({ sort: "usage", order: "desc", search: "Hello", min_duration: 1, max_duration: 3, offset: 0 }),
        })));
    });

    it("previews authenticated audio and releases the object URL on unmount", async () => {
        const { unmount } = render(<TtsCacheList />);
        fireEvent.click(await screen.findByRole("button", { name: "Listen to Hello there" }));
        const audio = await screen.findByLabelText("Cached speech preview");
        expect(audio.getAttribute("src")).toBe("blob:test-audio");
        expect(mocks.preview).toHaveBeenCalledWith(expect.objectContaining({ path: { entry_id: entry.id }, parseAs: "blob" }));
        unmount();
        expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-audio");
    });

    it("invalidates only the selected entry and refreshes the list", async () => {
        render(<TtsCacheList />);
        fireEvent.click(await screen.findByRole("button", { name: "Invalidate Hello there" }));
        await waitFor(() => expect(mocks.invalidate).toHaveBeenCalledWith({ path: { entry_id: entry.id } }));
        await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
        expect(mocks.clear).not.toHaveBeenCalled();
    });

    it("requires confirmation before clearing the organization cache", async () => {
        render(<TtsCacheList />);
        await screen.findByText("Hello there");
        vi.mocked(window.confirm).mockReturnValue(false);
        fireEvent.click(screen.getByRole("button", { name: "Clear organization cache" }));
        expect(mocks.clear).not.toHaveBeenCalled();
        vi.mocked(window.confirm).mockReturnValue(true);
        fireEvent.click(screen.getByRole("button", { name: "Clear organization cache" }));
        await waitFor(() => expect(mocks.clear).toHaveBeenCalledTimes(1));
    });

    it("renders structured API errors without crashing", async () => {
        mocks.list.mockResolvedValue({ error: { detail: [{ msg: "Invalid duration" }] } });
        render(<TtsCacheList />);
        expect((await screen.findByRole("alert")).textContent).toBe("Invalid duration");
    });

    it("reports invalidation failure without claiming success or refreshing", async () => {
        mocks.invalidate.mockResolvedValue({ error: { detail: "Speech cache is temporarily unavailable" } });
        render(<TtsCacheList />);
        fireEvent.click(await screen.findByRole("button", { name: "Invalidate Hello there" }));
        await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith("Speech cache is temporarily unavailable"));
        expect(mocks.list).toHaveBeenCalledTimes(1);
    });
});
