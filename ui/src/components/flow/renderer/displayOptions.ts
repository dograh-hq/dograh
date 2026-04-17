// Structural type — kept local so this module has no dependencies and
// the parity-test script (`ui/scripts/test-display-options.mts`) can
// import it directly via tsx without alias resolution. The generated
// `DisplayOptions` from `@/client/types.gen` is structurally identical.
export type DisplayOptionsRule = {
    show?: Record<string, unknown[]> | null;
    hide?: Record<string, unknown[]> | null;
};

/**
 * Evaluate a `display_options` rule against the current form values.
 *
 * `show` keys are AND-combined: visible only when EVERY referenced field's
 * value matches one of the listed allowed values.
 *
 * `hide` keys are OR-combined: hidden when ANY referenced field's value
 * matches one of the listed values.
 *
 * Mirror of `display_options` semantics in the Python NodeSpec. The
 * golden-test suite locks the two implementations together.
 */
export function evaluateDisplayOptions(
    rules: DisplayOptionsRule | null | undefined,
    values: Record<string, unknown>,
): boolean {
    if (!rules) return true;

    if (rules.show) {
        for (const [field, allowed] of Object.entries(rules.show)) {
            const v = values[field];
            if (!allowed?.some((a) => isEqual(a, v))) return false;
        }
    }

    if (rules.hide) {
        for (const [field, hidden] of Object.entries(rules.hide)) {
            const v = values[field];
            if (hidden?.some((h) => isEqual(h, v))) return false;
        }
    }

    return true;
}

// Strict equality matches the Python `==` semantics used by the backend
// evaluator. Both implementations only compare scalar values
// (string|number|boolean|null) — anything richer is a spec authoring error.
function isEqual(a: unknown, b: unknown): boolean {
    return a === b;
}
