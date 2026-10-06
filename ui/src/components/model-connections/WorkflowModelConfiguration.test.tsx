import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { previewModelConfiguration } from "@/client/sdk.gen";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { resolveWorkflowConfigurations } from "@/types/workflow-configurations";

import { selectOption, selectOptions } from "./test-helpers";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";
import { useModelConnections } from "./useModelConnections";
import { WorkflowModelConfiguration } from "./WorkflowModelConfiguration";

vi.mock("@/client/sdk.gen", () => ({ previewModelConfiguration: vi.fn() }));
vi.mock("./useModelConnections", () => ({ useModelConnections: vi.fn() }));
vi.mock("@/context/UnsavedChangesContext", () => ({ useUnsavedChanges: vi.fn() }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const catalog: ModelConnectionCatalog = { services: { llm: { openai: {
    title: "OpenAI", credential_fields: {}, connection_fields: {}, settings_schema: { properties: {
        model: { type: "string", title: "Model", default: "model-a" },
        temperature: { title: "Temperature", anyOf: [{ type: "number", minimum: 0, maximum: 2 }, { type: "null" }], default: 0.1 },
    } },
} }, tts: { openai: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: { voice: { type: "string", title: "Voice" } } } } }, stt: { openai: { credential_fields: {}, connection_fields: {}, settings_schema: { properties: { model: { type: "string", title: "Model" } } } } } } };
const connections: ProviderConnection[] = [{ uuid: "connection", name: "OpenAI Production", provider: "openai", connection_settings: {}, configured_credentials: ["api_key"], is_active: true, revision: 1 }];
const configurations: NamedModelConfiguration[] = ["Sales", "Support"].map((name, index) => ({ uuid: name, name, is_active: true, revision: 1, configuration: {
    version: 3, mode: "pipeline", llm: { provider_connection_uuid: "connection", settings: { model: index ? "model-b" : "model-a", temperature: index ? 0.7 : 0.2 } },
    stt: { provider_connection_uuid: "connection", settings: { model: "transcribe" } }, tts: { provider_connection_uuid: "connection", settings: { voice: index ? "Bob" : "Alice" } }, embeddings: null,
} }));

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations, defaultUuid: "Sales", loading: false, error: null, reload: vi.fn() });
    vi.mocked(previewModelConfiguration).mockResolvedValue({ data: { configuration: {}, provenance: [] } } as never);
});

describe("workflow model configuration selection", () => {
    it("offers only the organization default and active named configurations", () => {
        vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations: [...configurations, { ...configurations[0], uuid: "archived", name: "Archived", is_active: false }], defaultUuid: "Sales", loading: false, error: null, reload: vi.fn() });
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={vi.fn()} workflowConfigurations={resolveWorkflowConfigurations()} />);
        expect(screen.getAllByRole("combobox")).toHaveLength(1);
        expect(selectOptions("Model configuration")).toEqual(["Organization default · Sales", "Sales", "Support"]);
        expect(screen.queryByRole("checkbox")).toBeNull();
        for (const label of ["Temperature", "Voice", "Mode", "LLM provider connection"]) expect(screen.queryByLabelText(label)).toBeNull();
        expect(screen.getByRole("link", { name: "Models" }).getAttribute("href")).toBe("/model-configurations");
        expect(screen.getByText("Sales · Cascade")).toBeDefined();
    });

    it("saves only the chosen UUID, clears earlier model patches, and preserves unrelated settings", async () => {
        const onSave = vi.fn();
        const original = resolveWorkflowConfigurations({ dictionary: "customer name", model_configuration_override: { model_configuration_uuid: "Sales", llm: { settings: { temperature: 0.4 } }, tts: { settings: { voice: "Custom" } } } });
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={original} />);
        selectOption("Model configuration", "Support");
        expect(screen.getByText("LLM: OpenAI Production / model-b")).toBeDefined();
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", true);
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(previewModelConfiguration).toHaveBeenCalledWith({ body: { model_configuration_uuid: "Support" } });
        expect(onSave.mock.calls[0][0].model_configuration_override).toEqual({ model_configuration_uuid: "Support" });
        expect(onSave.mock.calls[0][0].dictionary).toBe("customer name");
        expect(onSave.mock.calls[0][1]).toBe("Agent");
        expect(original.model_configuration_override?.llm?.settings?.temperature).toBe(0.4);
    });

    it("saves explicit inheritance and removes retired overrides from the submitted payload", async () => {
        const onSave = vi.fn();
        const original = resolveWorkflowConfigurations({ model_overrides: { llm: { temperature: 0.4 } }, model_configuration_override: { model_configuration_uuid: "Support" } });
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={original} />);
        expect(screen.queryByText(/This workflow has custom/)).toBeNull();
        selectOption("Model configuration", "Organization default · Sales");
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(previewModelConfiguration).toHaveBeenCalledWith({ body: {} });
        expect(onSave.mock.calls[0][0].model_configuration_override).toEqual({});
        expect(onSave.mock.calls[0][0]).not.toHaveProperty("model_overrides");
        expect(original.model_overrides?.llm?.temperature).toBe(0.4);
    });

    it.each([
        { model_overrides: { llm: { temperature: 0.4 } } },
        { model_configuration_override: { llm: { settings: { temperature: 0.4 } } } },
        { model_configuration_override: { model_configuration_uuid: "Sales", tts: { settings: { voice: "Custom" } } } },
        { model_configuration_override: { mode: "pipeline" as const, llm: { provider_connection_uuid: "connection", settings: {} }, stt: { provider_connection_uuid: "connection", settings: {} }, tts: { provider_connection_uuid: "connection", settings: {} }, embeddings: null } },
    ])("preserves existing custom settings until a replacement is explicitly chosen: %j", async saved => {
        const onSave = vi.fn();
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations(saved)} />);
        expect(screen.getByLabelText("Model configuration").textContent).toBe("Existing workflow override");
        expect(screen.getByText(/They remain in use until/)).toBeDefined();
        expect((screen.getByRole("button", { name: "Save Model Configuration" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.queryByLabelText("Temperature")).toBeNull();
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", false);
        expect(onSave).not.toHaveBeenCalled();
        expect(previewModelConfiguration).not.toHaveBeenCalled();
        selectOption("Model configuration", "Sales");
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].model_configuration_override).toEqual({ model_configuration_uuid: "Sales" });
    });

    it("does not revive retired settings when an explicit empty binding selects inheritance", () => {
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={vi.fn()} workflowConfigurations={resolveWorkflowConfigurations({ model_configuration_override: {}, model_overrides: { llm: { temperature: 0.4 } } })} />);
        expect(screen.getByLabelText("Model configuration").textContent).toBe("Organization default · Sales");
        expect(screen.queryByText(/This workflow has custom/)).toBeNull();
    });

    it("allows a named configuration when no organization default exists", async () => {
        vi.mocked(useModelConnections).mockReturnValue({ catalog, connections, configurations, defaultUuid: null, loading: false, error: null, reload: vi.fn() });
        const onSave = vi.fn();
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations()} />);
        expect((screen.getByRole("button", { name: "Save Model Configuration" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.getByText(/Set an organization default in Models/)).toBeDefined();
        selectOption("Model configuration", "Support");
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
        expect(onSave.mock.calls[0][0].model_configuration_override).toEqual({ model_configuration_uuid: "Support" });
    });

    it("keeps an unavailable saved UUID explicit until the user chooses a replacement", () => {
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={vi.fn()} workflowConfigurations={resolveWorkflowConfigurations({ model_configuration_override: { model_configuration_uuid: "unavailable" } })} />);
        expect(screen.getByLabelText("Model configuration").textContent).toBe("Unavailable configuration");
        expect(screen.getByText(/The selected configuration is unavailable/)).toBeDefined();
        expect((screen.getByRole("button", { name: "Save Model Configuration" }) as HTMLButtonElement).disabled).toBe(true);
        expect(screen.queryByText("Sales · Cascade")).toBeNull();
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", false);
    });

    it("does not write workflow settings when semantic preview fails", async () => {
        vi.mocked(previewModelConfiguration).mockResolvedValue({ error: { detail: [{ msg: "Provider connection not found", loc: ["body"] }] } } as never);
        const onSave = vi.fn();
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations()} />);
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Provider connection not found");
        expect(onSave).not.toHaveBeenCalled();
    });

    it("retains the draft selection and reports workflow save failures", async () => {
        const onSave = vi.fn().mockRejectedValue(new Error("Unable to save workflow"));
        render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations()} />);
        selectOption("Model configuration", "Support");
        fireEvent.click(screen.getByRole("button", { name: "Save Model Configuration" }));
        expect((await screen.findByRole("alert")).textContent).toContain("Unable to save workflow");
        expect(screen.getByLabelText("Model configuration").textContent).toBe("Support");
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", true);
    });

    it("syncs the selection and clears dirty state after the parent saves it", async () => {
        const onSave = vi.fn();
        const { rerender } = render(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations()} />);
        selectOption("Model configuration", "Support");
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", true);
        rerender(<WorkflowModelConfiguration workflowName="Agent" onSave={onSave} workflowConfigurations={resolveWorkflowConfigurations({ model_configuration_override: { model_configuration_uuid: "Support" } })} />);
        expect(useUnsavedChanges).toHaveBeenLastCalledWith("models", false);
        expect(screen.getByLabelText("Model configuration").textContent).toBe("Support");
    });
});
