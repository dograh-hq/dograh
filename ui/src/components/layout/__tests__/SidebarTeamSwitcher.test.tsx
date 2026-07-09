/**
 * Regression tests for Sentry issue JAVASCRIPT-NEXTJS-2Z:
 * "Rendered fewer hooks than expected" (React error #300).
 *
 * Stack's TeamSwitcher Inner component calls `user?.useTeams()` — a hook behind
 * optional chaining. When Stack's useUser() flips from a user object to null
 * mid-session (token expiry, sign-out synced from another tab), the re-render
 * calls fewer hooks than the previous one and React throws.
 *
 * These tests fake useUser()/useStackApp() but render the REAL
 * SelectedTeamSwitcher component code from @stackframe/stack, so the first test
 * pins the upstream bug: if it starts failing after a @stackframe/stack
 * upgrade, the bug is fixed upstream and the guard in SidebarTeamSwitcher can
 * be removed.
 *
 * Mocking note: vi.mock("@stackframe/stack") fully replaces the package entry.
 * The real component is imported below via its dist file path — its internal
 * `import ... from "../index.js"` resolves to the same entry module and
 * therefore receives the mocked hooks.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "@/lib/auth";
import {
  AuthContext,
  type AuthContextType,
} from "@/lib/auth/providers/AuthProvider";

import { SelectedTeamSwitcher as RealSelectedTeamSwitcher } from "../../../../node_modules/@stackframe/stack/dist/esm/components/selected-team-switcher.js";
import { TranslationContext } from "../../../../node_modules/@stackframe/stack/dist/esm/providers/translation-provider-client.js";
import { SidebarTeamSwitcher } from "../SidebarTeamSwitcher";

const reloadAppMock = vi.hoisted(() => vi.fn());
const toastErrorMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/browserReload", () => ({
  reloadApp: reloadAppMock,
}));

vi.mock("sonner", () => ({
  toast: {
    error: toastErrorMock,
  },
}));

// Controls what the faked Stack useUser() returns; tests flip this to null to
// simulate the session dying (token expiry / sign-out from another tab).
const stackState: { user: ReturnType<typeof makeStackUser> | null } = {
  user: null,
};

const TEAM_ONE = { id: "team-1", displayName: "Team One", profileImageUrl: null };
const TEAM_TWO = { id: "team-2", displayName: "Team Two", profileImageUrl: null };
const makeTeams = () => [TEAM_ONE, TEAM_TWO];
type MockTeam = typeof TEAM_ONE | typeof TEAM_TWO;

function makeStackUser() {
  return {
    id: "user-1",
    selectedTeam: TEAM_ONE as MockTeam | null,
    // The real CurrentUser.useTeams() consumes React hooks internally (Stack's
    // async cache). Mirror that so the hook count matches real behavior — the
    // crash only reproduces if useTeams actually registers hooks. Use useMemo
    // (the same hook kind as the next hook in TeamSwitcher's Inner) so the
    // user→null transition completes the render with leftover hooks and React
    // throws the production error (#300) instead of a hook-slot TypeError.
    useTeams: () => React.useMemo(() => makeTeams(), []),
    setSelectedTeam: vi.fn(async (team: MockTeam | null) => {
      if (stackState.user) {
        stackState.user.selectedTeam = team;
      }
    }),
  };
}

const mockStackApp = {
  useNavigate: () => () => {},
  useProject: () => ({ config: { clientTeamCreationEnabled: false } }),
  urls: { accountSettings: "/account-settings" },
};

// Full replacement of the package entry. The factory only creates closures, so
// it is safe to execute during import hoisting; the consts above are read at
// render time. The raw upstream test imports the real SelectedTeamSwitcher by
// dist path; this package export is a small controlled stand-in for
// SidebarTeamSwitcher's lazy import.
vi.mock("@stackframe/stack", () => ({
  useStackApp: () => mockStackApp,
  useUser: () => stackState.user,
  SelectedTeamSwitcher: (props: {
    selectedTeam?: MockTeam | null;
    onChange?: (team: MockTeam) => void;
    triggerClassName?: string;
  }) => (
    <button
      aria-controls="team-options"
      aria-expanded="false"
      className={props.triggerClassName}
      role="combobox"
      onClick={() => props.onChange?.(TEAM_TWO)}
    >
      {props.selectedTeam?.displayName ?? "Select team"}
    </button>
  ),
}));

class CatchBoundary extends React.Component<
  { onError: (error: Error) => void; children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    this.props.onError(error);
  }
  render() {
    return this.state.failed ? (
      <div data-testid="crashed" />
    ) : (
      this.props.children
    );
  }
}

const translationValue = {
  quetzalKeys: new Map<string, string>(),
  quetzalLocale: new Map<string, string>(),
};

function withProviders(
  children: React.ReactNode,
  authValue?: AuthContextType
) {
  const inner = authValue ? (
    <AuthContext.Provider value={authValue}>{children}</AuthContext.Provider>
  ) : (
    children
  );
  return (
    <TranslationContext.Provider value={translationValue}>
      {inner}
    </TranslationContext.Provider>
  );
}

function makeAuthValue(user: AuthUser | null): AuthContextType {
  return {
    user,
    isAuthenticated: !!user,
    loading: false,
    getAccessToken: async () => "token",
    redirectToLogin: () => {},
    logout: async () => {},
    provider: "stack",
    getSelectedTeam: () => null,
  };
}

describe("SidebarTeamSwitcher", () => {
  beforeEach(() => {
    stackState.user = makeStackUser();
    reloadAppMock.mockClear();
    toastErrorMock.mockClear();
    // React logs boundary-caught render errors; keep test output readable.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("pins the upstream Stack bug: raw SelectedTeamSwitcher crashes with React #300 when the user flips to null", async () => {
    const errors: Error[] = [];

    const { rerender } = render(
      withProviders(
        <CatchBoundary onError={(e) => errors.push(e)}>
          <RealSelectedTeamSwitcher />
        </CatchBoundary>
      )
    );
    expect(await screen.findByRole("combobox")).toBeTruthy();

    stackState.user = null;
    rerender(
      withProviders(
        <CatchBoundary onError={(e) => errors.push(e)}>
          <RealSelectedTeamSwitcher />
        </CatchBoundary>
      )
    );

    expect(
      errors.some((e) => /Rendered fewer hooks than expected/.test(e.message)),
      `expected React #300, got: ${errors.map((e) => e.message).join("; ") || "no error"}`
    ).toBe(true);
  });

  it("renders the switcher for an authenticated stack user", async () => {
    render(
      withProviders(
        <SidebarTeamSwitcher />,
        makeAuthValue(stackState.user as unknown as AuthUser)
      )
    );
    expect((await screen.findByRole("combobox")).textContent).toBe("Team One");
  });

  it("blocks the app and reloads after Stack persists the selected team", async () => {
    let finishSwitch!: () => void;
    stackState.user!.setSelectedTeam = vi.fn(async (team: MockTeam | null) => {
      await new Promise<void>((resolve) => {
        finishSwitch = () => {
          if (stackState.user) {
            stackState.user.selectedTeam = team;
          }
          resolve();
        };
      });
    });

    render(
      withProviders(
        <SidebarTeamSwitcher />,
        makeAuthValue(stackState.user as unknown as AuthUser)
      )
    );

    const switcher = await screen.findByRole("combobox");
    fireEvent.click(switcher);

    expect((await screen.findByRole("status")).textContent).toContain("Switching teams...");
    expect(reloadAppMock).not.toHaveBeenCalled();
    expect(stackState.user?.setSelectedTeam).toHaveBeenCalledWith(TEAM_TWO);

    await act(async () => {
      finishSwitch();
    });

    await waitFor(() => {
      expect(reloadAppMock).toHaveBeenCalled();
    });
  });

  it("shows feedback and does not reload when Stack fails to persist the selected team", async () => {
    stackState.user!.setSelectedTeam = vi.fn(async () => {
      throw new Error("Stack write failed");
    });

    render(
      withProviders(
        <SidebarTeamSwitcher />,
        makeAuthValue(stackState.user as unknown as AuthUser)
      )
    );

    const switcher = await screen.findByRole("combobox");
    fireEvent.click(switcher);

    await waitFor(() => {
      expect(toastErrorMock).toHaveBeenCalledWith(
        "Could not switch teams. Please try again."
      );
    });
    expect(screen.queryByRole("status")).toBeNull();
    expect(reloadAppMock).not.toHaveBeenCalled();
  });

  it("unmounts the switcher instead of crashing when the session dies (user flips to null)", async () => {
    const errors: Error[] = [];

    const { rerender } = render(
      withProviders(
        <CatchBoundary onError={(e) => errors.push(e)}>
          <SidebarTeamSwitcher />
        </CatchBoundary>,
        makeAuthValue(stackState.user as unknown as AuthUser)
      )
    );
    expect(await screen.findByRole("combobox")).toBeTruthy();

    // In production both changes arrive in the same update: Stack's internal
    // store empties, which updates useUser() consumers AND our AuthContext
    // (StackAuthContextProvider derives its value from the same store).
    stackState.user = null;
    rerender(
      withProviders(
        <CatchBoundary onError={(e) => errors.push(e)}>
          <SidebarTeamSwitcher />
        </CatchBoundary>,
        makeAuthValue(null)
      )
    );

    expect(errors.map((e) => e.message)).toEqual([]);
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
