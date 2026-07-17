import type { HttpMethod, KeyValueItem, ParameterType, PresetToolParameter, ToolParameter } from "@/components/http";

const TYPE_SAMPLE_VALUES: Record<ParameterType, string> = {
    string: "sample_text",
    number: "5",
    boolean: "true",
    array: "[]",
    object: "{}",
};

/**
 * Type-based sample value used to pre-fill test-panel inputs. No
 * name/description heuristic — explicit user choice to keep this simple.
 */
export function generateSampleValue(type: ParameterType): string {
    return TYPE_SAMPLE_VALUES[type];
}

export type HttpToolTestSnapshotFields = {
    httpMethod: HttpMethod;
    url: string;
    credentialUuid: string;
    headers: KeyValueItem[];
    parameters: ToolParameter[];
    presetParameters: PresetToolParameter[];
    timeoutMs: number;
};

/**
 * Canonical string for the HTTP API config fields that affect what "Test
 * Tool" actually runs. Compared against the last-saved snapshot to detect
 * unsaved changes, since Test Tool always runs the saved config, not the
 * live form state.
 */
export function buildHttpToolTestSnapshot(fields: HttpToolTestSnapshotFields): string {
    // Normalize headers to a deduped key→value map (last key wins), matching
    // the shape the save request sends. Raw KeyValueItem[] with duplicate keys
    // would produce a snapshot that diverges from the saved state.
    const normalizedHeaders = Object.fromEntries(
        fields.headers.filter((h) => h.key).map((h) => [h.key, h.value])
    );
    return JSON.stringify({ ...fields, headers: normalizedHeaders });
}
