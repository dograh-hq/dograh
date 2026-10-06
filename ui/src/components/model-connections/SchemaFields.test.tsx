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
