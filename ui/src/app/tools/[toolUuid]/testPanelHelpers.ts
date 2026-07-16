import type { ParameterType } from "@/components/http";

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
