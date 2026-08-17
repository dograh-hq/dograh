import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProviderBrand } from "./ProviderBrand";

describe("ProviderBrand", () => {
  it("renders the PAPI logo only for the PAPI VoIP provider", () => {
    const { rerender } = render(<ProviderBrand provider="papi_voip" />);
    expect(screen.getByAltText("PAPI")).toBeTruthy();

    rerender(<ProviderBrand provider="twilio" />);
    expect(screen.queryByAltText("PAPI")).toBeNull();
  });
});
