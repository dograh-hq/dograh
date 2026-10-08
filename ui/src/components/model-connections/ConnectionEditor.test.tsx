import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createProviderConnection, updateProviderConnection } from "@/client/sdk.gen";

import { ConnectionEditor } from "./ConnectionEditor";
import { selectOptions } from "./test-helpers";
import type { ModelConnectionCatalog, ProviderConnection } from "./types";

vi.mock("@/client/sdk.gen", () => ({ createProviderConnection: vi.fn(), updateProviderConnection: vi.fn() }));
const catalog: ModelConnectionCatalog = { services: { llm: { dograh: {
    title: "Dograh", credential_fields: { api_key: { title: "API key", anyOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] } },
    credential_required: ["api_key"], connection_fields: {}, settings_schema: { properties: {} },
} } } };
const existing: ProviderConnection = { uuid: "connection-id", name: "Dograh production", provider: "dograh", connection_settings: {}, configured_credentials: ["api_key"], revision: 2, is_active: true };
function editor(connection = existing, onSaved = vi.fn()) {
    return render(<ConnectionEditor catalog={catalog} connection={connection} onSaved={onSaved} />);
}
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(updateProviderConnection).mockResolvedValue({ data: existing } as never);
    vi.mocked(createProviderConnection).mockResolvedValue({ data: existing } as never);
});

describe("provider connection credentials", () => {
    it("offers one account provider when both standard and realtime models are available", () => {
        const openai = { ...catalog.services.llm!.dograh, title: "OpenAI" };
        const google = { ...openai, title: "Google" };
        const shared: ModelConnectionCatalog = { services: {
            llm: { openai, google },
            realtime: {
                openai: { ...openai, settings_schema: { properties: { model: { default: "gpt-realtime-2" } } } },
                google: { ...google, settings_schema: { properties: { model: { default: "gemini-3.8-live" } } } },
            },
        } };
        render(<ConnectionEditor catalog={shared} onSaved={vi.fn()} />);
        expect(selectOptions("Provider")).toEqual(["Google", "OpenAI"]);
    });

    it("preserves configured credentials without round-tripping masked or empty values", async () => {
        editor();
        expect(screen.getByText("Configured")).toBeDefined();
        expect(screen.queryByLabelText("API key")).toBeNull();
        fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Dograh renamed" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(updateProviderConnection).toHaveBeenCalledOnce());
        expect(vi.mocked(updateProviderConnection).mock.calls[0][0]).toMatchObject({ path: { connection_uuid: "connection-id" }, body: { name: "Dograh renamed", revision: 2, credentials: {} } });
    });

    it("sends only explicitly replaced credentials", async () => {
        editor();
        fireEvent.click(screen.getByRole("button", { name: "Replace" }));
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "new-secret" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(updateProviderConnection).toHaveBeenCalledOnce());
        expect(vi.mocked(updateProviderConnection).mock.calls[0][0]?.body?.credentials).toEqual({ api_key: "new-secret" });
    });

    it("discarding a replacement omits the credential update", async () => {
        editor();
        fireEvent.click(screen.getByRole("button", { name: "Replace" }));
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "discarded" } });
        fireEvent.click(screen.getByRole("button", { name: "Keep existing credential" }));
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(updateProviderConnection).toHaveBeenCalledOnce());
        expect(vi.mocked(updateProviderConnection).mock.calls[0][0]?.body?.credentials).toEqual({});
    });

    it("adds and removes individual API key inputs and saves a credential pool", async () => {
        editor();
        fireEvent.click(screen.getByRole("button", { name: "Replace" }));
        fireEvent.change(screen.getByLabelText("API key"), { target: { value: "first-secret" } });
        fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
        fireEvent.change(screen.getByLabelText("API key 2"), { target: { value: "second-secret" } });
        fireEvent.click(screen.getByRole("button", { name: "Add API Key" }));
        fireEvent.change(screen.getByLabelText("API key 3"), { target: { value: "remove-this" } });
        fireEvent.click(screen.getByRole("button", { name: "Remove API key 3" }));
        expect(screen.queryByPlaceholderText("One key per line")).toBeNull();
        expect(screen.getByLabelText("API key 2").getAttribute("type")).toBe("password");
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        await waitFor(() => expect(updateProviderConnection).toHaveBeenCalledOnce());
        expect(vi.mocked(updateProviderConnection).mock.calls[0][0]?.body?.credentials).toEqual({ api_key: ["first-secret", "second-secret"] });
    });

    it("renders semantic API validation errors instead of reporting a successful save", async () => {
        vi.mocked(updateProviderConnection).mockResolvedValue({ error: { detail: [{ loc: ["body", "credentials"], msg: "Invalid credential fields" }] } } as never);
        const onSaved = vi.fn();
        editor(existing, onSaved);
        fireEvent.click(screen.getByRole("button", { name: "Save Connection" }));
        expect(await screen.findByRole("alert")).toBeDefined();
        expect(screen.getByRole("alert").textContent).toContain("Invalid credential fields");
        expect(onSaved).not.toHaveBeenCalled();
    });
});
