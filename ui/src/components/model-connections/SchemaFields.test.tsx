import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { cleanSettings } from "./configuration";
import { SchemaFields } from "./SchemaFields";
import { selectOption, selectOptions } from "./test-helpers";
import type { FieldSchema } from "./types";

function Editor({ schema, initial, onSave = vi.fn() }: { schema: FieldSchema; initial: Record<string, unknown>; onSave?: (values: Record<string, unknown>) => void }) {
    const [values, setValues] = useState(initial);
    return <form onSubmit={event => { event.preventDefault(); onSave(cleanSettings(values, schema)); }}>
        <SchemaFields schema={schema} values={values} onChange={(name, value) => setValues(previous => ({ ...previous, [name]: value }))} />
        <button type="submit">Save</button>
    </form>;
}

describe("provider field controls", () => {
    it("requires a replacement credential but permits an empty extra pool row", () => {
        render(<SchemaFields secret configuredFields={["api_key"]} values={{}} onChange={vi.fn()} schema={{ required: ["api_key"], properties: { api_key: { title: "API key", anyOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] } } }} />);
        fireEvent.click(screen.getByRole("button", { name: "Replace" }));
        expect((screen.getByLabelText("API key") as HTMLInputElement).required).toBe(true);
    });

    it("allows trailing empty rows for required string arrays", () => {
        render(<Editor schema={{ required: ["keyterms"], properties: { keyterms: { type: "array", title: "Keyterms", items: { type: "string" } } } }} initial={{ keyterms: ["Dograh", ""] }} />);
        expect((screen.getByLabelText("Keyterms") as HTMLInputElement).required).toBe(true);
        expect((screen.getByLabelText("keyterms 2") as HTMLInputElement).required).toBe(false);
    });
    it("restores Flux language hint checkboxes, readable labels, and model-dependent visibility", () => {
        const onSave = vi.fn();
        const schema: FieldSchema = { properties: {
            model: { type: "string", title: "Model", examples: ["flux-general-multi", "flux-general-en"] },
            language_hints: { type: "array", title: "Language hints", items: { type: "string" }, examples: ["en", "es", "fr"], visible_for_models: ["flux-general-multi"] },
        } };
        render(<Editor schema={schema} initial={{ model: "flux-general-multi", language_hints: ["es"] }} onSave={onSave} />);
        expect(screen.getByRole("checkbox", { name: "Spanish" }).getAttribute("aria-checked")).toBe("true");
        fireEvent.click(screen.getByRole("checkbox", { name: "French" }));
        fireEvent.click(screen.getByRole("checkbox", { name: "Spanish" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model: "flux-general-multi", language_hints: ["fr"] });
        selectOption("Model", "flux-general-en");
        expect(screen.queryByRole("checkbox", { name: "French" })).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model: "flux-general-en" });
    });

    it("uses styled model choices and preserves custom values until the user chooses a listed value", () => {
        const onSave = vi.fn();
        render(<Editor schema={{ properties: { model: { type: "string", title: "Model", examples: ["standard", "fast"], allow_custom_input: true } } }} initial={{ model: "my-custom-model" }} onSave={onSave} />);
        expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("my-custom-model");
        fireEvent.click(screen.getByLabelText("Enter Custom Value"));
        expect(screen.getByLabelText("Model").getAttribute("data-slot")).toBe("select-trigger");
        expect(selectOptions("Model")).toEqual(["standard", "fast"]);
        selectOption("Model", "fast");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model: "fast" });
        fireEvent.click(screen.getByLabelText("Enter Custom Value"));
        fireEvent.change(screen.getByLabelText("Model"), { target: { value: "custom-again" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model: "custom-again" });
    });

    it("offers numeric enum values as labelled choices and saves them as numbers", () => {
        const onSave = vi.fn();
        const schema: FieldSchema = { properties: {
            expressivity: { title: "Expressivity", default: null, anyOf: [{ enum: [-2, -1, 0, 1, 2], type: "integer" }, { type: "null" }], option_labels: { "-2": "-2 (calm)", "2": "2 (animated)" } },
        } };
        render(<Editor schema={schema} initial={{}} onSave={onSave} />);
        expect(screen.getByLabelText("Expressivity").getAttribute("data-slot")).toBe("select-trigger");
        expect(selectOptions("Expressivity")).toEqual(["Provider default", "-2 (calm)", "-1", "0", "1", "2 (animated)"]);
        selectOption("Expressivity", "1");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ expressivity: 1 });
    });
    it("drops manual entry on a dependent field when what it depends on changes", () => {
        const schema: FieldSchema = { properties: {
            model: { type: "string", title: "Model", examples: ["aura-2", "flux"] },
            voice: { type: "string", title: "Voice", examples: ["v1", "v2"], allow_custom_input: true, reset_on_change: ["model"] },
        } };
        render(<Editor schema={schema} initial={{ model: "aura-2", voice: "v1" }} />);
        fireEvent.click(screen.getByLabelText("Enter Custom Value"));
        expect(screen.getByLabelText("Voice").getAttribute("data-slot")).not.toBe("select-trigger");
        selectOption("Model", "flux");
        expect(screen.getByLabelText("Voice").getAttribute("data-slot")).toBe("select-trigger");
    });
    it("shows model-specific language options with display names and documentation", () => {
        render(<Editor schema={{ properties: {
            model: { type: "string", title: "Model", examples: ["flux", "nova"] },
            language: { type: "string", title: "Language", examples: ["en", "es", "fr"], model_options: { flux: ["en", "es"] }, docs_url: "https://developers.deepgram.com/docs/flux-multilingual", docs_label: "Supported languages" },
        } }} initial={{ model: "flux", language: "en" }} />);
        expect(selectOptions("Language")).toEqual(["English", "Spanish"]);
        selectOption("Model", "nova");
        expect(selectOptions("Language")).toEqual(["English", "Spanish", "French"]);
        expect(screen.getByRole("link", { name: "Supported languages" }).getAttribute("href")).toContain("/flux-multilingual");
    });

    it.each(["eleven_v3_custom", "eleven_v4_custom"])("omits unsupported speed for custom dialogue model %s", model => {
        const onSave = vi.fn();
        render(<Editor schema={{ properties: {
            model: { type: "string", title: "Model" },
            speed: { type: "number", title: "Speed", model_constraints: [{ pattern: "^eleven_v[34]", supported: false }] },
        } }} initial={{ model, speed: 1.2 }} onSave={onSave} />);
        expect(screen.queryByLabelText("Speed")).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model });

        fireEvent.change(screen.getByLabelText("Model"), { target: { value: "eleven_flash_v2_5" } });
        expect((screen.getByLabelText("Speed") as HTMLInputElement).value).toBe("1.2");
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenLastCalledWith({ model: "eleven_flash_v2_5", speed: 1.2 });
    });

    it("uses removable input rows for free-form lists", () => {
        const onSave = vi.fn();
        render(<Editor schema={{ properties: { keyterms: { type: "array", title: "Keyterms", items: { type: "string" } } } }} initial={{ keyterms: ["Dograh"] }} onSave={onSave} />);
        fireEvent.click(screen.getByRole("button", { name: "Add keyterms" }));
        fireEvent.change(screen.getByLabelText("keyterms 2"), { target: { value: "Flux" } });
        fireEvent.click(screen.getByRole("button", { name: "Remove keyterms 1" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        expect(onSave).toHaveBeenCalledWith({ keyterms: ["Flux"] });
    });
});
