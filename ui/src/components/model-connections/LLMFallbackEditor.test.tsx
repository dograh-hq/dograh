import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { updateNamedModelConfiguration } from "@/client/sdk.gen";

import { LLMFallbackEditor } from "./LLMFallbackEditor";
import { selectOption } from "./test-helpers";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";

vi.mock("@/client/sdk.gen", () => ({ updateNamedModelConfiguration: vi.fn(), getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));

const entry = (model: string) => ({ credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
    model: { type: "string", title: "Model", default: model }, temperature: { type: "number", title: "Temperature", default: 0.1 },
} } });
const catalog: ModelConnectionCatalog = { services: { llm: { google: entry("gemini-3.5-flash"), openai: entry("gpt-4.1"), dograh: entry("default") } } };
const connections: ProviderConnection[] = ["google", "openai", "dograh"].map(provider => ({ uuid: provider, name: provider, provider, is_active: true, revision: 1, configured_credentials: ["api_key"], connection_settings: {} }));
connections.push({ ...connections[0], uuid: "google-backup", name: "Google backup" });
const saved: NamedModelConfiguration = { uuid: "sales", name: "Sales", is_active: true, revision: 7, configuration: { version: 3, mode: "pipeline", llm: { provider_connection_uuid: "google", settings: { model: "gemini-3.5-flash" } }, stt: { provider_connection_uuid: "stt", settings: { model: "existing-transcriber" } }, tts: { provider_connection_uuid: "tts", settings: {} } } };
const onSaved = vi.fn();

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(updateNamedModelConfiguration).mockResolvedValue({ data: saved } as never);
});

describe("LLM fallback editor", () => {
    it("saves two independent conditions and provider targets with a revision", async () => {
        render(<LLMFallbackEditor saved={saved} connections={connections} catalog={catalog} onSaved={onSaved} />);
        fireEvent.click(screen.getByRole("switch", { name: "Slow response" }));
        fireEvent.change(screen.getByLabelText("Wait before starting fallback (ms)"), { target: { value: "750" } });
        fireEvent.change(screen.getByLabelText("Model"), { target: { value: "gpt-4.1-mini" } });
        fireEvent.click(screen.getByRole("switch", { name: "Request error" }));
        // Give each rule its own external target; Dograh manages its own fallbacks.
        const selectors = screen.getAllByLabelText("Provider connection");
        fireEvent.keyDown(selectors[1], { key: "ArrowDown" });
        expect(screen.queryByRole("option", { name: /dograh/i })).toBeNull();
        fireEvent.click(screen.getByRole("option", { name: "Google backup · google" }));
        fireEvent.click(screen.getByRole("button", { name: "Save fallbacks" }));
        await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
        const body = vi.mocked(updateNamedModelConfiguration).mock.calls[0][0]?.body;
        expect(body?.revision).toBe(7);
        expect(body?.configuration?.llm_fallback).toEqual({ version: 1, rules: [
            { condition: { type: "no_output", after_ms: 750 }, target: { provider_connection_uuid: "openai", settings: { model: "gpt-4.1-mini", temperature: 0.1 } } },
            { condition: { type: "error" }, target: { provider_connection_uuid: "google-backup", settings: { model: "gemini-3.5-flash", temperature: 0.1 } } },
        ] });
        expect(body?.configuration?.llm).toEqual(saved.configuration.llm);
        expect(body?.configuration?.stt).toEqual(saved.configuration.stt);
    });

    it.each([99, 10001, 750.5])("rejects delay %s with browser validity", value => {
        render(<LLMFallbackEditor saved={saved} connections={connections} catalog={catalog} onSaved={onSaved} />);
        fireEvent.click(screen.getByRole("switch", { name: "Slow response" }));
        const delay = screen.getByLabelText("Wait before starting fallback (ms)") as HTMLInputElement;
        fireEvent.change(delay, { target: { value: String(value) } });
        expect(delay.checkValidity()).toBe(false);
    });

    it("can remove a saved rule and explicitly disable fallback", async () => {
        const enabled: NamedModelConfiguration = { ...saved, configuration: { ...saved.configuration, llm_fallback: { version: 1, rules: [{ condition: { type: "error" }, target: { provider_connection_uuid: "openai", settings: {} } }] } } };
        render(<LLMFallbackEditor saved={enabled} connections={connections} catalog={catalog} onSaved={onSaved} />);
        fireEvent.click(screen.getByRole("switch", { name: "Request error" }));
        fireEvent.click(screen.getByRole("button", { name: "Save fallbacks" }));
        await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
        expect(vi.mocked(updateNamedModelConfiguration).mock.calls[0][0]?.body?.configuration?.llm_fallback).toEqual({ version: 1, rules: [] });
    });

    it("keeps the draft on validation or stale-revision errors", async () => {
        vi.mocked(updateNamedModelConfiguration).mockResolvedValue({ error: { detail: [{ msg: "Refresh this configuration", loc: ["revision"] }] } } as never);
        render(<LLMFallbackEditor saved={saved} connections={connections} catalog={catalog} onSaved={onSaved} />);
        fireEvent.click(screen.getByRole("switch", { name: "Request error" }));
        fireEvent.click(screen.getByRole("button", { name: "Save fallbacks" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Refresh this configuration");
        expect(screen.getByRole("switch", { name: "Request error" }).getAttribute("aria-checked")).toBe("true");
        expect(onSaved).not.toHaveBeenCalled();
    });

    it("resets provider settings when the target provider changes", () => {
        render(<LLMFallbackEditor saved={saved} connections={connections} catalog={catalog} onSaved={onSaved} />);
        fireEvent.click(screen.getByRole("switch", { name: "Request error" }));
        selectOption("Provider connection", "google · google");
        expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemini-3.5-flash");
    });
});
