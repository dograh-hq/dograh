import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LocaleProvider } from "@/context/LocaleContext";

import { LocaleSelector } from "./LocaleSelector";

describe("LocaleSelector", () => {
  it("switches the selected language to Portuguese", async () => {
    render(
      <LocaleProvider>
        <LocaleSelector />
      </LocaleProvider>,
    );

    fireEvent.pointerDown(screen.getByRole("button", { name: /language/i }));
    fireEvent.click(
      await screen.findByRole("menuitemradio", { name: /portugues/i }),
    );

    expect(screen.getByRole("button", { name: /pt-br/i })).toBeTruthy();
  });
});
