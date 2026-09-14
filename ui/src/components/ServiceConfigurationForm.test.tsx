import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type ServiceConfigurationDefaults, ServiceConfigurationForm } from "./ServiceConfigurationForm";

const { subscriptionStatus, authState } = vi.hoisted(() => ({
    subscriptionStatus: vi.fn(),
    authState: { loading: false, user: { id: "test-user" } as { id: string } | null },
}));
vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
});
vi.mock("@/client/sdk.gen", () => ({
    getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet: vi.fn(),
    getOpenaiSubscriptionStatusApiV1UserConfigurationsOpenaiSubscriptionStatusGet: subscriptionStatus,
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => authState }));
beforeEach(() => {
    subscriptionStatus.mockReset();
    subscriptionStatus.mockResolvedValue({ data: { status: "ready", message: "Credentials appear usable. A voice session must verify access." } });
    authState.loading = false;
    authState.user = { id: "test-user" };
});
vi.mock("@/context/UserConfigContext", () => ({ useUserConfig: () => ({ userConfig: null }) }));
vi.mock("@/components/VoiceSelector", () => ({ VoiceSelector: () => null }));
vi.mock("@/components/ui/select", () => ({
    Select: ({ value, onValueChange, children }: { value: string; onValueChange: (value: string) => void; children: ReactNode }) => (
        <select value={value} onChange={event => onValueChange(event.target.value)}>{children}</select>
    ),
    SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectItem: ({ value, children }: { value: string; children: ReactNode }) => <option value={value}>{children}</option>,
}));

const defaults: ServiceConfigurationDefaults = {
    llm: {}, tts: {}, stt: {}, embeddings: {},
    default_providers: { realtime: "openai_realtime" },
    realtime: {
        openai_live_subscription: {
            title: "OpenAI GPT-Live (ChatGPT subscription)",
            properties: {
                provider: { default: "openai_live_subscription" },
                model: { default: "gpt-live-1-codex", examples: ["gpt-live-1-codex"] },
                voice: { default: "cove", examples: ["cove"] },
                backend_model: { title: "Workflow backend model", default: "gpt-5.4-mini", examples: ["gpt-5.4-mini"], allow_custom_input: true },
                api_key: { title: "Workflow backend API key", type: "string" },
            },
        },
        openai_realtime: {
            title: "OpenAI",
            properties: {
                provider: { default: "openai_realtime" },
                model: { default: "gpt-realtime-2", examples: ["gpt-live-1", "gpt-realtime-2.1", "gpt-realtime-2"] },
                voice: { default: "alloy", examples: ["alloy", "marin", "cedar"], model_options: { "gpt-live-1": ["marin", "cedar"] } },
                language: { default: "en", examples: ["en", "fr"], hidden_for_models: ["gpt-live-1"] },
                backend_model: { default: "gpt-5.4-mini", examples: ["gpt-5.4-mini"], visible_for_models: ["gpt-live-1"] },
                api_key: { type: "string" },
            },
        },
    },
};

const initialConfig = {
    is_realtime: true,
    realtime: { provider: "openai_realtime", api_key: "test-key", model: "gpt-realtime-2", voice: "alloy", language: "en" },
};

describe("OpenAI speech model selection", () => {
    it("switches to Live under the same provider and saves its relevant settings", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getAllByRole("option", { name: "OpenAI" })).toHaveLength(1);
        expect(screen.queryByText("backend model")).toBeNull();

        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await waitFor(() => expect(screen.getByDisplayValue("Marin")).toBeTruthy());
        expect(screen.queryByText("language")).toBeNull();
        expect(screen.getByText("backend model")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));

        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({
            provider: "openai_realtime", api_key: ["test-key"], model: "gpt-live-1", voice: "marin", backend_model: "gpt-5.4-mini",
        });
    });

    it("hides Live backend settings when switching back to Realtime", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={initialConfig} onSave={onSave} />);
        const modelSelect = await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.change(modelSelect, { target: { value: "gpt-live-1" } });
        await screen.findByText("backend model");
        fireEvent.change(modelSelect, { target: { value: "gpt-realtime-2.1" } });
        expect(screen.queryByText("backend model")).toBeNull();
        expect(screen.getByText("language")).toBeTruthy();
        expect(screen.getByDisplayValue("Marin")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.model).toBe("gpt-realtime-2.1");
        expect(onSave.mock.calls[0][0].realtime.voice).toBe("marin");
        expect(onSave.mock.calls[0][0].realtime).not.toHaveProperty("backend_model");
    });
});

const subscriptionConfig = {
    is_realtime: true,
    realtime: { provider: "openai_live_subscription", api_key: "workflow-only-key", model: "gpt-live-1-codex", voice: "cove", backend_model: "gpt-5.4-mini" },
};

describe("subscription voice configuration", () => {
    it("saves and reloads the subscription provider with a separate workflow API key", async () => {
        const onSave = vi.fn();
        const props = { mode: "global" as const, forceRealtime: true, configurationDefaults: defaults, initialConfig: subscriptionConfig, onSave };
        const view = render(<ServiceConfigurationForm {...props} />);
        const key = await screen.findByLabelText("Workflow backend API key");
        expect(key.getAttribute("type")).toBe("password");
        expect(screen.getByDisplayValue("Cove")).toBeTruthy();
        expect(screen.getByText("Workflow backend model")).toBeTruthy();
        expect(screen.getByText(/Workflow reasoning, analysis, extraction, and telephony are billed separately/)).toBeTruthy();
        await screen.findByText(/Credentials ready/);
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime).toEqual({ ...subscriptionConfig.realtime, api_key: ["workflow-only-key"] });
        view.rerender(<ServiceConfigurationForm {...props} initialConfig={onSave.mock.calls[0][0]} />);
        expect((await screen.findByLabelText("Workflow backend API key") as HTMLInputElement).value).toBe("workflow-only-key");
        expect(screen.getByDisplayValue("gpt-live-1-codex")).toBeTruthy();
    });

    it("waits for authenticated user readiness before checking the connection", async () => {
        authState.loading = true;
        authState.user = null;
        const props = { mode: "global" as const, forceRealtime: true, configurationDefaults: defaults, initialConfig: subscriptionConfig, onSave: vi.fn() };
        const view = render(<ServiceConfigurationForm {...props} />);
        await screen.findByText("Subscription voice connection");
        expect(subscriptionStatus).not.toHaveBeenCalled();
        authState.loading = false;
        view.rerender(<ServiceConfigurationForm {...props} />);
        expect(screen.getByText("Sign in to check the connection.")).toBeTruthy();
        expect(subscriptionStatus).not.toHaveBeenCalled();
        authState.user = { id: "test-user" };
        view.rerender(<ServiceConfigurationForm {...props} />);
        await screen.findByText(/Credentials ready/);
        expect(subscriptionStatus).toHaveBeenCalledOnce();
    });

    it("normalizes HTTP validation errors and lets the operator check again", async () => {
        subscriptionStatus.mockResolvedValueOnce({ error: { detail: [{ msg: "Subscription unavailable for this organization", loc: ["organization"] }] } });
        subscriptionStatus.mockResolvedValueOnce({ data: { status: "busy", message: "Finish the active subscription session first." } });
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={subscriptionConfig} onSave={vi.fn()} />);
        expect((await screen.findByRole("alert")).textContent).toBe("Subscription unavailable for this organization");
        fireEvent.click(screen.getByRole("button", { name: "Check connection" }));
        await screen.findByText(/Account busy/);
        expect(screen.queryByRole("alert")).toBeNull();
        expect(subscriptionStatus).toHaveBeenCalledTimes(2);
    });

    it("shows an actionable network failure without exposing the transport error", async () => {
        subscriptionStatus.mockRejectedValueOnce(new Error("private transport detail"));
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={subscriptionConfig} onSave={vi.fn()} />);
        expect((await screen.findByRole("alert")).textContent).toBe("Unable to reach Dograh. Try checking the connection again.");
        expect(screen.queryByText(/private transport detail/)).toBeNull();
    });

    it.each([
        ["disabled", "Disabled"], ["login_required", "Login required"],
        ["refresh_required", "Refresh required"], ["reauthentication_required", "Reconnect required"],
        ["unavailable", "Unavailable"],
    ])("displays the %s connection state", async (status, label) => {
        subscriptionStatus.mockResolvedValueOnce({ data: { status, message: "Ask the server operator to check the dedicated subscription login." } });
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={subscriptionConfig} onSave={vi.fn()} />);
        await screen.findByText(`${label}:`);
        expect(screen.getByText(/Ask the server operator/)).toBeTruthy();
    });
});
