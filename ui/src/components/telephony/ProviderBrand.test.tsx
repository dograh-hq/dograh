import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProviderBrand } from "./ProviderBrand";

describe("ProviderBrand", () => {
  it("renders branding supplied by provider metadata", () => {
    const { rerender } = render(
      <ProviderBrand logoUrl="/providers/example.png" displayName="Example Voice" />,
    );
    expect(screen.getByAltText("Example Voice")).toBeTruthy();

    rerender(<ProviderBrand />);
    expect(screen.queryByAltText("Example Voice")).toBeNull();
  });
});
