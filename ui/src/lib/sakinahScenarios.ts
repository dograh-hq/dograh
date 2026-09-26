export const SCENARIO_STORAGE_KEY = "calmos.sakinah.scenario-library.v1";
export const SCENARIO_INITIALIZED_KEY = `${SCENARIO_STORAGE_KEY}.initialized`;

export type ScenarioMode = "structured" | "freestyle";

export interface Scenario {
    id: string;
    sequence: number;
    title: string;
    category: string;
    tags: string[];
    mode: ScenarioMode;
    persona: string;
    age: string;
    gender: string;
    language: string;
    emotion: string;
    communicationStyle: string;
    initialInformation: string;
    hiddenInformation: string;
    disclosure: string;
    behaviour: string;
    background: string;
    additionalFactors: string;
    notes: string;
    freestylePrompt: string;
    createdAt: string;
    updatedAt: string;
}

export type ScenarioDraft = Omit<Scenario, "id" | "sequence" | "createdAt" | "updatedAt">;

interface ScenarioStorage {
    getItem(key: string): string | null;
    setItem(key: string, value: string): void;
}

export const EMPTY_SCENARIO_DRAFT: ScenarioDraft = {
    title: "",
    category: "",
    tags: [],
    mode: "structured",
    persona: "",
    age: "",
    gender: "Not specified",
    language: "English",
    emotion: "",
    communicationStyle: "",
    initialInformation: "",
    hiddenInformation: "",
    disclosure: "",
    behaviour: "",
    background: "",
    additionalFactors: "",
    notes: "",
    freestylePrompt: "",
};

const SEED_DRAFT: ScenarioDraft = {
    ...EMPTY_SCENARIO_DRAFT,
    title: "Guarded service user",
    persona: "A service user speaking with Sakinah who is initially cautious about discussing personal difficulties.",
    gender: "Not specified",
    language: "English",
    emotion: "Guarded / anxious",
    initialInformation: "The service user gives only limited information at the beginning of the conversation.",
    hiddenInformation: "Important personal concerns are not volunteered initially.",
    disclosure: "Further information should emerge gradually when the service user feels heard, understood, and safe.",
    behaviour: "Initially guarded.\n\nIf Sakinah responds empathically:\nBecome more open.\n\nIf Sakinah repeatedly uses checklist questions:\nBecome irritated and withdraw.",
};

function createId(): string {
    return globalThis.crypto?.randomUUID?.() ??
        `scenario-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isScenario(value: unknown): value is Scenario {
    if (!value || typeof value !== "object") return false;
    const candidate = value as Record<string, unknown>;
    const stringFields: Array<keyof Scenario> = [
        "id", "title", "persona", "age", "gender", "language", "emotion",
        "communicationStyle", "initialInformation", "hiddenInformation",
        "disclosure", "behaviour", "background", "additionalFactors", "notes",
        "freestylePrompt", "createdAt", "updatedAt",
    ];
    return stringFields.every((field) => typeof candidate[field] === "string") &&
        (candidate.mode === "structured" || candidate.mode === "freestyle") &&
        typeof candidate.sequence === "number" &&
        Number.isInteger(candidate.sequence) && candidate.sequence > 0;
}

export function parseStoredScenarios(raw: string | null): Scenario[] {
    if (!raw) return [];
    try {
        const parsed: unknown = JSON.parse(raw);
        return Array.isArray(parsed)
            ? parsed.filter(isScenario).map((scenario) => ({
                ...scenario,
                category: typeof scenario.category === "string" ? scenario.category : "",
                tags: Array.isArray(scenario.tags) ? scenario.tags.filter((tag) => typeof tag === "string") : [],
            }))
            : [];
    } catch {
        return [];
    }
}

export function nextScenarioSequence(scenarios: readonly Scenario[]): number {
    return scenarios.reduce((maximum, scenario) => Math.max(maximum, scenario.sequence), 0) + 1;
}

export function formatScenarioNumber(sequence: number): string {
    return `Scenario ${String(sequence).padStart(3, "0")}`;
}

export function scenarioMatchesSearch(scenario: Scenario, search: string): boolean {
    const term = search.trim().toLocaleLowerCase();
    if (!term) return true;
    return [
        scenario.id,
        scenario.title,
        scenario.category,
        scenario.persona,
        scenario.language,
        scenario.communicationStyle,
        scenario.initialInformation,
        scenario.hiddenInformation,
        scenario.disclosure,
        scenario.behaviour,
        scenario.background,
        scenario.additionalFactors,
        scenario.notes,
        scenario.freestylePrompt,
        ...scenario.tags,
    ].some((value) => value.toLocaleLowerCase().includes(term));
}

export function filterSakinahScenarios(scenarios: readonly Scenario[], search: string): Scenario[] {
    return scenarios.filter((scenario) => scenarioMatchesSearch(scenario, search));
}

export function createScenario(
    draft: ScenarioDraft,
    scenarios: readonly Scenario[],
    now = new Date().toISOString(),
): Scenario {
    return {
        ...draft,
        id: createId(),
        sequence: nextScenarioSequence(scenarios),
        createdAt: now,
        updatedAt: now,
    };
}

export function scenarioToDraft(scenario: Scenario): ScenarioDraft {
    return {
        title: scenario.title,
        category: scenario.category,
        tags: [...scenario.tags],
        mode: scenario.mode,
        persona: scenario.persona,
        age: scenario.age,
        gender: scenario.gender,
        language: scenario.language,
        emotion: scenario.emotion,
        communicationStyle: scenario.communicationStyle,
        initialInformation: scenario.initialInformation,
        hiddenInformation: scenario.hiddenInformation,
        disclosure: scenario.disclosure,
        behaviour: scenario.behaviour,
        background: scenario.background,
        additionalFactors: scenario.additionalFactors,
        notes: scenario.notes,
        freestylePrompt: scenario.freestylePrompt,
    };
}

export function duplicateScenario(
    original: Scenario,
    scenarios: readonly Scenario[],
    now = new Date().toISOString(),
): Scenario {
    const draft = scenarioToDraft(original);
    return createScenario({ ...draft, title: `Copy of ${original.title}` }, scenarios, now);
}

export function compileScenarioPrompt(scenario: Scenario): string {
    if (scenario.mode === "freestyle") return scenario.freestylePrompt.trim();
    const singleLineFields: Array<[string, string]> = [
        ["Scenario", scenario.title], ["Persona", scenario.persona], ["Age", scenario.age],
        ["Gender", scenario.gender], ["Language", scenario.language], ["Emotion", scenario.emotion],
        ["Communication style", scenario.communicationStyle],
    ];
    const longFields: Array<[string, string]> = [
        ["Initial information", scenario.initialInformation],
        ["Hidden information", scenario.hiddenInformation], ["Disclosure", scenario.disclosure],
        ["Behaviour", scenario.behaviour], ["Background", scenario.background],
        ["Additional factors", scenario.additionalFactors], ["Notes", scenario.notes],
    ];
    return [
        ...singleLineFields.filter(([, value]) => value.trim()).map(([label, value]) => `${label}: ${value.trim()}`),
        ...longFields.filter(([, value]) => value.trim()).map(([label, value]) => `${label}:\n${value.trim()}`),
    ].join("\n\n");
}

function importedDraft(value: unknown, index: number): ScenarioDraft {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
        throw new Error(`Scenario ${index + 1} must be a JSON object.`);
    }
    const candidate = value as Record<string, unknown>;
    const stringValue = (names: string | string[], fallback = ""): string => {
        const aliases = Array.isArray(names) ? names : [names];
        const name = aliases.find((alias) => candidate[alias] !== undefined);
        if (!name) return fallback;
        const raw = candidate[name];
        if (raw === null) return fallback;
        if (typeof raw !== "string") {
            throw new Error(`Scenario ${index + 1} field "${name}" must be a string.`);
        }
        return raw;
    };
    const emotionalState = candidate.emotional_state ?? candidate.emotionalState;
    if (emotionalState !== undefined &&
        (!emotionalState || typeof emotionalState !== "object" || Array.isArray(emotionalState))) {
        throw new Error(`Scenario ${index + 1} field "emotional_state" must be an object.`);
    }
    const emotionalStateRecord = emotionalState as Record<string, unknown> | undefined;
    const emotionalStateCategory = emotionalStateRecord?.category;
    const emotionalStateDescription = emotionalStateRecord?.description;
    if (emotionalStateCategory !== undefined && typeof emotionalStateCategory !== "string") {
        throw new Error(`Scenario ${index + 1} field "emotional_state.category" must be a string.`);
    }
    if (emotionalStateDescription !== undefined && typeof emotionalStateDescription !== "string") {
        throw new Error(`Scenario ${index + 1} field "emotional_state.description" must be a string.`);
    }
    const freestylePrompt = stringValue(["freestylePrompt", "prompt", "instructions"]);
    const rawMode = candidate.mode;
    const mode = rawMode === undefined
        ? (freestylePrompt.trim() ? "freestyle" : "structured")
        : rawMode;
    if (mode !== "structured" && mode !== "freestyle") {
        throw new Error(`Scenario ${index + 1} mode must be structured or freestyle.`);
    }
    const draft: ScenarioDraft = {
        ...EMPTY_SCENARIO_DRAFT,
        title: stringValue(["title", "scenario_title"], freestylePrompt.trim() ? "Imported scenario" : ""),
        category: stringValue("category"),
        tags: Array.isArray(candidate.tags) ? candidate.tags.filter((tag): tag is string => typeof tag === "string") : [],
        mode,
        persona: stringValue("persona"),
        age: stringValue(["age", "service_user_age"]),
        gender: stringValue("gender", EMPTY_SCENARIO_DRAFT.gender),
        language: stringValue("language", EMPTY_SCENARIO_DRAFT.language),
        emotion: stringValue("emotion", typeof emotionalStateCategory === "string" ? emotionalStateCategory : ""),
        communicationStyle: stringValue(["communicationStyle", "communication_style"]),
        initialInformation: stringValue(["initialInformation", "initial_information"]),
        hiddenInformation: stringValue(["hiddenInformation", "hidden_information"]),
        disclosure: stringValue("disclosure"),
        behaviour: stringValue("behaviour"),
        background: stringValue(["background", "background_context"]),
        additionalFactors: stringValue(["additionalFactors", "additional_factors"], typeof emotionalStateDescription === "string" ? `Emotional state detail: ${emotionalStateDescription}` : ""),
        notes: stringValue(["notes", "optional_free_notes"]),
        freestylePrompt,
    };
    if (mode === "freestyle" && !draft.freestylePrompt.trim()) {
        throw new Error(`Scenario ${index + 1} needs a freestylePrompt, prompt, or instructions field.`);
    }
    if (mode === "structured" && (!draft.title.trim() || !draft.persona.trim() || !draft.behaviour.trim())) {
        throw new Error(`Scenario ${index + 1} needs title, persona, and behaviour fields.`);
    }
    return draft;
}

export function parseScenarioImport(
    raw: string,
    existing: readonly Scenario[] = [],
    now = new Date().toISOString(),
): Scenario[] {
    const trimmed = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
    if (!trimmed) throw new Error("The import file is empty.");
    let parsed: unknown;
    try {
        parsed = JSON.parse(trimmed);
    } catch {
        try {
            parsed = trimmed.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
        } catch {
            throw new Error("Import must contain valid JSON, a JSON array, or newline-delimited JSON objects.");
        }
    }
    const candidates = Array.isArray(parsed)
        ? parsed
        : parsed && typeof parsed === "object" && Array.isArray((parsed as Record<string, unknown>).scenarios)
            ? (parsed as { scenarios: unknown[] }).scenarios
            : [parsed];
    if (!candidates.length) throw new Error("The import does not contain any scenarios.");
    const imported: Scenario[] = [];
    candidates.forEach((candidate, index) => {
        imported.push(createScenario(importedDraft(candidate, index), [...existing, ...imported], now));
    });
    return imported;
}

export function loadScenarios(storage: ScenarioStorage): Scenario[] {
    if (storage.getItem(SCENARIO_INITIALIZED_KEY) === "true") {
        return parseStoredScenarios(storage.getItem(SCENARIO_STORAGE_KEY));
    }
    const seed = createScenario(SEED_DRAFT, []);
    storage.setItem(SCENARIO_STORAGE_KEY, JSON.stringify([seed]));
    storage.setItem(SCENARIO_INITIALIZED_KEY, "true");
    return [seed];
}

export function saveScenarios(storage: ScenarioStorage, scenarios: readonly Scenario[]): void {
    storage.setItem(SCENARIO_STORAGE_KEY, JSON.stringify(scenarios));
    storage.setItem(SCENARIO_INITIALIZED_KEY, "true");
}

export function findScenario(storage: ScenarioStorage, id: string): Scenario | undefined {
    return loadScenarios(storage).find((scenario) => scenario.id === id);
}
