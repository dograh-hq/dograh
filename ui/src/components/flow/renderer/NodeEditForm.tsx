import { useCallback } from "react";

import type { NodeSpec } from "@/client/types.gen";

import { evaluateDisplayOptions } from "./displayOptions";
import { PropertyInput, type RendererContext } from "./PropertyInput";

export interface NodeEditFormProps {
    spec: NodeSpec;
    /** Current form values keyed by property name. */
    values: Record<string, unknown>;
    onChange: (next: Record<string, unknown>) => void;
    context: RendererContext;
}

/**
 * Generic node-edit form. Walks `spec.properties` once, evaluates each
 * property's `display_options` against current values, and renders the
 * visible properties through `<PropertyInput>`.
 *
 * Wire format compatibility: form `values` are flat (matching the wire
 * format), so `display_options` references work directly. Sub-objects from
 * grouped fields (e.g. `pre_call_fetch`) live as separate flat fields here.
 */
export function NodeEditForm({ spec, values, onChange, context }: NodeEditFormProps) {
    const setProp = useCallback(
        (propName: string, propValue: unknown) => {
            onChange({ ...values, [propName]: propValue });
        },
        [values, onChange],
    );

    return (
        <div className="grid gap-3">
            {spec.properties
                .filter((p) => evaluateDisplayOptions(p.display_options, values))
                .map((p) => (
                    <PropertyInput
                        key={p.name}
                        spec={p}
                        value={values[p.name]}
                        onChange={(v) => setProp(p.name, v)}
                        context={context}
                    />
                ))}
        </div>
    );
}
