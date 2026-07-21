import type { HttpMethod, KeyValueItem, ParameterType, PresetToolParameter, ToolParameter } from "@/components/http";

const TYPE_SAMPLE_VALUES: Record<ParameterType, string> = {
    string: "sample_text",
    number: "5",
    boolean: "true",
    array: "[]",
    object: "{}",
};

/**
 * Type-based sample value used to pre-fill test-dialog inputs. Parameters do
 * not currently include a schema, so objects and arrays can only be seeded
 * with valid empty JSON containers.
 */
export function generateSampleValue(type: ParameterType): string {
    return TYPE_SAMPLE_VALUES[type];
}

/** Whether testing this method may change state on the external service. */
export function isUnsafeHttpMethod(method: HttpMethod): boolean {
    return method !== "GET";
}

export function parseTestParameterValues(
    parameters: Array<Pick<ToolParameter, "name" | "type" | "required">>,
    values: Record<string, string>
): Record<string, unknown> {
    const parsedValues: Record<string, unknown> = {};

    for (const parameter of parameters) {
        const rawValue = values[parameter.name];
        if (rawValue === undefined || rawValue === "") {
            if (parameter.required) throw new Error(`${parameter.name}: value is required`);
            continue;
        }

        if (parameter.type === "number") {
            parsedValues[parameter.name] = Number(rawValue);
        } else if (parameter.type === "boolean") {
            parsedValues[parameter.name] = rawValue === "true";
        } else if (parameter.type === "object" || parameter.type === "array") {
            try {
                parsedValues[parameter.name] = JSON.parse(rawValue);
            } catch {
                throw new Error(`${parameter.name}: invalid JSON`);
            }
        } else {
            parsedValues[parameter.name] = rawValue;
        }
    }

    return parsedValues;
}

export type HttpToolTestSnapshotFields = {
    name: string;
    description: string;
    httpMethod: HttpMethod;
    url: string;
    credentialUuid: string;
    headers: KeyValueItem[];
    parameters: ToolParameter[];
    presetParameters: PresetToolParameter[];
    timeoutMs: number;
    customMessage: string;
    customMessageType: "text" | "audio";
    customMessageRecordingId: string;
};

/**
 * Canonical string for HTTP API fields that affect the saved configuration
 * used by Test Tool.
 */
export function buildHttpToolTestSnapshot(fields: HttpToolTestSnapshotFields): string {
    const normalizedHeaders = Object.fromEntries(
        fields.headers.filter((header) => header.key).map((header) => [header.key, header.value])
    );
    return JSON.stringify({ ...fields, headers: normalizedHeaders });
}
