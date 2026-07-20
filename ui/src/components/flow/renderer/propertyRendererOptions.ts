import type { PropertyRendererOptions } from "@/client/types.gen";

export function getPropertyColumnSpan(
    rendererOptions: PropertyRendererOptions | null | undefined,
): number {
    const value = rendererOptions?.layout?.column_span;
    if (typeof value !== "number" || !Number.isFinite(value)) {
        return 12;
    }
    return Math.min(Math.max(Math.trunc(value), 1), 12);
}

export function isFractionalNumberInput(
    rendererOptions: PropertyRendererOptions | null | undefined,
): boolean {
    return rendererOptions?.number_input?.fractional === true;
}
