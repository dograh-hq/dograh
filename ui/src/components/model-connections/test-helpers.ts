import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, vi } from "vitest";

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.releasePointerCapture = vi.fn();
});

export function selectOption(label: string, option: string | RegExp) {
    fireEvent.keyDown(screen.getByLabelText(label), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: option }));
}

export function selectOptions(label: string) {
    fireEvent.keyDown(screen.getByLabelText(label), { key: "ArrowDown" });
    const options = screen.queryAllByRole("option").map(option => option.textContent);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    return options;
}
