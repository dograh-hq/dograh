import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDefaultModelConfiguration, getModelConnectionCatalog, listNamedModelConfigurations, listProviderConnections } from "@/client/sdk.gen";

import { useModelConnections } from "./useModelConnections";

const state = vi.hoisted(() => ({ auth: { loading: true, user: null as { id: string } | null }, org: { organization_id: 7 as number | null }, organizationLoading: false }));
vi.mock("@/lib/auth", () => ({ useAuth: () => state.auth }));
vi.mock("@/context/OrgConfigContext", () => ({ useOrgConfig: () => ({ orgContext: state.org, loading: state.organizationLoading }) }));
vi.mock("@/client/sdk.gen", () => ({ getDefaultModelConfiguration: vi.fn(), getModelConnectionCatalog: vi.fn(), listNamedModelConfigurations: vi.fn(), listProviderConnections: vi.fn() }));

beforeEach(() => {
    vi.clearAllMocks();
    state.auth = { loading: true, user: null };
    state.org = { organization_id: 7 };
    state.organizationLoading = false;
    vi.mocked(getModelConnectionCatalog).mockResolvedValue({ data: { services: {} } } as never);
    vi.mocked(listProviderConnections).mockResolvedValue({ data: [] } as never);
    vi.mocked(listNamedModelConfigurations).mockResolvedValue({ data: [] } as never);
    vi.mocked(getDefaultModelConfiguration).mockResolvedValue({ data: { model_configuration_uuid: null } } as never);
});

describe("model connection loading", () => {
    it("waits for authenticated user readiness before calling the API", async () => {
        const hook = renderHook(useModelConnections);
        expect(getModelConnectionCatalog).not.toHaveBeenCalled();
        state.auth = { loading: false, user: null };
        hook.rerender();
        expect(getModelConnectionCatalog).not.toHaveBeenCalled();
        state.auth = { loading: false, user: { id: "user" } };
        hook.rerender();
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
        expect(getModelConnectionCatalog).toHaveBeenCalledOnce();
        expect(hook.result.current.catalog).toEqual({ services: {} });
        expect(listProviderConnections).toHaveBeenCalledWith({ query: { include_archived: false } });
        expect(listNamedModelConfigurations).toHaveBeenCalledWith({ query: { include_archived: false } });
    });

    it("requests archived records only for the management list", async () => {
        state.auth = { loading: false, user: { id: "user" } };
        const hook = renderHook(() => useModelConnections({ includeArchived: true }));
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
        expect(listProviderConnections).toHaveBeenCalledWith({ query: { include_archived: true } });
        expect(listNamedModelConfigurations).toHaveBeenCalledWith({ query: { include_archived: true } });
    });

    it("handles generated client HTTP errors and clears loading", async () => {
        state.auth = { loading: false, user: { id: "user" } };
        vi.mocked(listProviderConnections).mockResolvedValue({ error: { detail: [{ msg: "Not authorized", loc: ["header"] }] } } as never);
        const hook = renderHook(useModelConnections);
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
        expect(hook.result.current.error).toContain("Not authorized");
        expect(hook.result.current.catalog).toBeNull();
    });

    it("waits for organization context and bootstrap completion", async () => {
        state.auth = { loading: false, user: { id: "user" } };
        state.org = { organization_id: null };
        state.organizationLoading = true;
        const hook = renderHook(useModelConnections);
        expect(getModelConnectionCatalog).not.toHaveBeenCalled();
        state.org = { organization_id: 7 };
        hook.rerender();
        expect(getModelConnectionCatalog).not.toHaveBeenCalled();
        state.organizationLoading = false;
        hook.rerender();
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
        expect(getModelConnectionCatalog).toHaveBeenCalledOnce();
    });

    it("reloads for another organization", async () => {
        state.auth = { loading: false, user: { id: "user" } };
        const hook = renderHook(useModelConnections);
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
        state.org = { organization_id: 8 };
        hook.rerender();
        await waitFor(() => expect(getModelConnectionCatalog).toHaveBeenCalledTimes(2));
        await waitFor(() => expect(hook.result.current.loading).toBe(false));
    });

    it("keeps loaded configurations available during an organization refresh", async () => {
        state.auth = { loading: false, user: { id: "user" } };
        const configurations = [{ uuid: "sales", is_active: true, revision: 1 }];
        const connections = [{ uuid: "provider", is_active: true }];
        vi.mocked(listNamedModelConfigurations).mockResolvedValue({ data: configurations } as never);
        vi.mocked(listProviderConnections).mockResolvedValue({ data: connections } as never);
        vi.mocked(getDefaultModelConfiguration).mockResolvedValue({ data: { model_configuration_uuid: "sales" } } as never);
        const hook = renderHook(useModelConnections);
        await waitFor(() => expect(hook.result.current.loading).toBe(false));

        state.organizationLoading = true;
        hook.rerender();
        expect(hook.result.current.catalog).toEqual({ services: {} });
        expect(hook.result.current.connections).toEqual(connections);
        expect(hook.result.current.configurations).toEqual(configurations);
        expect(hook.result.current.defaultUuid).toBe("sales");
        expect(getModelConnectionCatalog).toHaveBeenCalledOnce();

        const updatedConfigurations = [{ ...configurations[0], revision: 2 }];
        const response = Promise.withResolvers<Awaited<ReturnType<typeof listNamedModelConfigurations<false>>>>();
        vi.mocked(listNamedModelConfigurations).mockReturnValueOnce(response.promise);
        state.organizationLoading = false;
        hook.rerender();
        expect(hook.result.current.loading).toBe(true);
        expect(hook.result.current.configurations).toEqual(configurations);
        await act(async () => response.resolve({ data: updatedConfigurations } as never));
        expect(hook.result.current.configurations).toEqual(updatedConfigurations);
        expect(hook.result.current.loading).toBe(false);
    });

    it.each(["organization", "user"])("clears loaded data when the %s changes while organization context is loading", async scope => {
        state.auth = { loading: false, user: { id: "user" } };
        vi.mocked(listNamedModelConfigurations).mockResolvedValue({ data: [{ uuid: "sales" }] } as never);
        const hook = renderHook(useModelConnections);
        await waitFor(() => expect(hook.result.current.loading).toBe(false));

        state.organizationLoading = true;
        if (scope === "organization") state.org = { organization_id: 8 };
        else state.auth = { loading: false, user: null };
        hook.rerender();
        expect(hook.result.current.catalog).toBeNull();
        expect(hook.result.current.configurations).toEqual([]);
        expect(hook.result.current.loading).toBe(true);
    });
});
