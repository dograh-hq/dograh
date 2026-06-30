import { useCallback } from "react";

import type { NodeSpec } from "@/client/types.gen";

import { evaluateDisplayOptions } from "./displayOptions";
import { PropertyInput, type RendererContext } from "./PropertyInput";
import { getPropertyColumnSpan } from "./propertyRendererOptions";

export interface NodeEditFormProps {
    spec: NodeSpec;
    /** Current form values keyed by property name. */
    values: Record<string, unknown>;
    onChange: (next: Record<string, unknown>) => void;
    context: RendererContext;
}

const COLUMN_SPAN_CLASS: Record<number, string> = {
    1: "sm:col-span-1",
    2: "sm:col-span-2",
    3: "sm:col-span-3",
    4: "sm:col-span-4",
    5: "sm:col-span-5",
    6: "sm:col-span-6",
    7: "sm:col-span-7",
    8: "sm:col-span-8",
    9: "sm:col-span-9",
    10: "sm:col-span-10",
    11: "sm:col-span-11",
    12: "sm:col-span-12",
};

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
        <div className="grid grid-cols-12 gap-3">
            {spec.properties
                .filter((p) => evaluateDisplayOptions(p.display_options, values))
                .map((p) => {
                    const columnSpan = getPropertyColumnSpan(p.renderer_options);
                    return (
                        <div
                            key={p.name}
                            className={`col-span-12 ${COLUMN_SPAN_CLASS[columnSpan]}`}
                        >
                            <PropertyInput
                                spec={p}
                                value={values[p.name]}
                                onChange={(v) => setProp(p.name, v)}
                                context={context}
                            />
                        </div>
                    );
                })}
        </div>
    );
}
