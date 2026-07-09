import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../AuthProvider";
import { StackProviderWrapper } from "../StackProviderWrapper";

type MockStackUser = {
  id: string;
  selectedTeam: null;
  getAuthJson: () => Promise<{ accessToken: string }>;
};

const stackState = vi.hoisted(() => ({
  user: null as MockStackUser | null,
}));

vi.mock("@stackframe/stack", () => {
  class MockStackClientApp {
    useUser = vi.fn(() => stackState.user);

    constructor(
      public options: {
        projectId: string;
        publishableClientKey: string;
      }
    ) {}
  }

  return {
    StackClientApp: MockStackClientApp,
    StackProvider: ({ children }: { children: React.ReactNode }) => children,
    StackTheme: ({ children }: { children: React.ReactNode }) => children,
  };
});

function AuthProbe() {
  const auth = useAuth();
  return (
    <div data-testid="auth-state">
      {auth.loading ? "loading" : auth.user?.id ?? "signed-out"}
    </div>
  );
}

function makeUser(): MockStackUser {
  return {
    id: "stack-user-1",
    selectedTeam: null,
    getAuthJson: async () => ({ accessToken: "token" }),
  };
}

describe("StackProviderWrapper", () => {
  beforeEach(() => {
    stackState.user = makeUser();
  });

  it("updates AuthContext when Stack's reactive user becomes null", () => {
    const props = {
      projectId: "00000000-0000-4000-8000-000000000000",
      publishableClientKey: "publishable-client-key",
    };

    const { rerender } = render(
      <StackProviderWrapper {...props}>
        <AuthProbe />
      </StackProviderWrapper>
    );
    expect(screen.getByTestId("auth-state").textContent).toBe("stack-user-1");

    stackState.user = null;
    rerender(
      <StackProviderWrapper {...props}>
        <AuthProbe />
      </StackProviderWrapper>
    );

    expect(screen.getByTestId("auth-state").textContent).toBe("signed-out");
  });
});
