import { describe, expect, it } from "vitest";

import { withDispositionCodeOptions, workflowFilterAttributes } from "@/lib/filterAttributes";
import { resolveFilterAttributes, validateFilter } from "@/lib/filters";
import type { ActiveFilter, MultiSelectValue } from "@/types/filters";

const dispositionAttribute = workflowFilterAttributes.find(
  attribute => attribute.id === "dispositionCode"
);

if (!dispositionAttribute) {
  throw new Error("Disposition filter attribute is missing");
}

describe("disposition filters", () => {
  it("resolves URL-loaded filters against the latest catalog options", () => {
    const activeFilter: ActiveFilter = {
      attribute: dispositionAttribute,
      value: { codes: ["user_hangup"] },
      isValid: true,
    };
    const currentAttributes = withDispositionCodeOptions(
      workflowFilterAttributes,
      ["user_hangup", "call_transferred"]
    );
    const currentAttribute = currentAttributes.find(
      attribute => attribute.id === "dispositionCode"
    );
    if (!currentAttribute) {
      throw new Error("Disposition filter attribute is missing");
    }

    const [resolvedFilter] = resolveFilterAttributes(
      [activeFilter],
      currentAttributes
    );

    expect(resolvedFilter.attribute).toBe(currentAttribute);
    expect(resolvedFilter.attribute.config.options).toEqual([
      "user_hangup",
      "call_transferred",
    ]);
  });

  it("allows selecting the complete backend catalog", () => {
    const codes = Array.from({ length: 20 }, (_, index) => `code-${index}`);
    const filter: ActiveFilter = {
      attribute: {
        ...dispositionAttribute,
        config: {
          ...dispositionAttribute.config,
          options: codes,
        },
      },
      value: { codes } satisfies MultiSelectValue,
      isValid: false,
    };

    expect(validateFilter(filter)).toBeNull();
  });
});
