import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AIModelConfigurationV2Editor, type ModelConfigurationDefaultsV2 } from "./AIModelConfigurationV2Editor";
import { type ServiceConfigurationDefaults, ServiceConfigurationForm } from "./ServiceConfigurationForm";

const { subscriptionStatus, authState, userConfigState } = vi.hoisted(() => ({
    subscriptionStatus: vi.fn(),
    userConfigState: { userConfig: null as Record<string, unknown> | null },
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
    userConfigState.userConfig = null;
    authState.loading = false;
    authState.user = { id: "test-user" };
});
vi.mock("@/context/UserConfigContext", () => ({ useUserConfig: () => userConfigState }));
vi.mock("@/components/VoiceSelector", () => ({ VoiceSelector: () => null }));
vi.mock("@/components/VoiceSelectorModal", () => ({ VoiceSelectorModal: () => null }));
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
    llm: { openai: { required: ["api_key"], properties: { provider: { default: "openai" }, model: { default: "gpt-5.4-mini" }, api_key: { type: "string" } } } },
    tts: {}, stt: {},
    embeddings: {
        openai: { required: ["api_key"], properties: { provider: { default: "openai" }, model: { default: "text-embedding-3-small" }, api_key: { type: "string" } } },
        local: { properties: { provider: { default: "local" }, model: { default: "local-embedding" } } },
    },
    default_providers: { realtime: "openai_realtime", llm: "openai", embeddings: "openai" },
    realtime: {
        openai_live_subscription: {
            title: "OpenAI GPT-Live (ChatGPT subscription)",
            properties: {
                provider: { default: "openai_live_subscription" },
                model: { default: "gpt-live-1-codex", examples: ["gpt-live-1-codex"] },
                voice: { default: "cove", examples: ["cove"] },
                backend_model: { title: "Workflow backend model", default: "gpt-5.6-luna", examples: ["gpt-5.6-luna"], allow_custom_input: true },
            },
        },
        openai_realtime: {
            title: "OpenAI",
            required: ["api_key"],
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
    realtime: { provider: "openai_live_subscription", api_key: "obsolete-workflow-key", model: "gpt-live-1-codex", voice: "cove", backend_model: "gpt-5.4-mini" },
    llm: { provider: "openai", model: "gpt-5.4-mini", api_key: "obsolete-llm-key" },
};

const keylessSubscription = {
    provider: "openai_live_subscription", model: "gpt-live-1-codex", voice: "cove", backend_model: "gpt-5.6-luna",
};

describe("subscription voice configuration", () => {
    it("saves and reloads without obsolete API keys or a separate LLM", async () => {
        const onSave = vi.fn();
        const props = { mode: "global" as const, forceRealtime: true, configurationDefaults: defaults, initialConfig: subscriptionConfig, onSave };
        const view = render(<ServiceConfigurationForm {...props} />);
        await screen.findByDisplayValue("Cove");
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
        expect(screen.queryByLabelText(/API key/i)).toBeNull();
        expect(screen.queryByDisplayValue("obsolete-workflow-key")).toBeNull();
        expect(screen.getByDisplayValue("gpt-5.6-luna")).toBeTruthy();
        expect(screen.getByText(/Voice and workflow reasoning use your connected ChatGPT subscription/)).toBeTruthy();
        expect(screen.getByText(/There is no silent paid API fallback/)).toBeTruthy();
        await screen.findByText(/Credentials ready/);
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const saved = onSave.mock.calls[0][0];
        expect(saved).toEqual({ is_realtime: true, realtime: keylessSubscription });
        view.rerender(<ServiceConfigurationForm {...props} initialConfig={saved} />);
        expect(await screen.findByDisplayValue("gpt-live-1-codex")).toBeTruthy();
        expect(screen.queryByLabelText(/API key/i)).toBeNull();
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
    });

    it("suppresses the old key field even if cached defaults still advertise it", async () => {
        const staleDefaults = {
            ...defaults,
            realtime: {
                ...defaults.realtime,
                openai_live_subscription: {
                    ...defaults.realtime!.openai_live_subscription,
                    properties: {
                        ...defaults.realtime!.openai_live_subscription.properties,
                        api_key: { title: "Workflow backend API key", type: "string" },
                    },
                },
            },
        };
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={staleDefaults} initialConfig={subscriptionConfig} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        expect(screen.queryByLabelText("Workflow backend API key")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ is_realtime: true, realtime: keylessSubscription });
    });

    it("preserves custom subscription reasoning models", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ ...subscriptionConfig, realtime: { ...keylessSubscription, backend_model: "custom-codex-model" } }} onSave={onSave} />);
        expect(await screen.findByDisplayValue("custom-codex-model")).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].realtime.backend_model).toBe("custom-codex-model");
    });

    it("drops API credentials and hides the LLM tab when switching providers", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ ...initialConfig, llm: subscriptionConfig.llm }} onSave={onSave} />);
        const provider = await screen.findByDisplayValue("OpenAI");
        expect(screen.getByRole("tab", { name: "LLM" })).toBeTruthy();
        fireEvent.change(provider, { target: { value: "openai_live_subscription" } });
        await screen.findByDisplayValue("Cove");
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
        expect(screen.queryByLabelText(/API key/i)).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ is_realtime: true, realtime: keylessSubscription });
        fireEvent.change(screen.getByDisplayValue("OpenAI GPT-Live (ChatGPT subscription)"), { target: { value: "openai_realtime" } });
        expect(screen.getByRole("tab", { name: "LLM" })).toBeTruthy();
        expect(screen.getByLabelText("API key")).toBeTruthy();
    });

    it.each([
        { provider: "openai", model: "text-embedding-3-small", api_key: ["explicit-embedding-key"] },
        { provider: "local", model: "explicit-keyless-embedding" },
    ])("preserves explicitly configured embeddings: %j", async embeddings => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="global" forceRealtime configurationDefaults={defaults} initialConfig={{ ...subscriptionConfig, embeddings }} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ is_realtime: true, realtime: keylessSubscription, embeddings });
    });

    it("excludes obsolete LLM overrides and subscription keys", async () => {
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="override" forceRealtime configurationDefaults={defaults} initialConfig={subscriptionConfig} currentOverrides={{ llm: subscriptionConfig.llm, realtime: subscriptionConfig.realtime }} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ model_overrides: { is_realtime: true, realtime: keylessSubscription } });
    });

    it("preserves API LLM overrides after disabling a subscription realtime override", async () => {
        userConfigState.userConfig = initialConfig;
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="override" forceRealtime configurationDefaults={defaults} currentOverrides={{ realtime: keylessSubscription, llm: subscriptionConfig.llm }} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
        fireEvent.click(screen.getByRole("switch", { name: "Override Realtime Model" }));
        expect(screen.getByRole("tab", { name: "LLM" })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ model_overrides: { llm: subscriptionConfig.llm } });
    });

    it("omits API LLM overrides after disabling an API override over subscription global settings", async () => {
        userConfigState.userConfig = { is_realtime: true, realtime: keylessSubscription };
        const onSave = vi.fn();
        render(<ServiceConfigurationForm mode="override" forceRealtime configurationDefaults={defaults} currentOverrides={{ realtime: initialConfig.realtime, llm: subscriptionConfig.llm }} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getByRole("tab", { name: "LLM" })).toBeTruthy();
        fireEvent.click(screen.getByRole("switch", { name: "Override Realtime Model" }));
        expect(screen.queryByRole("tab", { name: "LLM" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ model_overrides: undefined });
    });

    it("removes optional API embeddings when the operator clears all required keys", async () => {
        const onSave = vi.fn();
        const embeddings = { provider: "openai", model: "text-embedding-3-small", api_key: ["embedding-key"] };
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} effectiveConfiguration={{ ...subscriptionConfig, embeddings }} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        fireEvent.mouseDown(screen.getByRole("tab", { name: "Embedding" }), { button: 0, ctrlKey: false });
        const key = await screen.findByDisplayValue("embedding-key");
        fireEvent.change(key, { target: { value: "" } });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ version: 2, mode: "byok", byok: { mode: "realtime", realtime: { realtime: keylessSubscription } } });
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

const v2Defaults: ModelConfigurationDefaultsV2 = {
    dograh: { voices: ["cove"], speeds: [1], languages: ["en"], defaults: { voice: "cove", speed: 1, language: "en" } },
    byok: {
        pipeline: defaults,
        realtime: { realtime: defaults.realtime!, llm: defaults.llm, embeddings: defaults.embeddings, default_providers: defaults.default_providers },
    },
};

describe("V2 subscription configuration", () => {
    it("saves keyless subscription realtime without requiring or submitting an LLM", async () => {
        const onSave = vi.fn();
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} effectiveConfiguration={subscriptionConfig} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ version: 2, mode: "byok", byok: { mode: "realtime", realtime: { realtime: keylessSubscription } } });
    });

    it("loads V2 subscription configuration without an LLM and preserves explicit embeddings", async () => {
        const onSave = vi.fn();
        const configuration = {
            version: 2, mode: "byok", byok: {
                mode: "realtime", realtime: {
                    realtime: keylessSubscription,
                    embeddings: { provider: "openai", model: "text-embedding-3-small", api_key: ["embedding-key"] },
                },
            },
        };
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} configuration={configuration} onSave={onSave} />);
        await screen.findByDisplayValue("Cove");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual(configuration);
    });

    it("still requires a separately configured LLM for API realtime", async () => {
        const onSave = vi.fn();
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} effectiveConfiguration={initialConfig} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        expect(screen.getByRole("tab", { name: "LLM" })).toBeTruthy();
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await screen.findByText("llm configuration is required");
        expect(onSave).not.toHaveBeenCalled();
    });

    it("preserves the API realtime key and separate LLM configuration", async () => {
        const onSave = vi.fn();
        const llm = { provider: "openai", model: "gpt-5.4-mini", api_key: ["api-llm-key"] };
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} effectiveConfiguration={{ ...initialConfig, llm }} onSave={onSave} />);
        await screen.findByDisplayValue("gpt-realtime-2");
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0]).toEqual({ version: 2, mode: "byok", byok: { mode: "realtime", realtime: { realtime: { ...initialConfig.realtime, api_key: ["test-key"] }, llm } } });
    });

    it("still requires the LLM for a pipeline configuration", async () => {
        const onSave = vi.fn();
        render(<AIModelConfigurationV2Editor defaults={v2Defaults} effectiveConfiguration={{ is_realtime: false }} onSave={onSave} />);
        await screen.findByRole("tab", { name: "LLM", selected: true });
        fireEvent.click(screen.getByRole("button", { name: "Save Configuration" }));
        await screen.findByText("llm configuration is required");
        expect(onSave).not.toHaveBeenCalled();
    });
});
