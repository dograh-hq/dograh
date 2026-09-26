import {
    type BulkScenarioImportResponse as ApiBulkScenarioImportResponse,
    commitBulkScenariosApiV1SakinahScenariosBulkImportCommitPost,
    createScenarioApiV1SakinahScenariosPost,
    deleteScenarioApiV1SakinahScenariosScenarioIdDelete,
    listSakinahRunsApiV1SakinahRunsGet,
    listScenariosApiV1SakinahScenariosGet,
    previewBulkScenariosApiV1SakinahScenariosBulkImportPreviewPost,
    type ScenarioResponse as ApiScenarioResponse,
    updateScenarioApiV1SakinahScenariosScenarioIdPut,
} from "@/client";

import {
    parseStoredScenarios,
    type Scenario,
    type ScenarioDraft,
    scenarioToDraft,
} from "./sakinahScenarios";

export type BulkScenarioDuplicatePolicy = "skip_existing" | "replace_existing" | "import_as_new";

export interface BulkScenarioImportItem {
    item_index: number;
    source_filename: string;
    scenario_title: string | null;
    status: string;
    validation_status: string;
    validation_error: string | null;
    existing_scenario_id: string | null;
    scenario_id: string | null;
}

export interface BulkScenarioImportResponse {
    preview_token: string | null;
    files_detected: number;
    valid: number;
    invalid: number;
    duplicates: number;
    already_existing: number;
    imported: number;
    failed: number;
    items: BulkScenarioImportItem[];
}

type ScenarioApiResponse = ApiScenarioResponse;

export interface PersistedSakinahRun {
    session_id: string;
    agent_id: number;
    run_id: number;
    service_user_agent_id?: number | null;
    service_user_run_id?: number | null;
    scenario: string;
    status: string;
    experiment_mode?: string | null;
    transcript?: string | null;
    transcript_url?: string | null;
    conversation: Array<Record<string, unknown>>;
    preview_data: Record<string, unknown>;
    recording_url?: string | null;
    recording_file_reference: Record<string, unknown>;
    started_at: string;
    ended_at?: string | null;
    calm_turns: Array<Record<string, unknown>>;
    timings: Record<string, unknown>;
    created_at: string;
}

function requestError(error: unknown, fallback: string): Error {
    if (error && typeof error === "object" && "detail" in error) {
        const detail = (error as { detail?: unknown }).detail;
        if (typeof detail === "string") return new Error(detail);
    }
    return new Error(fallback);
}

function normalizeScenario(scenario: ScenarioApiResponse): Scenario {
    return {
        id: scenario.id,
        sequence: scenario.sequence,
        title: scenario.title ?? "",
        category: scenario.category ?? "",
        tags: scenario.tags ?? [],
        mode: scenario.mode ?? "structured",
        persona: scenario.persona ?? "",
        age: scenario.age ?? "",
        gender: scenario.gender ?? "",
        language: scenario.language ?? "",
        emotion: scenario.emotion ?? "",
        communicationStyle: scenario.communication_style ?? "",
        initialInformation: scenario.initial_information ?? "",
        hiddenInformation: scenario.hidden_information ?? "",
        disclosure: scenario.disclosure ?? "",
        behaviour: scenario.behaviour ?? "",
        background: scenario.background ?? "",
        additionalFactors: scenario.additional_factors ?? "",
        notes: scenario.notes ?? "",
        freestylePrompt: scenario.freestyle_prompt ?? "",
        createdAt: scenario.created_at,
        updatedAt: scenario.updated_at,
    };
}

function normalizeBulkImportResponse(
    response: ApiBulkScenarioImportResponse,
): BulkScenarioImportResponse {
    return {
        preview_token: response.preview_token ?? null,
        files_detected: response.files_detected,
        valid: response.valid,
        invalid: response.invalid,
        duplicates: response.duplicates,
        already_existing: response.already_existing,
        imported: response.imported ?? 0,
        failed: response.failed ?? 0,
        items: response.items.map((item) => ({
            item_index: item.item_index,
            source_filename: item.source_filename,
            scenario_title: item.scenario_title ?? null,
            status: item.status,
            validation_status: item.validation_status,
            validation_error: item.validation_error ?? null,
            existing_scenario_id: item.existing_scenario_id ?? null,
            scenario_id: item.scenario_id ?? null,
        })),
    };
}

function draftPayload(draft: ScenarioDraft) {
    return {
        title: draft.title,
        category: draft.category,
        tags: draft.tags,
        mode: draft.mode,
        persona: draft.persona,
        age: draft.age,
        gender: draft.gender,
        language: draft.language,
        emotion: draft.emotion,
        communication_style: draft.communicationStyle,
        initial_information: draft.initialInformation,
        hidden_information: draft.hiddenInformation,
        disclosure: draft.disclosure,
        behaviour: draft.behaviour,
        background: draft.background,
        additional_factors: draft.additionalFactors,
        notes: draft.notes,
        freestyle_prompt: draft.freestylePrompt,
    };
}

async function createScenarioOnServer(draft: ScenarioDraft): Promise<Scenario> {
    const response = await createScenarioApiV1SakinahScenariosPost({
        body: draftPayload(draft),
    });
    if (response.error || !response.data) throw requestError(response.error, "Unable to save scenario.");
    return normalizeScenario(response.data);
}

export async function listSakinahScenarios(search = ""): Promise<Scenario[]> {
    const response = await listScenariosApiV1SakinahScenariosGet({
        query: search.trim() ? { search: search.trim() } : undefined,
    });
    if (response.error || !response.data) throw requestError(response.error, "Unable to load scenarios.");
    return response.data.scenarios.map(normalizeScenario);
}

export async function previewBulkSakinahScenarios(
    files: File[],
): Promise<BulkScenarioImportResponse> {
    const response = await previewBulkScenariosApiV1SakinahScenariosBulkImportPreviewPost({
        body: { files },
    });
    if (response.error || !response.data) {
        throw requestError(response.error, "Unable to preview the scenario import.");
    }
    return normalizeBulkImportResponse(response.data);
}

export async function commitBulkSakinahScenarios(
    previewToken: string,
    duplicatePolicy: BulkScenarioDuplicatePolicy,
    itemIndexes?: number[],
): Promise<BulkScenarioImportResponse> {
    const response = await commitBulkScenariosApiV1SakinahScenariosBulkImportCommitPost({
        body: {
            preview_token: previewToken,
            duplicate_policy: duplicatePolicy,
            item_indexes: itemIndexes,
        },
    });
    if (response.error || !response.data) {
        throw requestError(response.error, "Unable to import the scenarios.");
    }
    return normalizeBulkImportResponse(response.data);
}

export async function saveSakinahScenario(
    scenario: Scenario | null,
    draft: ScenarioDraft,
): Promise<Scenario> {
    if (!scenario) return createScenarioOnServer(draft);
    const response = await updateScenarioApiV1SakinahScenariosScenarioIdPut({
        path: { scenario_id: scenario.id },
        body: draftPayload(draft),
    });
    if (response.error || !response.data) throw requestError(response.error, "Unable to update scenario.");
    return normalizeScenario(response.data);
}

export async function deleteSakinahScenario(scenarioId: string): Promise<void> {
    const response = await deleteScenarioApiV1SakinahScenariosScenarioIdDelete({
        path: { scenario_id: scenarioId },
    });
    if (response.error) throw requestError(response.error, "Unable to delete scenario.");
}

export async function migrateLegacySakinahScenarios(
    scenarios: Scenario[],
): Promise<Scenario[]> {
    const saved: Scenario[] = [];
    for (const scenario of scenarios) {
        saved.push(await createScenarioOnServer(scenarioToDraft(scenario)));
    }
    if (typeof window !== "undefined") {
        window.localStorage.removeItem("calmos.sakinah.scenario-library.v1");
        window.localStorage.removeItem("calmos.sakinah.scenario-library.v1.initialized");
    }
    return saved;
}

export async function loadSakinahScenariosWithLegacyMigration(search = ""): Promise<Scenario[]> {
    const serverScenarios = await listSakinahScenarios(search);
    if (search.trim()) return serverScenarios;
    if (serverScenarios.length > 0 || typeof window === "undefined") return serverScenarios;
    const legacy = parseStoredScenarios(window.localStorage.getItem("calmos.sakinah.scenario-library.v1"));
    if (legacy.length > 0) return migrateLegacySakinahScenarios(legacy);
    return serverScenarios;
}

export async function listSakinahRuns(): Promise<PersistedSakinahRun[]> {
    const response = await listSakinahRunsApiV1SakinahRunsGet();
    if (response.error || !response.data) throw requestError(response.error, "Unable to load runs.");
    return response.data.runs;
}
