import { fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

const methods = ["scrollIntoView", "hasPointerCapture", "releasePointerCapture"] as const;
let originalMethods: (PropertyDescriptor | undefined)[];

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    originalMethods = methods.map(name => Object.getOwnPropertyDescriptor(Element.prototype, name));
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
    vi.unstubAllGlobals();
    methods.forEach((name, index) => {
        const descriptor = originalMethods[index];
        if (descriptor) Object.defineProperty(Element.prototype, name, descriptor);
        else Reflect.deleteProperty(Element.prototype, name);
    });
});

export function selectOption(label: string | HTMLElement, option: string | RegExp) {
    fireEvent.keyDown(typeof label === "string" ? screen.getByLabelText(label) : label, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: option }));
}

export function selectOptions(label: string | HTMLElement) {
    fireEvent.keyDown(typeof label === "string" ? screen.getByLabelText(label) : label, { key: "ArrowDown" });
    const options = screen.queryAllByRole("option").map(option => option.textContent);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    return options;
}
