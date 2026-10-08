import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { resolveWorkflowConfigurations, type WorkflowConfigurations } from "@/types/workflow-configurations";

import { selectedMode, selectOption, selectOptions, servicePanel } from "./test-helpers";
import type { ConfigurationSpec, ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";
import { WorkflowModelConfiguration } from "./WorkflowModelConfiguration";

vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({ getVoicesApiV1UserConfigurationsVoicesProviderGet: vi.fn(async () => ({ data: { voices: [] } })) }));

const catalog: ModelConnectionCatalog = { services: { llm: { openai: {
    title: "OpenAI", credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
        model: { type: "string", title: "Model", default: "model-a" },
        temperature: { title: "Temperature", anyOf: [{ type: "number", minimum: 0, maximum: 2 }, { type: "null" }], default: 0.1 },
    } },
} }, tts: { openai: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: { voice: { type: "string", title: "Voice", examples: ["Alice", "Bob"] } } } } }, stt: { openai: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: { model: { type: "string", title: "Model" } } } } } } };
const connections: ProviderConnection[] = [{ uuid: "connection", name: "OpenAI Production", provider: "openai", connection_settings: {}, configured_credentials: ["api_key"], is_active: true, revision: 1 }];
const spec = (model: string, voice: string): ConfigurationSpec => ({
    version: 3, mode: "pipeline", llm: { provider_connection_uuid: "connection", settings: { model, temperature: 0.2 } },
    stt: { provider_connection_uuid: "connection", settings: { model: "transcribe" } }, tts: { provider_connection_uuid: "connection", settings: { voice } }, embeddings: null,
});
const configurations: NamedModelConfiguration[] = [
    { uuid: "Sales", name: "Sales", is_active: true, revision: 1, configuration: spec("model-a", "Alice") },
    { uuid: "Support", name: "Support", is_active: true, revision: 1, configuration: spec("model-b", "Bob") },
];

type SaveHandler = (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;

function renderCard(overrides: Parameters<typeof resolveWorkflowConfigurations>[0] = {}) {
    const onSave = vi.fn<SaveHandler>(async () => undefined);
    const card = (workflowConfigurations: WorkflowConfigurations) =>
        <UnsavedChangesProvider><WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={workflowConfigurations} /></UnsavedChangesProvider>;
    const workflowConfigurations = resolveWorkflowConfigurations({ dictionary: "customer name", ...overrides });
    const { rerender } = render(card(workflowConfigurations));
    const saveAt = (index: number) => onSave.mock.calls[index] as [WorkflowConfigurations, string];
    return { onSave, workflowConfigurations, saveAt, rerender: (next: WorkflowConfigurations) => rerender(card(next)) };
}

const tab = (name: string) => screen.getByRole("tab", { name });
const llmModel = () => within(servicePanel("LLM")).getByLabelText("Model") as HTMLInputElement;
const customFromScratch = {
    mode: "pipeline",
    llm: { provider_connection_uuid: "connection", settings: { model: "model-z", temperature: 0.1 } },
    stt: { provider_connection_uuid: "connection", settings: {} },
    tts: { provider_connection_uuid: "connection", settings: {} },
    embeddings: null,
    llm_fallback: { rules: [] },
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations, defaultUuid: "Sales", loading: false, error: null, reload: vi.fn() });
});

describe("workflow model configuration", () => {
    it("follows the organization default and offers the configurations, with no fields to edit", () => {
        renderCard();
        expect(tab("Preset configuration").getAttribute("aria-selected")).toBe("true");
        expect(screen.getByRole("status").textContent).toBe("Following Sales, the organization default.");
        expect(selectOptions("Configuration")).toEqual(["Organization default (Sales)", "Sales", "Support"]);
        expect(screen.getByRole("link", { name: "Open Sales in Models" }).getAttribute("href")).toBe("/model-configurations/Sales");
        expect(screen.queryByLabelText("Mode")).toBeNull();
    });

    it("pins the organization default by name, so the agent stays on it when the default changes", () => {
        renderCard({ model_configuration_override: { model_configuration_uuid: "Sales" } });
        expect(screen.getByLabelText("Configuration").textContent).toBe("Sales");
        expect(screen.getByRole("status").textContent).toBe("Following Sales.");
    });

    it("switches to a chosen configuration and drops retired keys", async () => {
        const { onSave, saveAt } = renderCard({ model_overrides: { llm: { temperature: 0.4 } }, model_configuration_override: {} });
        selectOption("Configuration", "Support");
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const [saved, name] = saveAt(0);
        expect(name).toBe("Agent");
        expect(saved.model_configuration_override).toEqual({ model_configuration_uuid: "Support" });
        expect(saved.dictionary).toBe("customer name");
        expect(saved).not.toHaveProperty("model_overrides");
    });

    it("starts custom settings from scratch and saves the whole configuration on the first change", async () => {
        const { onSave, saveAt } = renderCard();
        fireEvent.mouseDown(tab("Custom"));
        expect(screen.getByRole("status").textContent).toContain("save with your first change");
        expect(selectedMode()).toBe("BYOK");
        expect(llmModel().value).toBe("model-a");
        fireEvent.change(llmModel(), { target: { value: "model-z" } });
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce(), { timeout: 2000 });
        expect(saveAt(0)[0].model_configuration_override).toEqual(customFromScratch);
        expect(screen.getByRole("status").textContent).toContain("Custom settings for this agent.");
    });

    it("discards an unsaved custom draft without touching the agent", () => {
        const { onSave } = renderCard();
        fireEvent.mouseDown(tab("Custom"));
        fireEvent.mouseDown(tab("Preset configuration"));
        expect(onSave).not.toHaveBeenCalled();
        expect(screen.getByRole("status").textContent).toBe("Following Sales, the organization default.");
    });

    it("returns to the preset the custom settings started from", async () => {
        const { onSave, saveAt, rerender } = renderCard({ model_configuration_override: { model_configuration_uuid: "Support" } });
        fireEvent.mouseDown(tab("Custom"));
        fireEvent.change(llmModel(), { target: { value: "model-z" } });
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce(), { timeout: 2000 });
        rerender(saveAt(0)[0]);
        fireEvent.mouseDown(tab("Preset configuration"));
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
        expect(saveAt(1)[0].model_configuration_override).toEqual({ model_configuration_uuid: "Support" });
    });

    it("shows a saved patch as custom settings and re-saves them in full", async () => {
        const { onSave, saveAt } = renderCard({ model_configuration_override: { model_configuration_uuid: "Support", tts: { settings: { voice: "Alice" } } } });
        expect(tab("Custom").getAttribute("aria-selected")).toBe("true");
        expect(llmModel().value).toBe("model-b");
        expect(screen.getByLabelText("Voice").textContent).toBe("Alice");
        fireEvent.change(llmModel(), { target: { value: "model-z" } });
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce(), { timeout: 2000 });
        const [saved] = saveAt(0);
        expect(saved.model_configuration_override).toEqual({
            mode: "pipeline",
            llm: { provider_connection_uuid: "connection", settings: { model: "model-z", temperature: 0.2 } },
            stt: { provider_connection_uuid: "connection", settings: { model: "transcribe" } },
            tts: { provider_connection_uuid: "connection", settings: { voice: "Alice" } },
            embeddings: null,
            llm_fallback: { rules: [] },
        });
    });

    it("goes back to following a configuration, dropping the custom settings", async () => {
        const { onSave, saveAt } = renderCard({ model_configuration_override: { tts: { settings: { voice: "Bob" } } } });
        fireEvent.mouseDown(tab("Preset configuration"));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(saveAt(0)[0].model_configuration_override).toEqual({});
    });

    it("returns an older partial patch to the configuration it was layered on", async () => {
        const { onSave, saveAt } = renderCard({ model_configuration_override: { model_configuration_uuid: "Support", tts: { settings: { voice: "Alice" } } } });
        fireEvent.mouseDown(tab("Preset configuration"));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(saveAt(0)[0].model_configuration_override).toEqual({ model_configuration_uuid: "Support" });
    });

    it("keeps a failed edit on screen and retries it", async () => {
        const { onSave, saveAt } = renderCard();
        onSave.mockRejectedValueOnce(new Error("Provider connection not found"));
        fireEvent.mouseDown(tab("Custom"));
        fireEvent.change(llmModel(), { target: { value: "model-z" } });
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce(), { timeout: 2000 });
        await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Provider connection not found"));
        expect(llmModel().value).toBe("model-z");
        fireEvent.click(within(screen.getByRole("alert")).getByRole("button", { name: "Retry" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
        expect(saveAt(1)[0].model_configuration_override).toEqual(customFromScratch);
        await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    });

    it("keeps legacy inline settings until a configuration is chosen", async () => {
        const { onSave, saveAt } = renderCard({ model_overrides: { llm: { temperature: 0.4 } } });
        expect(screen.getByRole("status").textContent).toContain("custom model settings");
        expect(screen.getByLabelText("Configuration").textContent).toBe("Existing workflow override");
        // Looking at Custom and coming back leaves them alone.
        fireEvent.mouseDown(tab("Custom"));
        fireEvent.mouseDown(tab("Preset configuration"));
        expect(onSave).not.toHaveBeenCalled();
        expect(screen.getByLabelText("Configuration").textContent).toBe("Existing workflow override");
        selectOption("Configuration", "Organization default (Sales)");
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        const [saved] = saveAt(0);
        expect(saved.model_configuration_override).toEqual({});
        expect(saved).not.toHaveProperty("model_overrides");
    });

    it("explains when nothing is configured yet", () => {
        vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations: [], defaultUuid: null, loading: false, error: null, reload: vi.fn() });
        renderCard();
        expect(screen.getByRole("status").textContent).toContain("No configuration chosen yet");
        expect(screen.getByLabelText("Configuration").textContent).toBe("Organization default");
    });
});
